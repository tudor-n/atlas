// ATLAS'S WEEK on the ATLAS screen (chatter spec §6 C): the weekly self-review ATLAS writes in the
// background, as a title, a few paragraphs and up to two suggestions. Each suggestion is a row in the
// confirm card's language (.pp, today.css): ✓ makes it an open task in ATLAS's project, ✕ (press twice)
// drops it. Every string is agent-written, so it goes in as text only (el()). Nothing shows until
// GET /api/chatter/week returns a week; a hub without it (404) or a failed read leaves the section out.
// It reads again on voice.line (the one spoken line that points at a new week arrives that way).
import * as api from '../api.js';
import { el, clear, fmtWhen } from '../dom.js';
import { twice } from '../press.js';
import { button, dot, sectionHead } from '../components/ui.js';
import { weekView } from './voice-model.js';

// `blocked()` returns why writing can't work right now (a read-only root), or ''.
export function atlasWeek({ blocked = () => '' } = {}) {
  const node = el('section', { class: 'atl-week', hidden: true, 'aria-label': "ATLAS's week" });
  let week = null;
  let busy = false;
  let dead = false;
  let seq = 0;
  let timer = null;
  const errors = new Map();  // suggestion id -> what went wrong, until the next try
  const gates = [];

  async function load() {
    const mine = ++seq;
    let res;
    try { res = await api.get('/api/chatter/week'); } catch (_) { return; }  // not there yet, or the hub is away
    if (dead || mine !== seq) return;
    week = weekView(res && res.week);
    draw();
  }

  function draw() {
    for (const g of gates.splice(0)) g.destroy();
    clear(node);
    node.hidden = !week;
    if (!week) return;
    node.append(
      sectionHead("ATLAS'S WEEK", week.written ? fmtWhen(week.written) : null),
      el('div', { class: 'pane read atl-week-b' },
        week.title ? el('h3', { class: 'atl-week-t' }, week.title) : null,
        week.paragraphs.map((p) => el('p', {}, p))),
      week.suggestions.length ? el('ul', { class: 'pps' }, week.suggestions.map(row)) : null);
  }

  function row(x) {
    const reason = blocked();
    let acts;
    if (x.status === 'confirmed') {
      acts = el('div', { class: 'acts' }, el('span', { class: 'dl' }, dot('g'), 'ADDED AS A TASK'));
    } else {
      const ok = button('✓', { size: 40, title: reason || "Confirm: an open task in ATLAS's project", disabled: !!reason || busy,
        onclick: () => decide(x.id, 'confirm') });
      ok.setAttribute('aria-label', 'Confirm');
      const no = button('✕', { size: 40, cls: 'no', title: reason || 'Dismiss (press twice)', disabled: !!reason || busy });
      no.setAttribute('aria-label', 'Dismiss');
      gates.push(twice(no, { onGo: () => decide(x.id, 'dismiss') }));
      acts = el('div', { class: 'acts' }, ok, no);
    }
    const err = errors.get(x.id);
    return el('li', { class: 'pp' },
      el('span', { class: 'k lb' }, 'SUGGESTION'),
      el('div', { class: 'grow' }, el('div', { class: 't' }, x.text),
        err ? el('div', { class: 'cc-err err', role: 'alert' }, err) : null),
      acts);
  }

  async function decide(id, how) {
    if (busy) return;
    busy = true;
    errors.delete(id);
    draw();
    try {
      await api.post(`/api/chatter/week/${encodeURIComponent(id)}/${how}`, {});
    } catch (e) {
      errors.set(id, `${how === 'confirm' ? 'Not confirmed' : 'Not dismissed'}: ${e.message}`);
    }
    busy = false;
    if (dead) return;
    draw();
    await load();
  }

  const off = api.onEvent('voice.line', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { if (!dead && !busy) load(); }, 500);
  });
  load();

  return {
    node,
    destroy() {
      dead = true;
      clearTimeout(timer);
      off();
      for (const g of gates.splice(0)) g.destroy();
    },
  };
}
