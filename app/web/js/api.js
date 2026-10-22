// Thin client for the BrainHub server. Every JSON body goes out as application/json (the server returns 415 otherwise).

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (method !== 'GET') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body === undefined ? {} : body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (_) {
    throw new Error("Can't reach BrainHub. Is it running?");
  }
  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch (_) { data = null; }
  }
  if (!res.ok) {
    const e = new Error((data && data.error) || `Request failed (${res.status})`);
    e.status = res.status;
    e.drive = (data && data.drive) || null;  // 503 while the root isn't usable: the banner to show
    throw e;
  }
  return data;
}

export const get = (path) => request('GET', path);
export const post = (path, body) => request('POST', path, body);
export const patch = (path, body) => request('PATCH', path, body);
export const put = (path, body) => request('PUT', path, body);
export const del = (path) => request('DELETE', path);

// -- WebSocket: one shared, auto-reconnecting socket ----------------------------------------
// Listeners receive the whole message {type, data}. 'ws.open' fires on every reconnect (not the
// first connect), so views can refetch what they missed while the socket was down.
const listeners = new Map();
let started = false;
let wasConnected = false;

function dispatch(type, msg) {
  for (const fn of [...(listeners.get(type) || [])]) {
    try { fn(msg); } catch (e) { console.error(e); }
  }
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${proto}://${location.host}/ws`);
  socket.onopen = () => {
    if (wasConnected) dispatch('ws.open', { type: 'ws.open', data: {} });
    wasConnected = true;
  };
  socket.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    if (msg && typeof msg.type === 'string') dispatch(msg.type, msg);
  };
  socket.onclose = () => setTimeout(connect, 2000);
  socket.onerror = () => { try { socket.close(); } catch (_) { /* already closing */ } };
}

// Returns an unsubscribe function.
export function onEvent(type, fn) {
  if (!started) { started = true; connect(); }
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
  return () => listeners.get(type).delete(fn);
}

// -- Server-sent events (recall). handlers: {hits, delta, error, done}, each gets the parsed data.
// Returns a close() function.
export function sse(path, handlers) {
  const es = new EventSource(path);
  for (const name of ['hits', 'delta', 'error', 'done']) {
    es.addEventListener(name, (ev) => {
      if (ev.data === undefined) {  // the browser's own connection error, not a server 'error' event
        if (name === 'error') { es.close(); if (handlers.error) handlers.error('Connection lost'); }
        return;
      }
      let data;
      try { data = JSON.parse(ev.data); } catch (_) { data = ev.data; }
      if (handlers[name]) handlers[name](data);
      if (name === 'done') es.close();
    });
  }
  return () => es.close();
}
