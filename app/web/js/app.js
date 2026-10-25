// App shell (phase 4.5 design §3): hash router and view registry, the top bar, the banner, the capture
// bar and the confirm-card tray, in index.html's full-height grid. Routing comes from nav-model.js.
import * as api from './api.js';
import { el, clear } from './dom.js';
import { mountCapture } from './components/capture.js';
import { confirmCard } from './components/confirm-card.js';
import { mountTopbar } from './components/topbar.js';
import { notice } from './components/ui.js';
import { installPress } from './press.js';
import { parseHash, tabByKey } from './nav-model.js';
import { driveText, heldText, readonlyReason } from './drive.js';

// Re-exported so older imports keep working; new code imports from ./dom.js.
export { el, clear, fmtWhen } from './dom.js';

// -- Shared state ---------------------------------------------------------------------------
export const state = { setup_done: true, settings: {}, backup: null, log_error: null, startup_error: null,
  drive: null, warnings: [] };
let offline = false;  // the last /api/state couldn't reach the hub

// Reduced mode: the root isn't usable and there is no brain to show (the reduced app sends
// setup_done null). Only the drive notice and the capture bar render; setup is never offered.
const reduced = () => !!state.drive && !state.setup_done;
// 'full', 'setup' (#/setup is forced until it is done) or 'reduced'.
const mode = () => (reduced() ? 'reduced' : state.setup_done ? 'full' : 'setup');

const top = mountTopbar({ shell: () => ({ mode: mode(), drive: state.drive, offline }) });

// Returns true when the mode changed (the hub came back in full, or went reduced).
export async function refreshState() {
  const was = reduced();
  try {
    Object.assign(state, await api.get('/api/state'));
    offline = false;
    renderBanner();
  } catch (e) {
    offline = !e.status;
    renderBanner(e.message);
  }
  top.render();
  return was !== reduced();
}

function renderBanner(connectionError) {
  const box = clear(document.getElementById('banner'));
  if (connectionError) {
    box.appendChild(el('div', { class: 'bn err', role: 'alert' }, connectionError));
  }
  if (state.drive && !reduced()) {  // reduced mode says it in the view's notice instead
    box.appendChild(el('div', { class: 'bn', role: 'status' }, el('span', { class: 'sq' }), driveText(state.drive)));
  }
  if (state.log_error) {
    box.appendChild(notice(`Your raw log file has a problem: ${state.log_error}. Nothing was changed; fix or ask for help.`,
      { title: 'RAW LOG', role: 'alert' }));
  }
  if (Array.isArray(state.startup_error) && state.startup_error.length) {
    const n = notice('ATLAS started, but noticed problems. Capture still works.', { title: 'STARTUP', role: 'alert' });
    n.appendChild(el('ul', {}, state.startup_error.map((m) => el('li', {}, m))));
    box.appendChild(n);
  }
  for (const w of Array.isArray(state.warnings) ? state.warnings : []) {
    box.appendChild(el('p', { class: 'bn q' }, w));  // quiet: one line, never a popup
  }
}

// -- View registry --------------------------------------------------------------------------
// name -> async loader returning a module with `render(container, params, query)`. render may return
// a cleanup function (called when the user leaves the view). A new view is one module + one line here.
export const views = {
  today: () => import('./views/today.js'),
  atlas: () => import('./views/atlas.js'),
  ask: () => import('./views/atlas.js'),  // Ask became ATLAS: the old links still land
  journal: () => import('./views/journal.js'),
  tablet: () => import('./views/tablet.js'),
  browse: browseView(null),
  project: browseView('project'),
  people: browseView('people'),
  deadlines: browseView('deadlines'),
  agents: () => import('./views/agents.js'),
  usage: () => import('./views/agents.js').then((m) => ({ render: (c, p, q) => m.render(c, p, q, { focus: 'usage' }) })),
  settings: () => import('./views/settings.js'),
  setup: () => import('./views/setup.js'),
};

// Browse (people, deadlines, project/<slug>, and `#/browse` for the last-used one) is one view with a
// rail, views/browse-shell.js; it remembers the last-used page itself. `page` is put in front of the
// route's params, so the shell sees ['people'], ['project', '<slug>'] or [] for `#/browse`.
function browseView(page) {
  return () => import('./views/browse-shell.js').then((m) => ({
    render: (c, p, q) => m.render(c, page ? [page, ...p] : p, q),
  }));
}

// -- Router ---------------------------------------------------------------------------------
let cleanup = null;
let routeToken = 0;
const viewBox = () => document.getElementById('view');

function runCleanup() {
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
}

// Reduced mode: no tabs and no view, so nothing calls an API that can't answer. One notice says why,
// with the held count; the capture bar stays.
function renderReduced() {
  runCleanup();
  top.setRoute(null);
  oneInput(false);
  const d = state.drive || {};
  const n = notice(d.message || "ATLAS's data folder can't be used right now.", { title: 'REDUCED MODE' });
  n.append(
    el('p', { class: 'held' }, heldText(d.held) || 'Nothing held on this OS.'),
    el('p', { class: 'd' }, 'Capture still works: what you save is held on this OS until the drive is back.'),
  );
  clear(viewBox()).appendChild(el('div', { class: 'vw reduced' }, n));
}

async function route() {
  const token = ++routeToken;
  const r = parseHash(location.hash);
  await refreshState();
  if (token !== routeToken) return;
  if (reduced()) {
    renderReduced();
    return;
  }
  if (!state.setup_done && r.name !== 'setup') {
    location.hash = '#/setup';
    return;
  }
  const { name, params } = r;
  const loader = Object.hasOwn(views, name) ? views[name] : null;
  if (!loader) {
    location.hash = '#/today';
    return;
  }
  runCleanup();
  top.setRoute(r.name);
  oneInput(name === 'atlas' || name === 'ask');
  const container = clear(viewBox());
  container.scrollTop = 0;
  try {
    const mod = await loader();
    if (token !== routeToken) return;
    const result = await mod.render(container, params, r.query);
    if (token !== routeToken) { if (typeof result === 'function') result(); return; }
    cleanup = typeof result === 'function' ? result : null;
  } catch (e) {
    console.error(e);
    oneInput(false);  // the view didn't load: the capture bar is the way in
    clear(container).appendChild(e.drive  // the drive went away: its notice, not an error
      ? el('div', { class: 'vw reduced' }, notice(driveText(e.drive), { title: 'DRIVE' }))
      : el('p', { class: 'bn err', role: 'alert' }, `Couldn't load this page: ${e.message}`));
  }
}

// The ATLAS screen has its own write-or-ask field: the capture bar steps aside there, so one input
// shows at a time (voice-input decisions VI1). It stays everywhere else, and in reduced mode.
function oneInput(on) {
  document.getElementById('app').classList.toggle('one-input', on);
}

// -- Keyboard: Alt+1…6 the tabs, Alt+0 Settings, Alt+C capture -----------------------------------
// Only Alt combinations, so typing in a field is never interrupted. AltGr (Ctrl+Alt on Windows) is
// left alone: it types ș ț ă î â.
function onKey(ev, capture) {
  if (!ev.altKey || ev.ctrlKey || ev.metaKey || ev.repeat) return;
  const isC = ev.code ? ev.code === 'KeyC' : /^c$/i.test(ev.key);
  if (isC) {
    ev.preventDefault();
    const own = document.getElementById('atl-q');  // on the ATLAS screen, its own field
    if (own) own.focus(); else capture.focus();
    return;
  }
  if (mode() !== 'full') return;
  const digit = /^Digit([0-9])$/.exec(ev.code || '');
  const id = tabByKey(digit ? digit[1] : ev.key);
  if (!id) return;
  ev.preventDefault();
  location.hash = `#/${id}`;
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
    blocked: () => readonlyReason(state.drive),
    onClose: () => { card.destroy(); card.remove(); trayCards.delete(entryId); },
  });
  trayCards.set(entryId, card);
  tray.prepend(card);
}

function startTray(capture) {
  // The ATLAS screen hands its note over when it moves on (a new note, or the owner leaves).
  document.addEventListener('atlas:tray', (ev) => {
    const { entryId, text } = ev.detail || {};
    if (!entryId) return;
    mine.set(entryId, text);
    showTrayCard(entryId);
  });
  api.onEvent('filing.done', ({ data }) => {
    if (!mine.has(data.entry_id)) return;
    capture.setStatus('Filed');
    showTrayCard(data.entry_id);
  });
  api.onEvent('filing.failed', ({ data }) => {
    if (!mine.has(data.entry_id)) return;
    capture.setStatus('Filing failed · see the card above');
    showTrayCard(data.entry_id);
  });
}

// -- Boot -----------------------------------------------------------------------------------
async function boot() {
  installPress(document);
  const capture = mountCapture(document.getElementById('capture'), {
    onSaved: (entryId, text, held) => {
      mine.set(entryId, text);  // a held capture's card comes once it is filed, after the drive is back
      if (held) refreshState().then(() => { if (reduced()) renderReduced(); });  // the held count
      else showTrayCard(entryId);  // at once: it shows the filing's live progress, then the review
    },
    // A question typed in the bar: the ATLAS screen answers it (voice-input decisions VI1).
    // Without the ATLAS screen (reduced mode, setup) the bar keeps the text and shows why.
    onAsk: (text) => {
      if (mode() === 'reduced') return "Can't ask while ATLAS's data folder is unavailable. Your text is still here; press NOTE to save it.";
      if (mode() === 'setup') return 'Finish setup first, then ask. Your text is still here.';
      location.hash = `#/atlas?q=${encodeURIComponent(text)}`;
      return '';
    },
  });
  startTray(capture);
  document.addEventListener('atlas:draft', (ev) => {
    const { text, why } = ev.detail || {};
    if (typeof text === 'string' && text.trim()) capture.setDraft(text, why);
  });
  document.addEventListener('keydown', (ev) => onKey(ev, capture));
  // A reconnect may follow a restart (exit 75 once the drive is back): re-route if the mode changed.
  api.onEvent('ws.open', async () => { if (await refreshState()) route(); });
  window.addEventListener('hashchange', route);
  await route();
}

boot();
