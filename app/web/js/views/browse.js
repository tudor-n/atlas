// Browse pages: deadlines, people (waiting / to read) and a project or course page, drawn into the main
// column of browse-shell.js (which owns the rail and the routes). Item rows are Today's (done, snooze);
// notes and journal entries are read-only.
// opts.map (the brain map, or null) gives names to slugs and tells a course from a project.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { button, sectionHead, label, seg } from '../components/ui.js';
import { entryCard, noteText } from '../components/text.js';
import { readonlyReason } from '../drive.js';
import { findPlace } from './browse-model.js';
import { SNOOZES, SNOOZE_NAMES, snoozeUntil, itemMeta, mapNames, nameIn, localDay } from './today-model.js';

const LIVE = new Set(['open', 'to_read', 'snoozed']);
const live = (items) => items.filter((i) => LIVE.has(i.status));

// The item whose SNOOZE line is open; kept across the page's refreshes (as on Today).
let snoozeFor = null;

// The meta line's parts, as Today draws them (today-model.itemMeta: names from the map, `due MM-DD`
// amber once late, `remind …` when there is no due date, `since …` for waiting), with the project as
// a link (left out on its own page), the kind where nothing else says it, and to read / snoozed.
function metaNodes(it, ctx, { project = true } = {}) {
  const parts = itemMeta(it, ctx.names, ctx.today, { place: false });
  const nodes = [];
  const span = (p) => el('span', { class: p.warn ? 'p amb' : 'p' }, p.text);
  let rest = parts;
  const place = project && it.project
    ? el('a', { href: `#/project/${encodeURIComponent(it.project)}` }, nameIn(ctx.names, 'project', it.project))
    : !it.project && it.area ? el('span', { class: 'p' }, nameIn(ctx.names, 'area', it.area)) : null;
  if (place) {
    nodes.push(place);
    if (it.person && parts.length) { nodes.push(' / ', span(parts[0])); rest = parts.slice(1); }
  }
  const more = [...rest,
    it.kind === 'next_time' ? { text: 'next time' } : null,
    it.status === 'to_read' ? { text: 'to read' } : null,
    it.status === 'snoozed' ? { text: it.snooze_until ? `snoozed to ${String(it.snooze_until).slice(5, 10)}` : 'snoozed' } : null]
    .filter(Boolean);
  for (const p of more) {
    if (nodes.length) nodes.push(' · ');
    nodes.push(span(it.kind === 'waiting' && /^since /.test(p.text) ? { ...p, text: `waiting ${p.text}` } : p));
  }
  return nodes;
}

// One item row, Today's (today.js itemRow): the done ring, the text and its meta line, SNOOZE with
// its 1 D / 3 D / 1 W line. Writes are disabled on a read-only root, with the reason as the title.
function itemRow(it, ctx, opts) {
  const err = el('div', { class: 'err it-err', role: 'alert' });
  const doIt = async (patch) => {
    err.textContent = '';
    try {
      await api.patch(`/api/items/${encodeURIComponent(it.id)}`, patch);
      if (snoozeFor === it.id) snoozeFor = null;
      ctx.refresh();
    } catch (e) {
      ring.classList.remove('done');
      err.textContent = `Couldn't update: ${e.message}`;
    }
  };
  const reason = ctx.readonly;
  const ring = el('button', { type: 'button', class: 'dn', 'aria-label': `Done: ${it.text}`,
    title: reason || 'Done', disabled: reason ? true : null,
    onclick: () => { ring.classList.add('done'); doIt({ status: 'done' }); } });
  ring.dataset.k = `done:${it.id}`;

  const meta = metaNodes(it, ctx, opts);
  const snoozeBtn = button('Snooze', { size: 40, title: reason || null, disabled: !!reason });
  snoozeBtn.dataset.k = `snz:${it.id}`;
  const pick = el('div', { class: 'snz-pick', hidden: true });
  pick.id = `bsnz-${it.id}`;
  snoozeBtn.setAttribute('aria-controls', pick.id);
  const setOpen = (open, { focus } = {}) => {
    snoozeFor = open ? it.id : (snoozeFor === it.id ? null : snoozeFor);
    snoozeBtn.classList.toggle('on', open);
    snoozeBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    clear(pick);
    pick.hidden = !open;
    if (!open) { if (focus) snoozeBtn.focus(); return; }
    const cells = seg(SNOOZES, null, (days) => doIt({ status: 'snoozed', snooze_until: snoozeUntil(days) }),
      { size: 40, label: `Snooze for: ${it.text}` });
    cells.querySelectorAll('button').forEach((b, i) => { b.title = `Snooze ${SNOOZE_NAMES[SNOOZES[i].value]}`; });
    pick.append(el('span', { class: 'lb' }, 'Snooze for'), cells);
    if (focus) cells.querySelector('button').focus();
  };
  pick.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { ev.preventDefault(); setOpen(false, { focus: true }); } });
  snoozeBtn.addEventListener('click', () => setOpen(!snoozeBtn.classList.contains('on'), { focus: true }));
  snoozeBtn.setAttribute('aria-expanded', 'false');
  if (snoozeFor === it.id && !reason) setOpen(true);

  return el('div', { class: 'row it' },
    ring,
    el('div', { class: 'grow' },
      el('div', { class: 't' }, it.text),
      meta.length ? el('div', { class: 'mt' }, meta) : null,
      err),
    el('div', { class: 'snz' }, snoozeBtn),
    pick);
}

const itemList = (items, ctx, opts) => el('div', { class: 'items' }, items.map((it) => itemRow(it, ctx, opts)));

// Shared page scaffold: an optional title block, the error line, the body. load(body, isDead, ctx)
// fills a fresh body and may throw; it runs again on items.changed (a done or snooze here, or
// anywhere else) and swaps the body in place, so the focus and the open SNOOZE line come back.
async function page(container, head, load, opts = {}) {
  let body = el('div', { class: 'bbody' });
  const msg = el('p', { class: 'err bmsg', role: 'alert' });
  const root = el('div', { class: opts.cls ? `bpage ${opts.cls}` : 'bpage' }, head, msg, body);
  container.appendChild(root);
  let dead = false, loading = false, again = false;
  const ctx = { today: localDay(), names: mapNames(opts.map), readonly: '', refresh: () => run() };
  async function run() {
    if (dead) return;
    if (loading) { again = true; return; }
    loading = true;
    try {
      const st = await api.get('/api/state').catch(() => null);
      ctx.readonly = readonlyReason(st && st.drive);
      ctx.today = localDay();
      const next = el('div', { class: 'bbody' });
      await load(next, () => dead, ctx);
      if (dead) return;
      const a = document.activeElement;
      const key = a && root.contains(a) ? a.dataset.k : null;
      msg.textContent = '';
      body.replaceWith(next);
      body = next;
      if (key) {
        const n = body.querySelector(`[data-k="${CSS.escape(key)}"]`);
        if (n) n.focus({ preventScroll: true });
      }
    } catch (e) {
      if (!dead) msg.textContent = `Couldn't load this page: ${e.message}`;
    } finally {
      loading = false;
      if (again) { again = false; run(); }
    }
  }
  await run();
  const offs = ['items.changed', 'ws.open'].map((t) => api.onEvent(t, () => run()));
  return () => { dead = true; for (const off of offs) off(); };
}

const panel = (title, count, ...children) =>
  el('section', { class: 'bpanel' }, sectionHead(title, count), ...children);

// -- #/project/<slug> (a project or a course) ------------------------------------------------
export function renderProject(container, params, _query, opts = {}) {
  const slug = params[0];
  if (!slug) { location.hash = '#/today'; return undefined; }
  const place = findPlace(opts.map, slug);
  const prefix = place.prefix;
  let openNote = null;  // the note in the viewer, kept when the page refreshes
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
      openNote = n.path;
      for (const r of rows) { r.classList.toggle('cur', r === btn); r.setAttribute('aria-current', r === btn ? 'true' : 'false'); }
      clear(viewer).append(el('div', { class: 'nview-h' }, n.path), noteText(n.text));
    };
    for (const n of notes) {
      const btn = el('button', { type: 'button', class: 'row nrow', title: n.path, onclick: () => show(n, btn) },
        el('span', { class: 'grow' }, n.path.slice(prefix.length) || n.path));
      btn.dataset.k = `note:${n.path}`;
      rows.push(btn);
    }
    if (notes.length) {
      const i = Math.max(0, notes.findIndex((n) => n.path === openNote));
      show(notes[i], rows[i]);
    }
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
    const pname = (k) => nameIn(ctx.names, 'person', k);
    const keys = [...groups.keys()].sort((a, b) => (a === '' ? 1 : b === '' ? -1 : pname(a).localeCompare(pname(b))));
    for (const key of keys) {
      body.appendChild(panel(key ? nameIn(ctx.names, 'person', key) : 'No person', groups.get(key).length,
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
