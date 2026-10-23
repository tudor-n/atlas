// Journal: the verbatim history, newest first, grouped by day under sticky headers. A 240px rail
// holds the day picker, the area filter (rows with counts) and the text filter (local search only).
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { entryCard, hitList, noteText } from '../components/text.js';
import { button, sectionHead, row, emptyRow } from '../components/ui.js';

const PAGE = 50;
const WEEKDAYS = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];

// "e-20261020-143000-ab12" -> "2026-10-20"
const dayOfId = (id) => {
  const m = /^e-(\d{4})(\d{2})(\d{2})-/.exec(id || '');
  return m ? `${m[1]}-${m[2]}-${m[3]}` : '';
};

// "2026-10-24" -> "2026-10-24 · SAT" (the weekday of that calendar date, whatever the time zone).
export function dayLabel(day) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || '');
  if (!m) return String(day || '');
  const wd = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay();
  return Number.isNaN(wd) ? day : `${day} · ${WEEKDAYS[wd]}`;
}

// The areas an entry was filed into, from its filing's files: the top folder, or projects/<slug>.
export function entryAreas(entry) {
  const f = entry && entry.filing;
  const files = f && Array.isArray(f.files) ? f.files : [];
  const out = new Set();
  for (const x of files) {
    const path = typeof x === 'string' ? x : (x && x.path) || '';
    const parts = path.split(/[\\/]/).filter(Boolean);
    if (!parts.length) continue;
    out.add(parts[0] === 'projects' && parts.length > 2 ? `projects/${parts[1]}` : parts[0]);
  }
  return [...out].sort();
}

// [[area, count]] over the loaded entries, sorted by area; `keep` (the selected area) stays listed
// with 0 when no loaded entry has it.
export function areaCounts(entries, keep = '') {
  const counts = new Map();
  for (const e of entries) for (const a of entryAreas(e)) counts.set(a, (counts.get(a) || 0) + 1);
  if (keep && !counts.has(keep)) counts.set(keep, 0);
  return [...counts].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
}

export async function render(container, _params, query) {
  const focusId = (query && query.get('entry')) || '';
  let day = dayOfId(focusId);
  let entries = [];
  let more = false;
  let filter = '';
  let area = '';
  let dead = false;
  let seq = 0;
  const subs = [];

  // -- the rail: DAY, AREA, FIND --------------------------------------------------------------
  const dayInput = el('input', { id: 'j-day', type: 'date', class: 'h40', value: day, 'aria-label': 'Day',
    onchange: () => { day = dayInput.value; reload(); } });
  const allDays = button('All days', { size: 40, cls: 'j-all', onclick: () => { day = ''; dayInput.value = ''; reload(); } });
  const areaCount = el('span', { class: 'n' });
  const areaRows = el('div', { class: 'j-areas', role: 'group', 'aria-label': 'Area' });
  const filterInput = el('input', { id: 'j-q', type: 'search', class: 'h40', placeholder: 'Words',
    'aria-label': 'Find text' });
  const findForm = el('form', { class: 'j-find', onsubmit: (ev) => { ev.preventDefault(); runFilter(); } },
    filterInput, button('Find', { size: 40, type: 'submit' }));
  const rail = el('aside', { class: 'rail j-rail', 'aria-label': 'Journal filters' },
    sectionHead('Day'),
    el('div', { class: 'j-day' }, dayInput, allDays),
    el('div', { class: 'sect-h' }, el('h2', { class: 'lb' }, 'Area'), areaCount),
    areaRows,
    sectionHead('Find'),
    el('div', { class: 'j-findp' }, findForm, el('p', { class: 'hn j-findn' }, 'Local search only, never Claude.')));

  // -- the main pane --------------------------------------------------------------------------
  const msg = el('div', { class: 'j-msg err', role: 'alert' });
  const list = el('div', { class: 'j-list' });
  const olderBtn = button('Load older', { cls: 'j-older', onclick: () => loadOlder() });
  const olderRow = el('div', { class: 'j-olderp', hidden: true }, olderBtn);
  const main = el('div', { class: 'main j-main' }, msg, list, olderRow);
  container.appendChild(el('div', { class: 'vw split journal' }, rail, main));

  const url = (extra) => `/api/entries?${new URLSearchParams({ ...(day ? { day } : {}), ...extra })}`;

  // Area rows come from the loaded entries; the filter is applied here, client-side.
  function drawAreas() {
    const counts = areaCounts(entries, area);
    areaCount.textContent = String(counts.length);
    const pick = (a) => () => { area = a; draw(); };
    clear(areaRows).appendChild(row([el('span', { class: 'grow' }, 'All areas'), el('span', { class: 'j-c' }, String(entries.length))],
      { current: !area, onclick: pick('') }));
    for (const [k, n] of counts) {
      areaRows.appendChild(row([el('span', { class: 'grow j-an' }, k), el('span', { class: 'j-c' }, String(n))],
        { current: k === area, onclick: pick(k), title: k }));
    }
  }

  function draw() {
    drawAreas();
    clear(list);
    const shown = area ? entries.filter((e) => entryAreas(e).includes(area)) : entries;
    let group = null;
    let count = null;
    let n = 0;
    for (const e of shown) {
      const d = String(e.ts).slice(0, 10);
      if (!group || group.dataset.day !== d) {
        if (count) count.textContent = String(n);
        n = 0;
        count = el('span', { class: 'n' });
        group = el('section', { class: 'j-dayg', 'data-day': d },
          el('h2', { class: 'j-dayh' }, el('span', {}, dayLabel(d)), count));
        list.appendChild(group);
      }
      n++;
      group.appendChild(entryCard(e, { highlight: e.id === focusId, withDate: false }));
    }
    if (count) count.textContent = String(n);
    if (!shown.length) {
      const why = area ? `Nothing filed under ${area} among the entries loaded${more ? ' so far' : ''}.`
        : day ? 'Nothing written that day.' : 'Nothing written yet.';
      list.appendChild(emptyRow(why));
    }
    olderRow.hidden = !more;
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
    olderRow.hidden = true;
    try {
      const hits = await api.get(`/api/search?q=${encodeURIComponent(q)}`);
      if (dead || mine !== seq) return;
      const results = () => {
        clear(list).appendChild(el('div', { class: 'j-resh' },
          el('span', { class: 'lb' }, 'Find'), el('span', { class: 'j-q' }, q),
          el('span', { class: 'n' }, String(hits.length)), el('span', { class: 'grow' }),
          button('Clear', { size: 40, onclick: () => reload() })));
        list.appendChild(hitList(hits, openHit));
        main.scrollTop = 0;
      };
      const openHit = async (h) => {
        // The note as it is now on disk, not the indexed copy.
        let text;
        try {
          const notes = await api.get(`/api/notes?prefix=${encodeURIComponent(h.ref.split('#')[0])}`);
          text = notes.length ? notes[0].text : 'That note was not found.';
        } catch (e) {
          text = `Couldn't open that note: ${e.message}`;
        }
        if (dead || mine !== seq) return;
        const back = button('‹ Back', { size: 40, onclick: () => results() });
        clear(list).appendChild(el('div', { class: 'j-resh' }, back, el('span', { class: 'j-ref' }, h.ref)));
        list.appendChild(el('div', { class: 'j-note' }, noteText(text)));
        main.scrollTop = 0;
        back.focus();
      };
      results();
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
