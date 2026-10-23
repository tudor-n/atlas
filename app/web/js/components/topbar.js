// The top bar (phase 4.5 design §3): the six tabs and SETTINGS (selected from the route), the SETUP
// label, and the status cluster from status-model.statusFrom(): amber chips, REVIEW n (entries to
// review, as Today's TO REVIEW counts them; the proposals are in its title), 5H n%, the agents dot and
// the VOICE switch. The static parts (brand, #nav, #mode, #status, #settings-tab) are in
// index.html. Text enters only through el() and textContent (rule 6).
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { TABS, SETTINGS, tabFor } from '../nav-model.js';
import { statusFrom, reviewCounts } from '../status-model.js';
import { widgetVoice } from '../views/voice-model.js';
import { readonlyReason } from '../drive.js';
import { chip, dot, switchEl } from './ui.js';

// What can change the cluster's numbers: the widget's own list, plus tablet requests.
const REFRESH_EVENTS = ['items.changed', 'agents.changed', 'usage.changed', 'filing.started', 'filing.done',
  'filing.failed', 'requests.changed'];

const title = (label, key) => `${label[0]}${label.slice(1).toLowerCase()} (Alt+${key})`;

// shell() -> {mode: 'full' | 'setup' | 'reduced', drive, offline}: the app's own /api/state view.
export function mountTopbar({ shell }) {
  const nav = document.getElementById('nav');
  const modeBox = document.getElementById('mode');
  const statusBox = document.getElementById('status');
  const settingsTab = document.getElementById('settings-tab');
  settingsTab.title = title(SETTINGS.label, SETTINGS.key);

  const tabs = TABS.map((t) => el('a', {
    class: 'tab', href: `#/${t.id}`, 'data-press': true, 'data-tab': t.id, title: title(t.label, t.key),
  }, t.label));
  clear(nav).append(...tabs);

  // -- the status cluster: persistent nodes, so a refresh never steals focus ---------------------
  const chips = el('span', { class: 'chips' });
  const reviewV = el('span', { class: 'v' }, '–');
  // At narrow widths the label shortens to REV (a bare amber number would say nothing).
  const review = el('a', { class: 'sl ro rv', href: '#/today', 'data-press': true },
    el('span', { class: 'l long' }, 'REVIEW'), el('span', { class: 'l short' }, 'REV'), reviewV);
  const usageL = el('span', { class: 'u' }, '5H');
  const usageV = el('span', { class: 'v' }, '–');
  const usage = el('a', { class: 'sl ro', href: '#/usage', 'data-press': true }, usageL, usageV);
  const agentsDot = dot('');
  const agentsLong = el('span', { class: 'long' });
  const agentsShort = el('span', { class: 'short' });
  const agents = el('a', { class: 'sl dl', href: '#/agents', 'data-press': true }, agentsDot, agentsLong, agentsShort);
  const voiceSw = switchEl({ small: true, label: 'VOICE', onchange: toggleVoice });
  clear(statusBox).append(chips, review, usage, agents, voiceSw);

  let summary = null;   // GET /api/widget, or null
  let counts = null;    // status-model.reviewCounts() of the review lists, or null (not loaded)
  let voice = null;     // GET /api/voice, or null
  let offline = false;  // the last /api/widget failed for want of a connection
  let lastMode = null;
  // Until the app's first /api/state answer (or failure) the mode is only a guess, so nothing is fetched
  // or painted: a reduced or setup hub would answer the fetches with errors.
  let known = false;

  function render() {
    if (!known) return;
    paint();
    fit();
  }

  // When the tabs would be cut, the bar first tightens (.tight), then drops its labels (.compact); if
  // they are still cut, the strip scrolls and shows its edge (.ovf).
  const bar = document.getElementById('top');
  const cut = () => !nav.hidden && nav.scrollWidth > nav.clientWidth + 1;
  function fit() {
    bar.classList.remove('tight', 'compact');
    if (cut()) bar.classList.add('tight');
    if (cut()) bar.classList.add('compact');
    nav.classList.toggle('ovf', cut());
  }
  if (typeof ResizeObserver === 'function') new ResizeObserver(fit).observe(bar);
  else window.addEventListener('resize', fit);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);  // Roboto changes the widths

  function paint() {
    const s = shell();
    const full = s.mode === 'full';
    nav.hidden = !full;
    settingsTab.hidden = !full;
    modeBox.hidden = s.mode !== 'setup';
    modeBox.textContent = s.mode === 'setup' ? 'SETUP' : '';
    statusBox.hidden = s.mode === 'setup';
    if (s.mode !== lastMode) {
      lastMode = s.mode;
      if (full) { refresh(); refreshVoice(); }  // the first known mode, or back from setup or reduced
    }
    if (s.mode === 'setup') return;

    const st = statusFrom(full ? summary : null, full ? voice : null,
      { offline: !!s.offline || (full && offline), drive: s.drive || null, review: full ? counts : null });
    clear(chips).append(...st.chips.map((c) => chip(c.text, { title: c.title })));
    for (const n of [review, usage, agents]) n.hidden = !full;
    voiceSw.hidden = !full || !st.voice.show;
    if (!full) return;

    reviewV.textContent = String(st.review.count);
    review.classList.toggle('warn', st.review.warn);
    review.title = st.review.title;

    usage.hidden = !st.usage;
    if (st.usage) {
      usageL.textContent = st.usage.label;
      usageV.textContent = `${st.usage.pct}%`;
      usage.classList.toggle('warn', st.usage.warn);
      usage.title = `${st.usage.label} window: ${st.usage.pct}% used · Usage`;
    }

    const a = st.agents;
    agentsDot.className = a.dot ? `dot ${a.dot}` : 'dot';
    agentsLong.textContent = a.text;
    agentsShort.textContent = a.waiting > 0 ? `${a.running} · ${a.waiting}` : String(a.running);
    agents.title = `${a.text} · Agents`;

    if (st.voice.show) {
      const ro = readonlyReason(s.drive);
      voiceSw.setOn(st.voice.on);
      voiceSw.disabled = !!ro;
      voiceSw.title = ro || st.voice.title;
    }
  }

  // -- data ------------------------------------------------------------------------------------
  let loading = false;
  let again = false;
  let debounce = null;

  async function refresh() {
    if (!known || shell().mode !== 'full') { render(); return; }
    if (loading) { again = true; return; }
    loading = true;
    try {
      summary = await api.get('/api/widget');
      offline = false;
      // The widget's summary counts proposals; Today counts entries. Until the hub sends
      // `review.entries` itself, the bar counts the two lists Today's queue is made of.
      if (Number.isFinite(summary && summary.review && summary.review.entries)) counts = null;
      else {
        try {
          const [groups, failed] = await Promise.all([api.get('/api/proposals'), api.get('/api/filings?status=failed')]);
          counts = reviewCounts(groups, failed);
        } catch (_) { counts = null; }  // the summary's own numbers stand in
      }
    } catch (e) {
      offline = !e.status;  // an HTTP error is an answer; only a failed fetch means offline
    }
    loading = false;
    render();
    if (again) { again = false; refresh(); }
  }

  function schedule() {
    clearTimeout(debounce);
    debounce = setTimeout(refresh, 300);
  }

  async function refreshVoice() {
    if (!known || shell().mode !== 'full') return;
    try { voice = await api.get('/api/voice'); } catch (_) { voice = null; }
    render();
  }

  // Same as the widget's speaker: switch on or off, or end a pause. The next refresh shows the truth.
  let voiceBusy = false;
  async function toggleVoice() {
    const w = widgetVoice(voice);
    if (!w.show || voiceBusy) return false;
    voiceBusy = true;
    try {
      if (w.action === 'resume') await api.post('/api/voice/pause', { resume: true });
      else await api.put('/api/settings', { voice_enabled: w.action === 'on' });
    } catch (_) { /* refused (read-only) or unreachable: refreshVoice puts the switch back */ }
    voiceBusy = false;
    await refreshVoice();
    return undefined;
  }

  for (const type of REFRESH_EVENTS) api.onEvent(type, schedule);
  api.onEvent('ws.open', () => { schedule(); refreshVoice(); });
  api.onEvent('ws.close', schedule);  // the hub went away: the failed fetch shows OFFLINE
  api.onEvent('voice.changed', ({ data: d }) => { if (!(d && d.download && d.download.running)) refreshVoice(); });
  setInterval(() => { refresh(); refreshVoice(); }, 60000);  // quiet hours, a pause running out, usage resets
  setInterval(() => { if (offline) refresh(); }, 5000);

  // The selected tab follows the route; a scrolled tab strip brings it into view.
  function setRoute(name) {
    const t = name ? tabFor(name) : null;
    for (const a of tabs) {
      if (a.dataset.tab === t) {
        a.setAttribute('aria-current', 'page');
        if (nav.scrollWidth > nav.clientWidth) a.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      } else {
        a.removeAttribute('aria-current');
      }
    }
    if (t === SETTINGS.id) settingsTab.setAttribute('aria-current', 'page');
    else settingsTab.removeAttribute('aria-current');
  }

  // The app calls render() once it knows the mode; the first full paint fetches the cluster's data.
  return { render: () => { known = true; render(); }, refresh, setRoute };
}
