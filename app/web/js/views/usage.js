// Usage: the two Claude Code rate-limit windows, fed by the status line. Since phase 4.5 it is the
// AGENTS view's right-hand panel (agents.js mounts it with mountUsage); `#/usage` opens Agents with
// this panel focused. render() still draws it alone, for anything that imports the old view.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { label, meter } from '../components/ui.js';
import { resetText, usedText, meterCells } from './agents-model.js';

const SEGMENTS = 20;

// One window: label, the .big value, a 20-segment meter (amber at the warn level), the reset time.
// warnPct is kept for older callers: the server's `warn` flag already applies the warn level.
export function windowBlock(title, w, _warnPct, now = new Date()) {
  if (!w) {
    return el('section', { class: 'uw' }, label(title),
      el('div', { class: 'big f' }, '–'),
      meter(SEGMENTS, 0, { label: `${title}: no data` }),
      el('p', { class: 'rs nd' }, 'No data yet. It appears after your next Claude Code reply.'));
  }
  const used = usedText(w);
  return el('section', { class: w.warn ? 'uw warn' : 'uw' },
    label(title),
    el('div', { class: used === '–' ? 'big f' : 'big' }, used),
    meter(SEGMENTS, meterCells(w.used, SEGMENTS), { warn: !!w.warn, label: `${title}: ${used} used` }),
    el('p', { class: 'rs' }, resetText(w, now)));
}

// Fills `box` with the usage panel and keeps it live. Returns the cleanup.
export function mountUsage(box) {
  let dead = false, loading = false, again = false;
  const body = el('div', { class: 'ub' });
  const msg = el('div', { class: 'um', role: 'alert' });
  const open = el('button', { type: 'button', class: 'b', onclick: async () => {
    clear(msg);
    try { await api.post('/api/open', { target: 'fable_usage' }); } catch (e) {
      msg.appendChild(el('p', { class: 'err' }, `Couldn't open it: ${e.message}`));
    }
  } }, 'Fable usage ↗');
  const age = el('p', { class: 'age' });
  box.append(
    el('div', { class: 'sect-h' }, el('h2', { class: 'lb' }, 'Usage')),
    body,
    el('div', { class: 'uf' }, age, open, msg));

  async function refresh() {
    if (dead) return;
    if (loading) { again = true; return; }
    loading = true;
    try {
      const u = await api.get('/api/usage');
      if (dead) return;
      const pct = Number(u.warn_pct) || 80;
      const now = new Date();
      clear(body).append(
        windowBlock('5-hour window', u.five_hour, pct, now),
        windowBlock('7-day', u.seven_day, pct, now));
      age.textContent = u.age_s > 300 ? `updated ${Math.round(u.age_s / 60)} min ago` : '';
    } catch (e) {
      if (!dead) clear(body).appendChild(el('p', { class: 'err ue', role: 'alert' }, `Couldn't load usage: ${e.message}`));
    } finally {
      loading = false;
      if (again) { again = false; refresh(); }
    }
  }

  refresh();
  const off = api.onEvent('usage.changed', refresh);
  const timer = setInterval(refresh, 60000);
  return () => { dead = true; off(); clearInterval(timer); };
}

export async function render(container) {
  const side = el('aside', { class: 'side usage', 'aria-label': 'Usage' });
  container.appendChild(el('div', { class: 'vw split agents' }, el('div', { class: 'main' }), side));
  return mountUsage(side);
}
