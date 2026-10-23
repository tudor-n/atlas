// Browse pages: deadlines, people (waiting / to read) and a project or course page. Read-only views over
// items and notes, drawn into the main column of browse-shell.js (which owns the rail and the routes).
// opts.map (the brain map, or null) gives names to slugs and tells a course from a project.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { sectionHead, label } from '../components/ui.js';
import { entryCard, noteText } from '../components/text.js';
import { dateCell, findPlace, localDay } from './browse-model.js';

const LIVE = new Set(['open', 'to_read', 'snoozed']);
const live = (items) => items.filter((i) => LIVE.has(i.status));
const KIND = { task: 'task', deadline: 'deadline', reminder: 'reminder', waiting: 'waiting', next_time: 'next time' };

// slug -> display name from the map's people, projects or courses (the slug itself when unknown).
function namer(map) {
  const names = new Map();
  for (const key of ['people', 'projects', 'courses']) {
    for (const x of (map && Array.isArray(map[key]) ? map[key] : [])) {
      if (x && x.slug) names.set(`${key}:${x.slug}`, String(x.name || x.slug));
    }
  }
  return {
    person: (s) => names.get(`people:${s}`) || s,
    place: (s) => names.get(`projects:${s}`) || names.get(`courses:${s}`) || s,
  };
}

// One 52px item row: a mono date column (amber when overdue), the text, a mono meta line.
function itemRow(it, ctx, { project = true } = {}) {
  const d = dateCell(it, ctx.today);
  const meta = [KIND[it.kind] || it.kind, it.area,
    project && it.project ? el('a', { href: `#/project/${encodeURIComponent(it.project)}` }, ctx.names.place(it.project)) : null,
    it.person ? ctx.names.person(it.person) : null,
    it.due && it.remind_at ? `remind ${dateCell({ remind_at: it.remind_at }, ctx.today).text}` : '',
    it.status === 'to_read' ? 'to read' : '', it.status === 'snoozed' ? 'snoozed' : '']
    .filter(Boolean);
  const metaNodes = [];
  meta.forEach((m, i) => { if (i) metaNodes.push(' · '); metaNodes.push(m); });
  const dcls = ['dt', d.overdue ? 'amb' : '', d.text === '–' ? 'f' : '', d.remind ? 'd' : ''].filter(Boolean).join(' ');
  return el('div', { class: 'row it' },
    el('span', { class: dcls, title: d.overdue ? 'overdue' : d.remind ? 'reminder' : it.due ? 'due' : null }, d.overdue ? el('span', { class: 'sq' }) : null, d.text),
    el('div', { class: 'grow' }, el('div', { class: 'tx' }, it.text), el('div', { class: 'mt' }, metaNodes)));
}

const itemList = (items, ctx, opts) => el('div', { class: 'items' }, items.map((it) => itemRow(it, ctx, opts)));

// Shared page scaffold: an optional title block, the error line, the body; load() fills the body and
// may throw.
async function page(container, head, load, opts = {}) {
  const body = el('div', { class: 'bbody' });
  const msg = el('p', { class: 'err bmsg', role: 'alert' });
  container.appendChild(el('div', { class: opts.cls ? `bpage ${opts.cls}` : 'bpage' }, head, msg, body));
  let dead = false;
  const ctx = { today: localDay(new Date()), names: namer(opts.map) };
  try {
    await load(body, () => dead, ctx);
  } catch (e) {
    msg.textContent = `Couldn't load this page: ${e.message}`;
  }
  return () => { dead = true; };
}

const panel = (title, count, ...children) =>
  el('section', { class: 'bpanel' }, sectionHead(title, count), ...children);

// -- #/project/<slug> (a project or a course) ------------------------------------------------
export function renderProject(container, params, _query, opts = {}) {
  const slug = params[0];
  if (!slug) { location.hash = '#/today'; return undefined; }
  const place = findPlace(opts.map, slug);
  const prefix = place.prefix;
  const head = el('header', { class: 'bh' },
    label(place.kind === 'course' ? 'Course' : 'Project'),
    el('h1', { class: 'ttl' }, place.name),
    el('span', { class: 'pfx' }, prefix));
  return page(container, head, async (body, isDead, ctx) => {
    const [items, notes, entries] = await Promise.all([
      api.get(`/api/items?project=${encodeURIComponent(slug)}`),
      api.get(`/api/notes?prefix=${encodeURIComponent(prefix)}`),
      api.get('/api/entries?limit=500')]);
    if (isDead()) return;

    const open = live(items);
    body.appendChild(panel('Open items', open.length,
      open.length ? itemList(open, ctx, { project: false }) : el('div', { class: 'empty' }, 'Nothing open.')));

    // NOTES: the list on the left, the open note on the right (the first one to start with).
    const viewer = el('div', { class: 'nview' });
    const rows = [];
    const show = (n, btn) => {
      for (const r of rows) { r.classList.toggle('cur', r === btn); r.setAttribute('aria-current', r === btn ? 'true' : 'false'); }
      clear(viewer).append(el('div', { class: 'nview-h' }, n.path), noteText(n.text));
    };
    for (const n of notes) {
      const btn = el('button', { type: 'button', class: 'row nrow', title: n.path, onclick: () => show(n, btn) },
        el('span', { class: 'grow' }, n.path.slice(prefix.length) || n.path));
      rows.push(btn);
    }
    if (notes.length) show(notes[0], rows[0]);
    body.appendChild(panel('Notes', notes.length, notes.length
      ? el('div', { class: 'nsplit' }, el('div', { class: 'nlist' }, rows), viewer)
      : el('div', { class: 'empty' }, 'No notes yet.')));

    const touches = (e) => e.filing && Array.isArray(e.filing.files) && e.filing.files.some(
      (f) => String(typeof f === 'string' ? f : (f && f.path) || '').startsWith(prefix));
    const mine = entries.filter(touches);
    body.appendChild(panel('Journal', mine.length,
      mine.length ? el('div', {}, mine.map((e) => entryCard(e))) : el('div', { class: 'empty' }, 'No journal entries filed here yet.'),
      entries.length >= 500 ? el('p', { class: 'bnote' }, 'Showing journal entries from the most recent 500; see Journal for older.') : null));
  }, { ...opts, cls: 'proj' });
}

// -- #/people --------------------------------------------------------------------------------
export function renderPeople(container, _params, _query, opts = {}) {
  return page(container, null, async (body, isDead, ctx) => {
    const items = live(await api.get('/api/items'))
      .filter((i) => i.kind === 'waiting' || i.status === 'to_read');
    if (isDead()) return;
    if (!items.length) {
      body.append(sectionHead('People', 0), el('div', { class: 'empty' }, 'Nobody is waiting on anything.'));
      return;
    }
    const groups = new Map();
    for (const it of items) {
      const key = it.person || '';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(it);
    }
    const keys = [...groups.keys()].sort((a, b) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)));
    for (const key of keys) {
      body.appendChild(panel(key ? ctx.names.person(key) : 'No person', groups.get(key).length,
        itemList(groups.get(key), ctx)));
    }
  }, opts);
}

// -- #/deadlines -----------------------------------------------------------------------------
export function renderDeadlines(container, _params, _query, opts = {}) {
  return page(container, null, async (body, isDead, ctx) => {
    // Dated means a due date or, failing that, a reminder date.
    const when = (i) => i.due || i.remind_at;
    const items = live(await api.get('/api/items')).filter(when)
      .sort((a, b) => (when(a) < when(b) ? -1 : when(a) > when(b) ? 1 : 0));
    if (isDead()) return;
    body.append(sectionHead('Deadlines', items.length),
      items.length ? itemList(items, ctx) : el('div', { class: 'empty' }, 'No dated items.'));
  }, opts);
}
