// Usage: the two Claude Code rate-limit windows, fed by the status line.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { resetText } from './agents-model.js';

function windowBlock(title, w, warnPct) {
  if (!w) {
    return el('section', { class: 'meter-block' }, el('h2', {}, title),
      el('p', { class: 'muted' }, 'No data yet. It appears after your next Claude Code reply.'));
  }
  const used = w.used == null ? null : Math.round(w.used);
  const block = el('section', { class: w.warn ? 'meter-block warn' : 'meter-block' },
    el('h2', {}, title),
    el('div', { class: 'big-pct' }, used == null ? '?' : `${used}%`));
  if (used != null) {
    block.appendChild(el('meter', { min: 0, max: 100, low: Math.max(0, warnPct - 1), high: warnPct, optimum: 0,
      value: used, 'aria-label': `${title} used` }));
  }
  block.appendChild(el('div', { class: 'meta' }, resetText(w, new Date())));
  return block;
}

export async function render(container) {
  let dead = false;
  const root = el('div', {});
  container.appendChild(root);

  async function refresh() {
    try {
      const u = await api.get('/api/usage');
      if (dead) return;
      const pct = Number(u.warn_pct) || 80;
      clear(root);
      root.appendChild(el('h1', {}, 'Usage'));
      root.appendChild(windowBlock('5-hour window', u.five_hour, pct));
      root.appendChild(windowBlock('Weekly limit', u.seven_day, pct));
      if (u.age_s > 300) root.appendChild(el('p', { class: 'meta' }, `updated ${Math.round(u.age_s / 60)} min ago`));
      const msg = el('div', { role: 'alert' });
      root.appendChild(el('div', { class: 'actions' },
        el('button', { type: 'button', onclick: async () => {
          clear(msg);
          try { await api.post('/api/open', { target: 'fable_usage' }); } catch (e) {
            msg.appendChild(el('div', { class: 'error' }, `Couldn't open it: ${e.message}`));
          }
        } }, 'Fable usage ↗')), msg);
    } catch (e) {
      if (!dead) clear(root).appendChild(el('p', { class: 'error', role: 'alert' }, `Couldn't load usage: ${e.message}`));
    }
  }

  await refresh();
  const off = api.onEvent('usage.changed', refresh);
  const timer = setInterval(refresh, 60000);
  return () => { dead = true; off(); clearInterval(timer); };
}
