// Journal: the verbatim history, newest first, grouped by day. Day picker, text filter (local search only).
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { entryCard, hitList } from '../components/text.js';

const PAGE = 50;

// "e-20261020-143000-ab12" -> "2026-10-20"
const dayOfId = (id) => {
  const m = /^e-(\d{4})(\d{2})(\d{2})-/.exec(id || '');
  return m ? `${m[1]}-${m[2]}-${m[3]}` : '';
};

export async function render(container, _params, query) {
  const focusId = (query && query.get('entry')) || '';
  let day = dayOfId(focusId);
  let entries = [];
  let more = false;
  let filter = '';
  let dead = false;
  let seq = 0;
  const subs = [];

  const dayInput = el('input', { id: 'j-day', type: 'date', value: day,
    onchange: () => { day = dayInput.value; reload(); } });
  const filterInput = el('input', { id: 'j-q', type: 'search', placeholder: 'Search the journal and notes' });
  const clearDay = el('button', { type: 'button', onclick: () => { day = ''; dayInput.value = ''; reload(); } }, 'All days');
  const form = el('form', { class: 'journal-controls', onsubmit: (ev) => { ev.preventDefault(); runFilter(); } },
    el('div', {}, el('label', { for: 'j-day' }, 'Day'), dayInput),
    el('div', {}, el('label', { class: 'invisible', for: 'j-day' }, ' '), clearDay),
    el('div', { class: 'grow' }, el('label', { for: 'j-q' }, 'Find text'), filterInput),
    el('div', {}, el('label', { class: 'invisible', for: 'j-q' }, ' '), el('button', { type: 'submit' }, 'Search')));
  const msg = el('div', { class: 'error', role: 'alert' });
  const list = el('div', {});
  const olderBtn = el('button', { type: 'button', hidden: true, onclick: () => loadOlder() }, 'Load older');
  container.appendChild(el('div', {}, el('h1', {}, 'Journal'), form, msg, list, olderBtn));

  const url = (extra) => `/api/entries?${new URLSearchParams({ ...(day ? { day } : {}), ...extra })}`;

  function draw() {
    clear(list);
    let current = null;
    for (const e of entries) {
      const d = String(e.ts).slice(0, 10);
      if (d !== current) { current = d; list.appendChild(el('h2', {}, d)); }
      list.appendChild(entryCard(e, { highlight: e.id === focusId }));
    }
    if (!entries.length) list.appendChild(el('p', { class: 'empty' }, day ? 'Nothing written that day.' : 'Nothing written yet.'));
    olderBtn.hidden = !more;
  }

  async function reload(size = day ? 500 : PAGE) {
    filter = '';
    filterInput.value = '';
    const mine = ++seq;
    msg.textContent = '';
    try {
      const rows = await api.get(url({ limit: size }));
      if (dead || mine !== seq) return;
      entries = rows;
      more = !day && rows.length >= size;
      draw();
    } catch (e) {
      if (!dead) msg.textContent = `Couldn't load the journal: ${e.message}`;
    }
  }

  async function loadOlder() {
    if (!entries.length) return;
    msg.textContent = '';
    olderBtn.disabled = true;
    try {
      const rows = await api.get(url({ limit: PAGE, before: entries[entries.length - 1].id }));
      if (dead) return;
      entries = entries.concat(rows);
      more = rows.length >= PAGE;
      draw();
    } catch (e) {
      msg.textContent = `Couldn't load older entries: ${e.message}`;
    } finally {
      olderBtn.disabled = false;
    }
  }

  // The text filter uses /api/search only, so Claude is never called from here.
  async function runFilter() {
    const q = filterInput.value.trim();
    if (!q) { reload(); return; }
    filter = q;
    const mine = ++seq;
    msg.textContent = '';
    olderBtn.hidden = true;
    try {
      const hits = await api.get(`/api/search?q=${encodeURIComponent(q)}`);
      if (dead || mine !== seq) return;
      clear(list).appendChild(hitList(hits, async (h) => {
        // The note as it is now on disk, not the indexed copy.
        let text;
        try {
          const notes = await api.get(`/api/notes?prefix=${encodeURIComponent(h.ref.split('#')[0])}`);
          text = notes.length ? notes[0].text : 'That note was not found.';
        } catch (e) {
          text = `Couldn't open that note: ${e.message}`;
        }
        if (dead || mine !== seq) return;
        clear(list).appendChild(el('h2', {}, h.ref));
        list.appendChild(el('pre', { class: 'verbatim' }, text));
        list.appendChild(el('button', { type: 'button', onclick: () => runFilter() }, 'Back to results'));
      }));
    } catch (e) {
      msg.textContent = `Couldn't search: ${e.message}`;
    }
  }

  await reload();
  const hl = list.querySelector('.hl');
  if (hl && typeof hl.scrollIntoView === 'function') hl.scrollIntoView({ block: 'center' });
  if (focusId && !hl && !dead) msg.textContent = 'That entry was not found.';

  // New entries and filing results appear without a reload (not while a text search is on screen).
  for (const type of ['entry.saved', 'filing.done', 'filing.failed', 'ws.open']) {
    subs.push(api.onEvent(type, () => { if (!filter) reload(Math.max(PAGE, entries.length)); }));
  }
  return () => { dead = true; for (const s of subs.splice(0)) s(); };
}
