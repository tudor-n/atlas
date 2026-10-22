// Today (spec 3.2): To review, Overdue, Due today, Upcoming reminders, Next time, Waiting, To read, Open tasks,
// and Other open items (anything open that fits nowhere else, so nothing confirmed ever vanishes).
import * as api from '../api.js';
import { el, clear, fmtWhen } from '../dom.js';
import { confirmCard } from '../components/confirm-card.js';
import { readonlyReason } from '../drive.js';

const openCards = new Set();   // entry ids the owner expanded; survives re-renders
let reviewOpen = true;

const pad = (n) => String(n).padStart(2, '0');

// Local 09:00 of the day `days` from now, as a local ISO datetime (no timezone).
export function snoozeUntil(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T09:00`;
}

const SNOOZES = [['1', '1 day', 1], ['3', '3 days', 3], ['7', '1 week', 7]];

const NOTICES = {
  'setup-done': 'Setup was already completed; set the backup URL in Settings.',
};

export async function render(container, _params, query) {
  const destroyers = [];   // confirm cards on screen
  const subs = [];         // event subscriptions
  let dead = false;
  let loading = false;
  let again = false;
  let dirty = false;
  let readonly = '';       // read-only root: confirm and tick are disabled, with this as their title
  const blocked = () => readonly;

  const notice = query && NOTICES[query.get('notice')];
  if (notice) container.appendChild(el('p', { class: 'banner', role: 'status' }, notice));
  const root = el('div', {});
  container.appendChild(root);

  function typing() {
    const a = document.activeElement;
    return a && root.contains(a) && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT');
  }

  async function refresh() {
    if (dead) return;
    if (loading) { again = true; return; }
    if (typing()) { dirty = true; return; }  // don't wipe what the owner is typing
    loading = true;
    try {
      const [today, proposals, failed, st] = await Promise.all([
        api.get('/api/today'), api.get('/api/proposals'), api.get('/api/filings?status=failed'),
        api.get('/api/state')]);
      readonly = readonlyReason(st.drive);
      if (!dead) draw({ today, proposals, failed });
    } catch (e) {
      if (!dead) { clear(root).appendChild(el('p', { class: 'error', role: 'alert' }, `Couldn't load Today: ${e.message}`)); }
    } finally {
      loading = false;
      if (again) { again = false; refresh(); }
    }
  }

  root.addEventListener('focusout', (ev) => {
    if (ev.relatedTarget && root.contains(ev.relatedTarget)) return;  // moving to a button inside the view
    setTimeout(() => { if (dirty && !typing()) { dirty = false; refresh(); } }, 0);
  });

  function draw({ today, proposals, failed }) {
    for (const d of destroyers.splice(0)) d();
    clear(root);
    root.appendChild(el('h1', {}, 'Today'));
    root.appendChild(reviewSection(proposals, failed));
    root.appendChild(section('Overdue', today.overdue, itemList));
    root.appendChild(section('Due today', today.due_today, itemList));
    root.appendChild(section('Upcoming reminders', today.upcoming, itemList));
    root.appendChild(nextTimeSection(today.next_time));
    root.appendChild(section('Waiting', today.waiting, waitingList));
    root.appendChild(section('To read', today.to_read, itemList));
    root.appendChild(section('Open tasks', today.open_tasks, itemList));
    root.appendChild(section('Other open items', today.other || [], itemList));
  }

  // -- To review ----------------------------------------------------------------------------
  // groups: /api/proposals; failedRows: /api/filings?status=failed ({entry_id, error, ts, text}).
  function reviewSection(groups, failedRows) {
    const inGroups = new Set(groups.map((g) => g.entry_id));
    const failed = failedRows.filter((f) => !inGroups.has(f.entry_id));
    const total = groups.length + failed.length;
    const box = el('details', { class: 'section', open: reviewOpen || null,
      ontoggle: (ev) => { reviewOpen = ev.target.open; } },
      el('summary', {}, `To review (${total})`));
    if (total === 0) box.appendChild(el('p', { class: 'empty' }, 'Nothing to review.'));
    for (const g of groups) {
      const n = g.proposals.length;
      box.appendChild(reviewCard(g.entry_id, `${g.synopsis || g.text || g.entry_id} (${n} to confirm)`, g.text));
    }
    for (const f of failed) {
      const text = f.text || '';
      box.appendChild(reviewCard(f.entry_id, `Filing failed: ${text.slice(0, 100) || f.entry_id}`, text));
    }
    return box;
  }

  function reviewCard(entryId, label, text) {
    const d = el('details', { class: 'review', open: openCards.has(entryId) || null },
      el('summary', {}, label));
    let card = null;
    const mount = () => {
      if (card) return;
      card = confirmCard(entryId, { entryText: text, blocked });
      destroyers.push(() => card.destroy());
      d.appendChild(card);
    };
    d.addEventListener('toggle', () => {
      if (d.open) { openCards.add(entryId); mount(); } else { openCards.delete(entryId); }
    });
    if (d.open) mount();
    return d;
  }

  // -- item sections ------------------------------------------------------------------------
  function section(title, rows, build) {
    const box = el('section', {}, el('h2', {}, `${title}${rows.length ? ` (${rows.length})` : ''}`));
    if (rows.length === 0) box.appendChild(el('p', { class: 'empty' }, 'Nothing here.'));
    else box.appendChild(build(rows));
    return box;
  }

  const itemList = (rows) => el('ul', { class: 'items' }, rows.map((it) => itemRow(it)));
  const waitingList = (rows) => el('ul', { class: 'items' }, rows.map((w) => itemRow(w.item, { nudge: w.nudge })));

  function nextTimeSection(groups) {
    const keys = Object.keys(groups);
    const box = el('section', {}, el('h2', {}, 'Next time'));
    if (keys.length === 0) box.appendChild(el('p', { class: 'empty' }, 'Nothing here.'));
    for (const key of keys) {
      box.appendChild(el('h3', {}, el('a', { href: `#/project/${encodeURIComponent(key)}` }, key)));
      box.appendChild(itemList(groups[key]));
    }
    return box;
  }

  function itemRow(it, { nudge } = {}) {
    const err = el('div', { class: 'error', role: 'alert' });
    const doIt = async (patch) => {
      err.textContent = '';
      try {
        await api.patch(`/api/items/${encodeURIComponent(it.id)}`, patch);
        refresh();
      } catch (e) {
        err.textContent = `Couldn't update: ${e.message}`;
      }
    };
    const snooze = el('select', { 'aria-label': `Snooze: ${it.text}`,
      onchange: (ev) => {
        const preset = SNOOZES.find((x) => x[0] === ev.target.value);
        ev.target.value = '';
        if (preset) doIt({ status: 'snoozed', snooze_until: snoozeUntil(preset[2]) });
      } },
      el('option', { value: '' }, 'Snooze…'),
      SNOOZES.map(([v, label]) => el('option', { value: v }, label)));
    const meta = [it.area, it.project, it.person].filter(Boolean).join(' / ');
    const when = [it.due ? `due ${fmtWhen(it.due)}` : '', it.remind_at ? `remind ${fmtWhen(it.remind_at)}` : '',
      it.kind === 'waiting' ? `waiting since ${String(it.created).slice(0, 10)}` : ''].filter(Boolean).join(' · ');
    const reason = blocked();
    return el('li', { class: 'item' },
      el('button', { class: 'icon', type: 'button', 'aria-label': `Done: ${it.text}`, title: reason || 'Done',
        disabled: reason ? true : null, onclick: () => doIt({ status: 'done' }) }, '✓'),
      el('div', { class: 'item-main' },
        el('div', {}, it.text, nudge ? [' ', el('span', { class: 'nudge' }, 'nudge?')] : null),
        el('div', { class: 'meta' }, [meta, when].filter(Boolean).join(' · ')),
        err),
      snooze);
  }

  // -- live updates -------------------------------------------------------------------------
  for (const type of ['items.changed', 'filing.done', 'filing.failed', 'ws.open']) {
    subs.push(api.onEvent(type, () => refresh()));
  }
  await refresh();
  return () => { dead = true; for (const d of [...destroyers.splice(0), ...subs.splice(0)]) d(); };
}
