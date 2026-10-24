// Journal: the verbatim history, newest first, grouped by day under sticky headers. A 240px rail
// holds the text filter (local search only; first, so it shows above the fold), the day field (mono
// yyyy-mm-dd, ↑/↓ step a day) and the area filter (rows with counts, named from the brain map).
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { entryCard, hitList, noteText } from '../components/text.js';
import { button, sectionHead, row, emptyRow } from '../components/ui.js';
import { mapNames, nameIn, localDay } from './today-model.js';

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

// What the owner typed in the day field, as "YYYY-MM-DD": '' for empty (all days), null when it isn't
// a real calendar date. Takes "2026-10-24", "2026-1-5" and "20261024".
export function parseDay(text) {
  const t = String(text ?? '').trim();
  if (!t) return '';
  const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(t) || /^(\d{4})(\d{2})(\d{2})$/.exec(t);
  if (!m) return null;
  const y = +m[1], mo = +m[2], d = +m[3];
  const dt = new Date(Date.UTC(y, mo - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
  const pad = (n) => String(n).padStart(2, '0');
  return `${m[1]}-${pad(mo)}-${pad(d)}`;
}

// "YYYY-MM-DD" moved by n calendar days.
export function addDays(day, n) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || '');
  if (!m) return day;
  const dt = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] + n));
  return dt.toISOString().slice(0, 10);
}

// An area key from entryAreas() by its brain-map name: "projects/robot-arm" -> "Robot arm",
// "uni" -> "Uni". A folder the map doesn't know keeps its slug (a top folder gets a capital).
export function areaName(key, names) {
  const k = String(key || '');
  if (k.startsWith('projects/')) return nameIn(names, 'project', k.slice('projects/'.length));
  const n = nameIn(names, 'area', k);
  return n === k ? k.charAt(0).toUpperCase() + k.slice(1) : n;
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

  // -- the rail: FIND, DAY, AREA --------------------------------------------------------------
  let names = mapNames(null);
  const dayErr = el('p', { class: 'err j-dayerr', id: 'j-day-err', role: 'alert' });
  const dayInput = el('input', { id: 'j-day', type: 'text', class: 'h40', value: day, inputmode: 'numeric',
    maxlength: '10', autocomplete: 'off', spellcheck: 'false', placeholder: 'yyyy-mm-dd',
    'aria-label': 'Day, as year-month-day', 'aria-describedby': 'j-day-err',
    onchange: () => applyDay(), onkeydown: (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); applyDay(); }
      else if (ev.key === 'ArrowUp' || ev.key === 'ArrowDown') {
        ev.preventDefault();
        const from = parseDay(dayInput.value) || day || localDay();
        dayInput.value = addDays(from, ev.key === 'ArrowUp' ? 1 : -1);
        applyDay();
      } else if (ev.key === 'Escape' && dayInput.value !== day) { ev.preventDefault(); dayInput.value = day; showDayErr(''); }
    } });
  const showDayErr = (text) => { dayErr.textContent = text; dayInput.classList.toggle('bad', !!text); };
  function applyDay() {
    const d = parseDay(dayInput.value);
    if (d === null) { showDayErr('Use yyyy-mm-dd, a real date.'); return; }
    showDayErr('');
    dayInput.value = d;
    if (d !== day) { day = d; reload(); }
  }
  const allDays = button('All days', { size: 40, cls: 'j-all', onclick: () => { day = ''; dayInput.value = ''; showDayErr(''); reload(); } });
  const areaCount = el('span', { class: 'n' });
  const areaRows = el('div', { class: 'j-areas', role: 'group', 'aria-label': 'Area' });
  const filterInput = el('input', { id: 'j-q', type: 'search', class: 'h40', placeholder: 'Words',
    'aria-label': 'Find text' });
  const findForm = el('form', { class: 'j-find', onsubmit: (ev) => { ev.preventDefault(); runFilter(); } },
    filterInput, button('Find', { size: 40, type: 'submit' }));
  const rail = el('aside', { class: 'rail j-rail', 'aria-label': 'Journal filters' },
    sectionHead('Find'),
    el('div', { class: 'j-findp' }, findForm, el('p', { class: 'hn j-findn' }, 'Local search only, never Claude.')),
    sectionHead('Day'),
    el('div', { class: 'j-day' }, dayInput, dayErr,
      el('p', { class: 'hn j-dayn' }, '↑ ↓ step a day'), allDays),
    el('div', { class: 'sect-h' }, el('h2', { class: 'lb' }, 'Area'), areaCount),
    areaRows);

  // -- the main pane --------------------------------------------------------------------------
  const msg = el('div', { class: 'j-msg err', role: 'alert' });
  const list = el('div', { class: 'j-list' });
  const olderBtn = button('Load older', { cls: 'j-older', onclick: () => loadOlder() });
  const olderRow = el('div', { class: 'j-olderp', hidden: true }, olderBtn);
  const main = el('div', { class: 'main j-main' }, msg, list, olderRow);
  container.appendChild(el('div', { class: 'vw split journal' }, rail, main));

  // The map's names for the AREA rows (slugs until it answers, or when it can't be read).
  const loadNamesFirst = () => api.get('/api/map').then((m) => { names = mapNames(m); }, () => {});

  const url = (extra) => `/api/entries?${new URLSearchParams({ ...(day ? { day } : {}), ...extra })}`;

  // Area rows come from the loaded entries; the filter is applied here, client-side.
  function drawAreas() {
    const counts = areaCounts(entries, area);
    areaCount.textContent = String(counts.length);
    const pick = (a) => () => { area = a; draw(); };
    clear(areaRows).appendChild(row([el('span', { class: 'grow' }, 'All areas'), el('span', { class: 'j-c' }, String(entries.length))],
      { current: !area, onclick: pick('') }));
    for (const [k, n] of counts) {
      areaRows.appendChild(row([el('span', { class: 'grow j-an' }, areaName(k, names)), el('span', { class: 'j-c' }, String(n))],
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
      const why = area ? `Nothing filed under ${areaName(area, names)} among the entries loaded${more ? ' so far' : ''}.`
        : day ? 'Nothing written that day.' : 'Nothing written yet.';
      list.appendChild(emptyRow(why));
    }
    olderRow.hidden = !more;
  }

  // keepFind: a background refresh leaves the FIND field as it is (the owner may be typing in it).
  async function reload(size = day ? 500 : PAGE, { keepFind = false } = {}) {
    filter = '';
    if (!keepFind) filterInput.value = '';
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

  await Promise.all([loadNamesFirst(), reload()]);
  if (!dead) drawAreas();
  const hl = list.querySelector('.hl');
  if (hl && typeof hl.scrollIntoView === 'function') hl.scrollIntoView({ block: 'center' });
  if (focusId && !hl && !dead) msg.textContent = 'That entry was not found.';

  // New entries and filing results appear without a reload (not while a text search is on screen).
  // While a rail field has the focus the refresh waits until it is left, and it never empties FIND.
  let pending = false;
  const typing = () => {
    const a = document.activeElement;
    return !!a && rail.contains(a) && a.tagName === 'INPUT';
  };
  const refresh = () => {
    if (dead || filter) return;
    if (typing()) { pending = true; return; }
    pending = false;
    reload(Math.max(PAGE, entries.length), { keepFind: true });
  };
  rail.addEventListener('focusout', (ev) => {
    if (ev.relatedTarget && rail.contains(ev.relatedTarget) && ev.relatedTarget.tagName === 'INPUT') return;
    setTimeout(() => { if (pending && !typing()) refresh(); }, 0);
  });
  for (const type of ['entry.saved', 'filing.done', 'filing.failed', 'ws.open']) {
    subs.push(api.onEvent(type, refresh));
  }
  const loadNames = () => api.get('/api/map').then((m) => { if (!dead) { names = mapNames(m); drawAreas(); } }, () => {});
  subs.push(api.onEvent('map.changed', loadNames));
  return () => { dead = true; for (const s of subs.splice(0)) s(); };
}
