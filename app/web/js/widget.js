// Desktop widget page: collapsed synopsis bar, expanded capture + tiles + popups (design §7.1).
import { get, post, onEvent } from './api.js';
import { el, clear } from './dom.js';
import { TILE_LABEL, TILE_VIEW, tileSummary, popupRows, synopsisText, loadDraft, saveDraft } from './widget-model.js';

const WIDTH = 520;
const OFFLINE = 'BrainHub offline · retrying';
const REFRESH_EVENTS = ['items.changed', 'agents.changed', 'usage.changed', 'filing.done', 'filing.failed', 'map.changed'];

let data = null;
let offline = false;
let expanded = false;
let openTile = null;
let debounce = null;
let savedTimer = null;
let saving = false;
let popupError = '';
let loading = false;
let again = false;

const root = document.getElementById('w');

// localStorage may be missing or throw on access (blocked storage): treat that as "no draft".
function draftStore() {
  try { return window.localStorage; } catch (_) { return null; }
}

// -- Tauri bridge: no-op in a plain browser ---------------------------------------------------
function fitWindow() {
  if (!window.__TAURI__) return;
  try {
    const panel = root.firstElementChild;
    if (!panel) return;
    const h = Math.ceil(panel.getBoundingClientRect().height);
    window.__TAURI__.window.getCurrentWindow().setSize(new window.__TAURI__.dpi.LogicalSize(WIDTH, h));
  } catch (_) { /* not running in the desktop shell */ }
}

// -- static shell, built once so the capture field keeps its text and focus ------------------
const grip = el('span', { class: 'grip', 'aria-hidden': 'true', 'data-tauri-drag-region': true }, '⋮⋮');
const dot = el('span', { class: 'dot', title: 'Needs your attention', hidden: true, 'data-tauri-drag-region': true });
const synopsis = el('span', { class: 'synopsis', 'data-tauri-drag-region': true }, 'Loading…');
const expandBtn = el('button', { class: 'expand', type: 'button', 'aria-label': 'Expand', 'aria-expanded': 'false', onclick: () => setExpanded(!expanded) }, '▾');
const bar = el('div', { class: 'bar', 'data-tauri-drag-region': true }, grip, dot, synopsis, expandBtn);

const capture = el('input', { class: 'capture', type: 'text', placeholder: 'Write something down, Enter to save', 'aria-label': 'Capture', autocomplete: 'off', onkeydown: onCaptureKey, oninput: () => saveDraft(draftStore(), capture.value) });
const captureStatus = el('div', { class: 'capture-status', role: 'status' });
const notes = el('div', { class: 'notes', hidden: true });  // quiet warnings (time zone, Fast Startup)
const grid = el('div', { class: 'grid' });
const popup = el('div', { class: 'popup', hidden: true });
const body = el('div', { class: 'body', hidden: true }, capture, captureStatus, notes, grid, popup);
root.appendChild(el('div', { class: 'panel' }, bar, body));

function setExpanded(on) {
  expanded = on;
  expandBtn.textContent = on ? '▴' : '▾';
  expandBtn.setAttribute('aria-expanded', String(on));
  expandBtn.setAttribute('aria-label', on ? 'Collapse' : 'Expand');
  body.classList.remove('show');
  body.hidden = !on;
  if (on) {
    renderBody();
    capture.focus();
    requestAnimationFrame(() => { body.classList.add('show'); fitWindow(); });
  } else {
    openTile = null;
    fitWindow();
  }
}

document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && expanded) setExpanded(false);
});

// -- capture ---------------------------------------------------------------------------------
function showStatus(text, isErr) {
  clearTimeout(savedTimer);
  captureStatus.textContent = text;
  captureStatus.classList.toggle('err', !!isErr);
  if (text && !isErr) savedTimer = setTimeout(() => { captureStatus.textContent = ''; }, 2000);
}

async function onCaptureKey(ev) {
  if (ev.key !== 'Enter' || ev.isComposing) return;
  const text = capture.value;
  if (!text.trim() || saving) return;
  saving = true;
  try {
    const res = await post('/api/entries', { text, source: 'widget' });
    if (capture.value === text) capture.value = '';
    saveDraft(draftStore(), capture.value); // cleared, or whatever was typed during the save
    showStatus(res && res.held ? 'Saved · held on this OS' : 'Saved', false);
    if (res && res.held) scheduleRefresh(); // the banner's held count
  } catch (e) {
    showStatus(e.message || 'Could not save', true);
  } finally {
    saving = false;
  }
}

// -- rendering -------------------------------------------------------------------------------
function renderBar() {
  synopsis.classList.toggle('offline', offline);
  if (offline) {
    synopsis.textContent = OFFLINE;
    synopsis.classList.remove('drive');
    synopsis.title = '';
    dot.hidden = true;
  } else if (data) {
    synopsis.textContent = synopsisText(data);
    synopsis.title = data.drive ? synopsis.textContent : '';  // the banner is long: all of it on hover
    synopsis.classList.toggle('drive', !!data.drive);
    dot.hidden = !(data.attention || data.drive);
  }
}

async function openInApp(name, view) {
  popupError = '';
  try {
    if (name === 'arm') await post('/api/open', { target: 'arm' });
    else await post('/api/open', { target: 'app', view: view || TILE_VIEW[name] });
  } catch (e) {
    popupError = e.message || 'Could not open';
    renderPopup();
    fitWindow();
  }
}

function renderPopup() {
  clear(popup);
  if (!openTile || !data) { popup.hidden = true; return; }
  const name = openTile;
  const rows = popupRows(name, data);
  popup.hidden = false;
  popup.appendChild(el('h3', null, TILE_LABEL[name] || name));
  if (name === 'arm') {
    popup.appendChild(el('div', { class: 'empty' }, `${data.arm.url} · ${data.arm.reachable ? 'reachable' : 'offline'}`));
  } else if (!rows.length) {
    popup.appendChild(el('div', { class: 'empty' }, 'Nothing here'));
  } else {
    popup.appendChild(el('ul', { class: 'rows' }, rows.map((r, i) => {
      const inner = [el('span', { class: 'text' }, r.text), el('span', { class: 'meta' }, r.meta)];
      return el('li', null, r.view
        ? el('button', { class: 'row', type: 'button', 'data-focus': `row:${i}`, onclick: () => openInApp(name, r.view) }, inner)
        : el('div', { class: 'row' }, inner));
    })));
  }
  if (popupError) popup.appendChild(el('div', { class: 'popup-err', role: 'alert' }, popupError));
  popup.appendChild(el('button', { class: 'open-link', type: 'button', 'data-focus': 'footer', onclick: () => openInApp(name) },
    name === 'arm' ? 'Open robot arm ↗' : 'Open in BrainHub ↗'));
}

function focusKey() {
  const a = document.activeElement;
  if (a && a.dataset && a.dataset.focus && (grid.contains(a) || popup.contains(a))) return a.dataset.focus;
  return null;
}

function renderBody() {
  if (!expanded || !data) return;
  const key = focusKey();
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  notes.textContent = warnings.join(' ');
  notes.hidden = !warnings.length;
  clear(grid);
  for (const name of data.tiles) {
    if (!TILE_LABEL[name]) continue;
    const s = tileSummary(name, data);
    grid.appendChild(el('button', {
      class: 'tile' + (s.warn ? ' warn' : ''), type: 'button', 'data-focus': `tile:${name}`, 'aria-expanded': String(openTile === name),
      onclick: () => { openTile = openTile === name ? null : name; popupError = ''; renderBody(); fitWindow(); },
    },
    el('span', { class: 'label' }, TILE_LABEL[name]),
    el('span', { class: 'value' }, s.value),
    el('span', { class: 'caption' }, s.caption)));
  }
  renderPopup();
  if (key) {
    for (const n of root.querySelectorAll('[data-focus]')) {
      if (n.dataset.focus === key) { n.focus(); break; }
    }
  }
}

function render() {
  renderBar();
  renderBody();
  fitWindow();
}

// -- data refresh ----------------------------------------------------------------------------
async function refresh() {
  if (loading) { again = true; return; }
  loading = true;
  try {
    data = await get('/api/widget');
    offline = false;
  } catch (_) {
    offline = true;
  }
  loading = false;
  render();
  if (again) { again = false; refresh(); }
}

function scheduleRefresh() {
  clearTimeout(debounce);
  debounce = setTimeout(refresh, 300);
}

for (const type of REFRESH_EVENTS) onEvent(type, scheduleRefresh);
onEvent('ws.open', scheduleRefresh);
setInterval(() => { if (!offline) refresh(); }, 60000);
setInterval(() => { if (offline) refresh(); }, 5000);

// A draft left by a reload (hub restart, shell re-navigation) comes back, expanded so it is seen.
const draft = loadDraft(draftStore());
if (draft) {
  capture.value = draft;
  setExpanded(true);
}
refresh();
