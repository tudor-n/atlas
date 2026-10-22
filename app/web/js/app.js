// App shell: DOM helper, hash router, view registry, banner, capture box and confirm-card tray.
import * as api from './api.js';
import { el, clear } from './dom.js';
import { mountCapture } from './components/capture.js';
import { confirmCard } from './components/confirm-card.js';
import { driveText } from './drive.js';

// Re-exported so older imports keep working; new code imports from ./dom.js.
export { el, clear, fmtWhen } from './dom.js';

// -- Shared state ---------------------------------------------------------------------------
export const state = { setup_done: true, settings: {}, backup: null, log_error: null, startup_error: null,
  drive: null, warnings: [] };

// Reduced mode: the root isn't usable and there is no brain to show (the reduced app sends
// setup_done null). Only the drive banner and the capture box render; setup is never offered.
const reduced = () => !!state.drive && !state.setup_done;

// Returns true when the mode changed (the hub came back in full, or went reduced).
export async function refreshState() {
  const was = reduced();
  try {
    Object.assign(state, await api.get('/api/state'));
    renderBanner();
  } catch (e) {
    renderBanner(e.message);
  }
  return was !== reduced();
}

function renderBanner(connectionError) {
  const box = document.getElementById('banner');
  clear(box);
  if (connectionError) {
    box.appendChild(el('div', { class: 'banner', role: 'alert' }, connectionError));
  }
  if (state.drive) {
    box.appendChild(el('div', { class: 'banner', role: 'status' }, driveText(state.drive)));
  }
  if (state.log_error) {
    box.appendChild(el('div', { class: 'banner', role: 'alert' },
      `Your raw log file has a problem: ${state.log_error}. Nothing was changed; fix or ask for help.`));
  }
  if (Array.isArray(state.startup_error) && state.startup_error.length) {
    box.appendChild(el('div', { class: 'banner', role: 'alert' },
      'BrainHub started, but noticed problems. Capture still works.',
      el('ul', {}, state.startup_error.map((m) => el('li', {}, m)))));
  }
  for (const w of Array.isArray(state.warnings) ? state.warnings : []) {
    box.appendChild(el('p', { class: 'quiet-note' }, w));  // quiet: one line, never a popup
  }
}

// -- View registry --------------------------------------------------------------------------
// name -> async loader returning a module with `render(container, params)`. render may return a
// cleanup function (called when the user leaves the view). A new view is one module + one line here.
export const views = {
  today: () => import('./views/today.js'),
  ask: () => import('./views/ask.js'),
  journal: () => import('./views/journal.js'),
  project: () => import('./views/browse.js').then((m) => ({ render: m.renderProject })),
  people: () => import('./views/browse.js').then((m) => ({ render: m.renderPeople })),
  deadlines: () => import('./views/browse.js').then((m) => ({ render: m.renderDeadlines })),
  agents: () => import('./views/agents.js'),
  usage: () => import('./views/usage.js'),
  settings: () => import('./views/settings.js'),
  setup: () => import('./views/setup.js'),
};

const NAV = [['today', 'Today'], ['ask', 'Ask'], ['journal', 'Journal'], ['people', 'People'],
  ['deadlines', 'Deadlines'], ['agents', 'Agents'], ['usage', 'Usage'], ['settings', 'Settings']];

// -- Router ---------------------------------------------------------------------------------
let cleanup = null;
let routeToken = 0;

// "#/journal?entry=e-1" -> name 'journal', params [], query URLSearchParams(entry=e-1)
function parseHash() {
  const [path, qs = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  return { name: parts[0] || 'today', params: parts.slice(1), query: new URLSearchParams(qs) };
}

function renderNav(name) {
  const nav = clear(document.getElementById('nav'));
  for (const [key, label] of NAV) {
    nav.appendChild(el('a', { href: `#/${key}`, 'aria-current': key === name ? 'page' : null }, label));
  }
}

function runCleanup() {
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
}

// Reduced mode: no nav and no view, so nothing calls an API that can't answer.
function renderReduced() {
  runCleanup();
  clear(document.getElementById('nav'));
  clear(document.getElementById('view'));
}

async function route() {
  const token = ++routeToken;
  const { name, params, query } = parseHash();
  await refreshState();
  if (token !== routeToken) return;
  if (reduced()) {
    renderReduced();
    return;
  }
  if (!state.setup_done && name !== 'setup') {
    location.hash = '#/setup';
    return;
  }
  const loader = Object.hasOwn(views, name) ? views[name] : null;
  if (!loader) {
    location.hash = '#/today';
    return;
  }
  runCleanup();
  renderNav(name);
  const container = clear(document.getElementById('view'));
  try {
    const mod = await loader();
    if (token !== routeToken) return;
    const result = await mod.render(container, params, query);
    if (token !== routeToken) { if (typeof result === 'function') result(); return; }
    cleanup = typeof result === 'function' ? result : null;
  } catch (e) {
    console.error(e);
    clear(container).appendChild(e.drive  // the drive went away: its banner, not an error
      ? el('p', { class: 'banner', role: 'status' }, driveText(e.drive))
      : el('p', { class: 'error', role: 'alert' }, `Couldn't load this page: ${e.message}`));
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
    onSaved: (entryId, text, held) => {
      mine.set(entryId, text);  // a held capture's card comes once it is filed, after the drive is back
      if (held) refreshState();  // the banner's held count
    },
  });
  startTray(capture);
  // A reconnect may follow a restart (exit 75 once the drive is back): re-route if the mode changed.
  api.onEvent('ws.open', async () => { if (await refreshState()) route(); });
  window.addEventListener('hashchange', route);
  await route();
}

boot();
