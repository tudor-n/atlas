// Desktop widget page (design §7.1; the redesign of 2026-10-24): the collapsed bar says what needs
// you; expanded, it adds capture, one NEEDS YOU list (its own scroll), a status strip and OPEN ATLAS.
// The widget is ATLAS minimised: the shell shows it only while the main window is (follow.rs).
// In the arm language (phase 4.5 design §7; layout in widget.css).
import { get, post, put, onEvent } from './api.js';
import { el, clear } from './dom.js';
import { needsYou, barText, attention, statusStrip, loadDraft, saveDraft, openPlan, tauriInvoke, openOutcome } from './widget-model.js';
import { widgetVoice } from './views/voice-model.js';
import { shownIntent } from './intent-model.js';
import { seg } from './components/ui.js';

const WIDTH = 520;
const OFFLINE = 'ATLAS offline · retrying';
const SVG = 'http://www.w3.org/2000/svg';
const REFRESH_EVENTS = ['items.changed', 'agents.changed', 'usage.changed', 'filing.done', 'filing.failed', 'map.changed',
  'requests.changed'];

let data = null;
let offline = false;
let expanded = false;
let debounce = null;
let savedTimer = null;
let saving = false;
let openError = '';
let loading = false;
let again = false;

const root = document.getElementById('w');

// localStorage may be missing or throw on access (blocked storage): treat that as "no draft".
function draftStore() {
  try { return window.localStorage; } catch (_) { return null; }
}

// -- Tauri bridge: no-op in a plain browser ---------------------------------------------------
// The window is exactly the panel, in device pixels: the shell's `fit_widget` pins it to that size
// (an older shell only has setSize, which GTK may ignore).
function fitWindow() {
  if (!window.__TAURI__) return;
  try {
    const panel = root.firstElementChild;
    if (!panel) return;
    const h = Math.ceil(panel.getBoundingClientRect().height);
    // In device pixels: the page may be drawn at another scale than the window's (125 % desktop).
    const scale = window.devicePixelRatio || 1;
    const invoke = tauriInvoke(window);
    const fallback = () => window.__TAURI__.window.getCurrentWindow()
      .setSize(new window.__TAURI__.dpi.PhysicalSize(Math.ceil(WIDTH * scale), Math.ceil(h * scale)));
    if (invoke) Promise.resolve(invoke('fit_widget', { width: WIDTH, height: h, scale })).catch(fallback);
    else fallback();
  } catch (_) { /* not running in the desktop shell */ }
}

// The – button: hide the widget (only in the desktop shell). ATLAS stays in the taskbar or the
// tray; the widget comes back the next time the main window is minimised.
function hideWidget() {
  const invoke = tauriInvoke(window);
  if (invoke) Promise.resolve(invoke('hide_widget')).catch(() => {});
}

// -- static shell, built once so the capture field keeps its text and focus ------------------
// Press feedback (.prs on pointer-down, never :active), as press.js does in the app.
document.addEventListener('pointerdown', (ev) => {
  const t = ev.target instanceof Element ? ev.target.closest('.b, button.wrow') : null;
  if (!t || t.disabled) return;
  t.classList.add('prs');
  const up = () => { t.classList.remove('prs'); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up); };
  window.addEventListener('pointerup', up);
  window.addEventListener('pointercancel', up);
});

const grip = el('span', { class: 'wgrip', 'aria-hidden': 'true', 'data-tauri-drag-region': true }, atlasMark(20));
const dot = el('span', { class: 'watt', title: 'Needs your attention', hidden: true, 'data-tauri-drag-region': true });
const synopsis = el('span', { class: 'wsyn', 'data-tauri-drag-region': true }, 'Loading…');
const expandBtn = el('button', { class: 'b wg', type: 'button', 'aria-label': 'Expand', 'aria-expanded': 'false', onclick: () => setExpanded(!expanded) }, '▾');
// The voice on/off (spec §2: reachable from the widget); hidden until the voice is installed.
const voiceBtn = el('button', { class: 'b wg', type: 'button', hidden: true, onclick: toggleVoice });
const hideBtn = el('button', { class: 'b wg', type: 'button', 'aria-label': 'Hide the widget', title: 'Hide the widget (ATLAS stays in the taskbar)',
  hidden: !tauriInvoke(window), onclick: hideWidget }, '–');
const bar = el('div', { class: 'wbar', 'data-tauri-drag-region': true }, grip, dot, synopsis, voiceBtn, expandBtn, hideBtn);

// One of the one input's doors (voice-input decisions VI1): NOTE | ASK follows the intent as you type
// (a press overrides it until the field is emptied). A note saves here; a question opens ATLAS on it.
const INTENTS = [{ value: 'note', label: 'NOTE' }, { value: 'ask', label: 'ASK' }];
let pinned = null;
const capture = el('input', { class: 'wcap', type: 'text', placeholder: 'Write something down or ask, Enter to send', 'aria-label': 'Capture or ask', autocomplete: 'off', onkeydown: onCaptureKey,
  oninput: () => { if (!capture.value.trim()) pinned = null; syncIntent(); saveDraft(draftStore(), capture.value); } });
const pick = seg(INTENTS, 'note', (v) => { pinned = v; syncIntent(); capture.focus(); }, { size: 40, label: 'Note or question' });
const intent = () => shownIntent(capture.value, pinned);
function syncIntent() {
  const now = intent();
  INTENTS.forEach((o, i) => pick.children[i].setAttribute('aria-pressed', o.value === now ? 'true' : 'false'));
}
const captureStatus = el('div', { class: 'wstat', role: 'status' });
const notes = el('div', { class: 'wnotes', hidden: true });  // quiet warnings (time zone, Fast Startup)
const listCount = el('span', { class: 'm wn' });
const listHead = el('div', { class: 'wlist-h' }, el('span', { class: 'lb' }, 'NEEDS YOU'), listCount);
const list = el('ul', { class: 'wlist', 'aria-label': 'Needs you', onkeydown: onListKey });
const strip = el('div', { class: 'wstrip m', 'aria-label': 'Status' });
const openErr = el('div', { class: 'wopen-err err', role: 'alert', hidden: true });
const footer = el('div', { class: 'wfoot' },
  el('button', { class: 'b h40', type: 'button', 'data-focus': 'open', onclick: () => openInApp(null) }, 'OPEN ATLAS ›'));
const body = el('div', { class: 'wbody', hidden: true },
  el('div', { class: 'wcapbox' }, el('div', { class: 'wcaprow' }, capture, pick), captureStatus, notes), listHead, list, strip, openErr, footer);
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

// The arrows move between the capture field and the list's rows; Enter on a row opens it.
function rowButtons() { return [...list.querySelectorAll('button.wrow')]; }
function onListKey(ev) {
  if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
  const rows = rowButtons();
  const i = rows.indexOf(document.activeElement);
  if (i < 0) return;
  ev.preventDefault();
  if (ev.key === 'ArrowUp' && i === 0) capture.focus();
  else rows[Math.min(rows.length - 1, Math.max(0, i + (ev.key === 'ArrowDown' ? 1 : -1)))].focus();
}

async function onCaptureKey(ev) {
  if (ev.key === 'ArrowDown' && !ev.isComposing) {
    const first = rowButtons()[0];
    if (first) { ev.preventDefault(); first.focus(); }
    return;
  }
  if (ev.key !== 'Enter' || ev.isComposing) return;
  const text = capture.value;
  if (!text.trim() || saving) return;
  saving = true;
  if (intent() === 'ask') {
    try {  // the hub holds it for the ATLAS screen, which opens on it; the text stays until it has opened
      await post('/api/atlas/handoff', { text });
      if (!await openInApp('atlas')) {
        showStatus("COULDN'T OPEN ATLAS · YOUR TEXT IS STILL HERE", true);
        return;
      }
      if (capture.value === text) { capture.value = ''; pinned = null; syncIntent(); }
      saveDraft(draftStore(), capture.value);
      showStatus('ASKING ATLAS', false);
    } catch (e) {
      showStatus(e.message || 'Could not ask', true);
    } finally {
      saving = false;
    }
    return;
  }
  try {
    const res = await post('/api/entries', { text, source: 'widget', reply: true });
    if (capture.value === text) { capture.value = ''; pinned = null; syncIntent(); }
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
    synopsis.textContent = barText(data);
    synopsis.title = synopsis.textContent;  // the bar cuts long text short: all of it on hover
    synopsis.classList.toggle('drive', !!data.drive);
    dot.hidden = !attention(data);
  }
}

// In the desktop shell a view opens in the main window (`open_main`), which then hides the widget;
// in a browser, or if the shell refuses (an older shell, a window it could not show) or doesn't
// answer within 3 s, POST /api/open opens it in the browser.
// True once the view is opening (the main window, or the browser through the hub).
async function openInApp(view) {
  openError = '';
  const plan = openPlan(view, tauriInvoke(window));
  let opened = false;
  try {
    if (plan.invoke && await openOutcome(() => plan.invoke('open_main', { view: plan.view })) === 'shell') return true;
    await post('/api/open', plan.body);
    opened = true;
  } catch (e) {
    openError = e.message || 'Could not open';
  }
  renderBody();
  fitWindow();
  return opened;
}

function focusKey() {
  const a = document.activeElement;
  return a && a.dataset && a.dataset.focus && body.contains(a) ? a.dataset.focus : null;
}

function renderBody() {
  if (!expanded || !data) return;
  const key = focusKey();
  const warnings = Array.isArray(data.warnings) ? data.warnings : [];
  notes.textContent = warnings.join(' ');
  notes.hidden = !warnings.length;
  const rows = needsYou(data);
  listCount.textContent = rows.length ? String(rows.length) : '';
  clear(list);
  if (!rows.length) list.appendChild(el('li', { class: 'wempty' }, 'Nothing needs you.'));
  for (const r of rows) {
    list.appendChild(el('li', null, el('button', {
      class: 'wrow' + (r.warn ? ' warn' : ''), type: 'button', 'data-focus': `row:${r.key}`, title: r.text,
      onclick: () => openInApp(r.view),
    }, el('span', { class: 'wsq', 'aria-hidden': 'true' }), el('span', { class: 'wt' }, r.text), el('span', { class: 'wm' }, r.meta))));
  }
  clear(strip);
  statusStrip(data).forEach((s, i) => {
    if (i) strip.appendChild(el('span', { class: 'wsep', 'aria-hidden': 'true' }, '·'));
    strip.appendChild(el('span', { class: s.warn ? 'amb' : '' }, s.text));
  });
  openErr.textContent = openError;
  openErr.hidden = !openError;
  if (key) {
    for (const n of body.querySelectorAll('[data-focus]')) {
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

// The ATLAS mark (the logo handoff, 64-unit grid) at `size` px, as the top bar's brand draws it:
// ink in currentColor, the knockout and the blue centre from components.css (.mk). The grip slot
// holds it, so it is the handle the panel is dragged by.
function atlasMark(size) {
  const svg = document.createElementNS(SVG, 'svg');
  for (const [k, v] of Object.entries({ class: 'mk', viewBox: '0 0 64 64', width: size, height: size, 'aria-hidden': 'true' })) {
    svg.setAttribute(k, String(v));
  }
  const add = (tag, attrs) => {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    svg.appendChild(n);
  };
  add('circle', { cx: '32', cy: '32', r: '21', fill: 'none', stroke: 'currentColor', 'stroke-width': '3.5' });
  const slot = { width: '12', height: '12', rx: '1.5', 'stroke-width': '3' };
  for (const [x, y] of [['26', '5'], ['26', '47'], ['5', '26']]) add('rect', { ...slot, class: 'ks', x, y, fill: 'currentColor' });
  add('rect', { ...slot, class: 'kf', x: '47', y: '26', stroke: 'currentColor' });  // the open task
  add('circle', { class: 'hub', cx: '32', cy: '32', r: '7' });
  return svg;
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
  syncIntent();
  setExpanded(true);
}
refresh();
refreshVoice();
