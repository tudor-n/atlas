// App shell: DOM helper, hash router, view registry, banner, capture box and confirm-card tray.
import * as api from './api.js';
import { el, clear } from './dom.js';
import { mountCapture } from './components/capture.js';
import { confirmCard } from './components/confirm-card.js';

// Re-exported so older imports keep working; new code imports from ./dom.js.
export { el, clear, fmtWhen } from './dom.js';

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
  ask: () => import('./views/ask.js'),
  journal: () => import('./views/journal.js'),
  project: () => import('./views/browse.js').then((m) => ({ render: m.renderProject })),
  people: () => import('./views/browse.js').then((m) => ({ render: m.renderPeople })),
  deadlines: () => import('./views/browse.js').then((m) => ({ render: m.renderDeadlines })),
  settings: comingSoon('Settings'),
  setup: comingSoon('Setup'),
};

const NAV = [['today', 'Today'], ['ask', 'Ask'], ['journal', 'Journal'], ['people', 'People'],
  ['deadlines', 'Deadlines'], ['settings', 'Settings']];

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

async function route() {
  const token = ++routeToken;
  const { name, params, query } = parseHash();
  await refreshState();
  if (token !== routeToken) return;
  if (!state.setup_done && name !== 'setup') {
    location.hash = '#/setup';
    return;
  }
  const loader = Object.hasOwn(views, name) ? views[name] : null;
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
    const result = await mod.render(container, params, query);
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
  api.onEvent('ws.open', () => refreshState());
  window.addEventListener('hashchange', route);
  await route();
}

boot();
