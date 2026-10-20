// App shell: DOM helper, hash router, view registry, banner, capture box and confirm-card tray.
import * as api from './api.js';
import { mountCapture } from './components/capture.js';
import { confirmCard } from './components/confirm-card.js';

// -- DOM helper ---------------------------------------------------------------------------
// el(tag, attrs, ...children). Text only ever enters the page as text nodes (never as HTML).
// attrs: `class`, `on<event>` functions, boolean/property attrs (value, checked, disabled, ...),
// anything else becomes an attribute. Children may be strings, numbers, nodes or arrays of those.
const PROPS = new Set(['value', 'checked', 'disabled', 'hidden', 'open', 'selected', 'required']);

export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'innerHTML' || k === 'outerHTML' || k === 'srcdoc') throw new Error(`el(): ${k} is not allowed`);
    if (k.startsWith('on')) {
      if (typeof v !== 'function') throw new Error(`el(): ${k} must be a function`);
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === 'class') {
      node.className = v;
    } else if (PROPS.has(k)) {
      node[k] = v;
    } else {
      node.setAttribute(k, v === true ? '' : String(v));
    }
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(node, c);
    else if (c instanceof Node) node.appendChild(c);
    else node.appendChild(document.createTextNode(String(c)));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

// "2026-10-25T14:00" -> "2026-10-25 14:00"
export const fmtWhen = (iso) => (iso ? String(iso).replace('T', ' ') : '');

// -- Shared state ---------------------------------------------------------------------------
export const state = { setup_done: true, settings: {}, backup: null, log_error: null };

export async function refreshState() {
  try {
    Object.assign(state, await api.get('/api/state'));
    renderBanner();
  } catch (e) {
    renderBanner(e.message);
  }
}

function renderBanner(connectionError) {
  const box = document.getElementById('banner');
  clear(box);
  if (connectionError) {
    box.appendChild(el('div', { class: 'banner', role: 'alert' }, connectionError));
  }
  if (state.log_error) {
    box.appendChild(el('div', { class: 'banner', role: 'alert' },
      `Your raw log file has a problem: ${state.log_error}. Nothing was changed; fix or ask for help.`));
  }
}

// -- View registry --------------------------------------------------------------------------
// name -> async loader returning a module with `render(container, params)`. render may return a
// cleanup function (called when the user leaves the view). A new view is one module + one line here.
function comingSoon(title) {
  return async () => ({
    render(container) {
      container.appendChild(el('h1', {}, title));
      container.appendChild(el('p', { class: 'empty' }, 'Coming soon.'));
    },
  });
}

export const views = {
  today: () => import('./views/today.js'),
  ask: comingSoon('Ask'),
  journal: comingSoon('Journal'),
  project: comingSoon('Project'),
  people: comingSoon('People'),
  deadlines: comingSoon('Deadlines'),
  settings: comingSoon('Settings'),
  setup: comingSoon('Setup'),
};

const NAV = [['today', 'Today'], ['ask', 'Ask'], ['journal', 'Journal'], ['people', 'People'],
  ['deadlines', 'Deadlines'], ['settings', 'Settings']];

// -- Router ---------------------------------------------------------------------------------
let cleanup = null;
let routeToken = 0;

function parseHash() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  return { name: parts[0] || 'today', params: parts.slice(1) };
}

function renderNav(name) {
  const nav = clear(document.getElementById('nav'));
  for (const [key, label] of NAV) {
    nav.appendChild(el('a', { href: `#/${key}`, 'aria-current': key === name ? 'page' : null }, label));
  }
}

async function route() {
  const token = ++routeToken;
  const { name, params } = parseHash();
  await refreshState();
  if (token !== routeToken) return;
  if (!state.setup_done && name !== 'setup') {
    location.hash = '#/setup';
    return;
  }
  const loader = views[name];
  if (!loader) {
    location.hash = '#/today';
    return;
  }
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
  renderNav(name);
  const container = clear(document.getElementById('view'));
  try {
    const mod = await loader();
    if (token !== routeToken) return;
    const result = await mod.render(container, params);
    if (token !== routeToken) { if (typeof result === 'function') result(); return; }
    cleanup = typeof result === 'function' ? result : null;
  } catch (e) {
    console.error(e);
    clear(container).appendChild(el('p', { class: 'error', role: 'alert' }, `Couldn't load this page: ${e.message}`));
  }
}

// -- Confirm-card tray (cards for entries captured in this tab) ---------------------------------
const mine = new Map();   // entry id -> text captured in this tab
const trayCards = new Map();  // entry id -> card element

function showTrayCard(entryId) {
  const tray = document.getElementById('tray');
  const existing = trayCards.get(entryId);
  if (existing) { existing.reload(); return; }
  const card = confirmCard(entryId, {
    entryText: mine.get(entryId),
    onClose: () => { card.destroy(); card.remove(); trayCards.delete(entryId); },
  });
  trayCards.set(entryId, card);
  tray.prepend(card);
}

function startTray(capture) {
  api.onEvent('filing.done', ({ data }) => {
    if (!mine.has(data.entry_id)) return;
    capture.setStatus('Filed');
    showTrayCard(data.entry_id);
  });
  api.onEvent('filing.failed', ({ data }) => {
    if (!mine.has(data.entry_id)) return;
    capture.setStatus('Filing failed. See below.');
    showTrayCard(data.entry_id);
  });
}

// -- Boot -----------------------------------------------------------------------------------
async function boot() {
  const capture = mountCapture(document.getElementById('capture'), {
    onSaved: (entryId, text) => { mine.set(entryId, text); },
  });
  startTray(capture);
  window.addEventListener('hashchange', route);
  await route();
}

boot();
