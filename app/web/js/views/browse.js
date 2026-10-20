// Browse: a project page, people (waiting / to read) and deadlines. Read-only views over items and notes.
import * as api from '../api.js';
import { el, clear, fmtWhen } from '../dom.js';
import { entryCard } from '../components/text.js';

const LIVE = new Set(['open', 'to_read', 'snoozed']);
const live = (items) => items.filter((i) => LIVE.has(i.status));
const KIND = { task: 'Task', deadline: 'Deadline', reminder: 'Reminder', waiting: 'Waiting', next_time: 'Next time' };

function itemRow(it) {
  const meta = [KIND[it.kind] || it.kind, it.area,
    it.project ? el('a', { href: `#/project/${encodeURIComponent(it.project)}` }, it.project) : null,
    it.person, it.due ? `due ${fmtWhen(it.due)}` : '', it.remind_at ? `remind ${fmtWhen(it.remind_at)}` : '',
    it.status === 'to_read' ? 'to read' : '', it.status === 'snoozed' ? 'snoozed' : '']
    .filter(Boolean);
  const metaNodes = [];
  meta.forEach((m, i) => { if (i) metaNodes.push(' · '); metaNodes.push(m); });
  return el('li', { class: 'item' },
    el('div', { class: 'item-main' }, el('div', {}, it.text), el('div', { class: 'meta' }, metaNodes)));
}

const itemList = (items) => el('ul', { class: 'items' }, items.map(itemRow));

// Shared page scaffold: heading, error line, body; load() fills the body and may throw.
async function page(container, title, load) {
  const body = el('div', {});
  const msg = el('div', { class: 'error', role: 'alert' });
  container.appendChild(el('div', {}, el('h1', {}, title), msg, body));
  let dead = false;
  try {
    await load(body, () => dead);
  } catch (e) {
    msg.textContent = `Couldn't load this page: ${e.message}`;
  }
  return () => { dead = true; };
}

const section = (title, count, ...children) =>
  el('section', {}, el('h2', {}, count === null ? title : `${title} (${count})`), ...children);

// -- #/project/<slug> ----------------------------------------------------------------------
export function renderProject(container, params) {
  const slug = params[0];
  if (!slug) { location.hash = '#/today'; return undefined; }
  const prefix = `projects/${slug}/`;
  return page(container, `Project: ${slug}`, async (body, isDead) => {
    const [items, notes, entries] = await Promise.all([
      api.get(`/api/items?project=${encodeURIComponent(slug)}`),
      api.get(`/api/notes?prefix=${encodeURIComponent(prefix)}`),
      api.get('/api/entries?limit=500')]);
    if (isDead()) return;

    const open = live(items);
    body.appendChild(section('Open items', open.length,
      open.length ? itemList(open) : el('p', { class: 'empty' }, 'Nothing open.')));

    const viewer = el('div', {});
    const noteList = el('ul', { class: 'items' }, notes.map((n) => el('li', { class: 'item' },
      el('button', { type: 'button', class: 'linkish', onclick: () => {
        clear(viewer).appendChild(el('h3', {}, n.path));
        viewer.appendChild(el('pre', { class: 'verbatim' }, n.text));
      } }, n.path.slice(prefix.length) || n.path))));
    body.appendChild(section('Notes', notes.length,
      notes.length ? noteList : el('p', { class: 'empty' }, 'No notes yet.'), viewer));

    const touches = (e) => e.filing && Array.isArray(e.filing.files) && e.filing.files.some(
      (f) => String(typeof f === 'string' ? f : (f && f.path) || '').startsWith(prefix));
    const mine = entries.filter(touches);
    body.appendChild(section('Journal entries', mine.length,
      mine.length ? el('div', {}, mine.map((e) => entryCard(e))) : el('p', { class: 'empty' }, 'No journal entries filed here yet.'),
      entries.length >= 500 ? el('p', { class: 'meta' }, 'Showing journal entries from the most recent 500; see Journal for older.') : null));
  });
}

// -- #/people ------------------------------------------------------------------------------
export function renderPeople(container) {
  return page(container, 'People', async (body, isDead) => {
    const items = live(await api.get('/api/items'))
      .filter((i) => i.kind === 'waiting' || i.status === 'to_read');
    if (isDead()) return;
    if (!items.length) { body.appendChild(el('p', { class: 'empty' }, 'Nobody is waiting on anything.')); return; }
    const groups = new Map();
    for (const it of items) {
      const key = it.person || '';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(it);
    }
    const keys = [...groups.keys()].sort((a, b) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)));
    for (const key of keys) {
      body.appendChild(section(key || 'No person', groups.get(key).length, itemList(groups.get(key))));
    }
  });
}

// -- #/deadlines ---------------------------------------------------------------------------
export function renderDeadlines(container) {
  return page(container, 'Deadlines', async (body, isDead) => {
    const items = live(await api.get('/api/items')).filter((i) => i.due)
      .sort((a, b) => (a.due < b.due ? -1 : a.due > b.due ? 1 : 0));
    if (isDead()) return;
    body.appendChild(items.length ? itemList(items) : el('p', { class: 'empty' }, 'No dated items.'));
  });
}
