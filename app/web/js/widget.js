// Desktop widget page: collapsed synopsis bar, expanded capture + tiles + popups (design §7.1),
// in the arm language (phase 4.5 design §7; layout in widget.css).
import { get, post, put, onEvent } from './api.js';
import { el, clear } from './dom.js';
import { TILE_LABEL, tileSummary, popupRows, synopsisText, loadDraft, saveDraft, openPlan, tauriInvoke } from './widget-model.js';
import { widgetVoice } from './views/voice-model.js';

const WIDTH = 520;
const OFFLINE = 'BrainHub offline · retrying';
const SVG = 'http://www.w3.org/2000/svg';
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
// Press feedback (.prs on pointer-down, never :active), as press.js does in the app.
document.addEventListener('pointerdown', (ev) => {
  const t = ev.target instanceof Element ? ev.target.closest('.b, .wtile, button.wrow') : null;
  if (!t || t.disabled) return;
  t.classList.add('prs');
  const up = () => { t.classList.remove('prs'); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up); };
  window.addEventListener('pointerup', up);
  window.addEventListener('pointercancel', up);
});

const grip = el('span', { class: 'wgrip', 'aria-hidden': 'true', 'data-tauri-drag-region': true }, '⋮⋮');
const dot = el('span', { class: 'watt', title: 'Needs your attention', hidden: true, 'data-tauri-drag-region': true });
const synopsis = el('span', { class: 'wsyn', 'data-tauri-drag-region': true }, 'Loading…');
const expandBtn = el('button', { class: 'b wg', type: 'button', 'aria-label': 'Expand', 'aria-expanded': 'false', onclick: () => setExpanded(!expanded) }, '▾');
// The voice on/off (spec §2: reachable from the widget); hidden until the voice is installed.
const voiceBtn = el('button', { class: 'b wg', type: 'button', hidden: true, onclick: toggleVoice });
const bar = el('div', { class: 'wbar', 'data-tauri-drag-region': true }, grip, dot, synopsis, voiceBtn, expandBtn);

const capture = el('input', { class: 'wcap', type: 'text', placeholder: 'Write something down, Enter to save', 'aria-label': 'Capture', autocomplete: 'off', onkeydown: onCaptureKey, oninput: () => saveDraft(draftStore(), capture.value) });
const captureStatus = el('div', { class: 'wstat', role: 'status' });
const notes = el('div', { class: 'wnotes', hidden: true });  // quiet warnings (time zone, Fast Startup)
const grid = el('div', { class: 'wgrid' });
const popup = el('div', { class: 'wpop', hidden: true });
const body = el('div', { class: 'wbody', hidden: true }, el('div', { class: 'wcapbox' }, capture, captureStatus, notes), grid, popup);
root.appendChild(el('div', { class: 'wpanel' }, bar, body));

function setExpanded(on) {
  expanded = on;
  expandBtn.textContent = on ? '▴' : '▾';
  expandBtn.setAttribute('aria-expanded', String(on));
  expandBtn.setAttribute('aria-label', on ? 'Collapse' : 'Expand');
  body.hidden = !on;
  if (on) {
    renderBody();
    capture.focus();
    requestAnimationFrame(fitWindow);
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
    showStatus(res && res.held ? 'SAVED · HELD ON THIS OS' : 'SAVED', false);
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

// In the desktop shell a view opens in the main window (`open_main`); in a browser, or if the shell
// refuses, POST /api/open opens it in the browser. The arm tile always posts its own target.
async function openInApp(name, view) {
  popupError = '';
  const plan = openPlan(name, view, tauriInvoke(window));
  try {
    if (plan.invoke) {
      try {
        await plan.invoke('open_main', { view: plan.view });
        return;
      } catch (_) { /* an older shell without the command: the browser instead */ }
    }
    await post('/api/open', plan.body);
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
  popup.appendChild(el('div', { class: 'wpop-h' }, el('span', { class: 'lb' }, TILE_LABEL[name] || name)));
  if (name === 'arm') {
    popup.appendChild(el('div', { class: 'wempty' }, el('span', { class: 'm' }, `${data.arm.url} · ${data.arm.reachable ? 'reachable' : 'offline'}`)));
  } else if (!rows.length) {
    popup.appendChild(el('div', { class: 'wempty' }, 'Nothing here'));
  } else {
    popup.appendChild(el('ul', null, rows.map((r, i) => {
      const inner = [el('span', { class: 'wt' }, r.text), r.meta ? el('span', { class: 'wm' }, r.meta) : null];
      return el('li', null, r.view
        ? el('button', { class: 'wrow', type: 'button', 'data-focus': `row:${i}`, onclick: () => openInApp(name, r.view) }, inner)
        : el('div', { class: 'wrow' }, inner));
    })));
  }
  if (popupError) popup.appendChild(el('div', { class: 'wpop-err err', role: 'alert' }, popupError));
  popup.appendChild(el('div', { class: 'wpop-f' },
    el('button', { class: 'b h40', type: 'button', 'data-focus': 'footer', onclick: () => openInApp(name) },
      name === 'arm' ? 'OPEN ROBOT ARM ›' : 'OPEN IN BRAINHUB ›')));
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
    const open = openTile === name;
    grid.appendChild(el('button', {
      class: 'wtile' + (s.warn ? ' warn' : '') + (open ? ' open' : ''), type: 'button', 'data-focus': `tile:${name}`, 'data-tile': name, 'aria-expanded': String(open),
      onclick: () => { openTile = openTile === name ? null : name; popupError = ''; renderBody(); fitWindow(); },
    },
    el('span', { class: 'lb' }, TILE_LABEL[name]),
    el('span', { class: 'wv' }, s.value),
    el('span', { class: 'wc' }, s.caption)));
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
  renderVoice();
  renderBody();
  fitWindow();
}

// -- the voice's speaker ---------------------------------------------------------------------
let voice = null;
let voiceBusy = false;

function renderVoice() {
  const w = widgetVoice(offline ? null : voice);
  voiceBtn.hidden = !w.show;
  if (!w.show) return;
  const on = w.action === 'off';  // on (a click turns it off); quiet hours keep it on, without waves
  const glyph = on ? (w.muted ? 'quiet' : 'on') : 'off';
  if (voiceBtn.dataset.glyph !== glyph) {
    clear(voiceBtn).appendChild(speaker(glyph));
    voiceBtn.dataset.glyph = glyph;
  }
  voiceBtn.title = w.title;
  voiceBtn.setAttribute('aria-label', w.title);
  voiceBtn.setAttribute('aria-pressed', String(on));
  voiceBtn.classList.toggle('on', on);
}

// A 16px speaker in currentColor (no emoji): with waves when on, plain in quiet hours, crossed when
// off or paused. Fixed shapes only, never data.
function speaker(kind) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  const add = (tag, attrs) => {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    svg.appendChild(n);
  };
  add('path', { d: 'M2 6h3l4-3.5v11L5 10H2z', fill: 'currentColor' });
  const stroke = { fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'square' };
  if (kind === 'on') {
    add('path', { ...stroke, d: 'M11 5.5a3.5 3.5 0 0 1 0 5' });
    add('path', { ...stroke, d: 'M12.5 3a6.5 6.5 0 0 1 0 10' });
  } else if (kind === 'off') {
    add('path', { ...stroke, d: 'M11 6l4 4M15 6l-4 4' });
  }
  return svg;
}

async function refreshVoice() {
  try { voice = await get('/api/voice'); } catch (_) { voice = null; }
  renderVoice();
}

async function toggleVoice() {
  const w = widgetVoice(voice);
  if (!w.show || voiceBusy) return;
  voiceBusy = true;
  try {
    if (w.action === 'resume') await post('/api/voice/pause', { resume: true });
    else await put('/api/settings', { voice_enabled: w.action === 'on' });
  } catch (_) { /* the next refresh shows the truth */ }
  voiceBusy = false;
  refreshVoice();
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
onEvent('ws.close', scheduleRefresh);  // the hub went away: show OFFLINE now, not at the next 60 s tick
onEvent('ws.open', refreshVoice);
onEvent('voice.changed', ({ data: d }) => { if (!(d && d.download && d.download.running)) refreshVoice(); });
setInterval(refreshVoice, 60000);  // quiet hours begin and end, and a pause runs out, without an event
setInterval(() => { if (!offline) refresh(); }, 60000);
setInterval(() => { if (offline) refresh(); }, 5000);

// A draft left by a reload (hub restart, shell re-navigation) comes back, expanded so it is seen.
const draft = loadDraft(draftStore());
if (draft) {
  capture.value = draft;
  setExpanded(true);
}
refresh();
refreshVoice();
