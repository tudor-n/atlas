// Today (spec 3.2; phase 4.5 design §4). Two columns from 1100px: on the left TO REVIEW as master
// and detail (a queue of entries, the selected one's confirm card below it; ↑/↓ move through the
// queue), then OVERDUE, DUE TODAY, UPCOMING, OPEN TASKS and OTHER (anything open that fits nowhere
// else, so nothing confirmed ever vanishes); on the right NEXT TIME (by project), WAITING, TO READ.
// Pure helpers: today-model.js. Styles: css/views/today.css.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { confirmCard } from '../components/confirm-card.js';
import { button, dot, notice as noticePanel, sectionHead, seg, emptyRow } from '../components/ui.js';
import { readonlyReason } from '../drive.js';
import {
  SNOOZES, SNOOZE_NAMES, snoozeUntil, entryStamp, reviewQueue, pickSelection, moveSelection,
  mapNames, projectSlugs, itemMeta, localDay,
} from './today-model.js';

export { snoozeUntil };

// Survive re-renders and visits: the selected review entry (and its place), the open snooze.
let selectedId = null;
let selectedIndex = 0;
let snoozeFor = null;

const NOTICES = {
  'setup-done': 'Setup was already completed; set the backup URL in Settings.',
};

export async function render(container, _params, query) {
  const subs = [];         // event subscriptions
  let dead = false;
  let loading = false;
  let again = false;
  let dirty = false;
  let readonly = '';       // read-only root: writes are disabled, with this as their title
  const blocked = () => readonly;
  let names = {};
  let linkable = null;

  // The selected entry's card is kept across redraws (a refresh never flashes "Loading…" or drops
  // what the card holds); it is replaced only when the selection moves.
  let card = null;
  let cardId = null;
  let cardQ = null;  // the queue row the card was opened from
  const dropCard = () => { if (card) { card.destroy(); card.remove(); } card = null; cardId = null; cardQ = null; };
  const cardFor = (q) => {
    if (card && cardId === q.id) return card;
    dropCard();
    card = confirmCard(q.id, { entryText: q.text, blocked, onChange: () => refresh() });
    cardId = q.id;
    cardQ = q;
    return card;
  };

  const view = el('div', { class: 'vw split today' });
  const main = el('div', { class: 'main' });
  const side = el('div', { class: 'side w400' });
  const note = query && NOTICES[query.get('notice')];
  view.append(main, side);
  container.appendChild(view);

  function typing() {
    const a = document.activeElement;
    return a && view.contains(a) && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT' || a.tagName === 'SELECT');
  }

  async function refresh() {
    if (dead) return;
    if (loading) { again = true; return; }
    if (typing()) { dirty = true; return; }  // don't wipe what the owner is typing
    loading = true;
    try {
      const [today, proposals, failed, st, map] = await Promise.all([
        api.get('/api/today'), api.get('/api/proposals'), api.get('/api/filings?status=failed'),
        api.get('/api/state'), api.get('/api/map').catch(() => null)]);
      readonly = readonlyReason(st.drive);
      names = mapNames(map);
      linkable = projectSlugs(map);
      if (!dead) draw({ today, proposals, failed });
    } catch (e) {
      if (!dead) {
        clear(main).appendChild(el('div', { class: 'pane' }, el('p', { class: 'err', role: 'alert' }, `Couldn't load Today: ${e.message}`)));
        clear(side);
      }
    } finally {
      loading = false;
      if (again) { again = false; refresh(); }
    }
  }

  view.addEventListener('focusout', (ev) => {
    if (ev.relatedTarget && view.contains(ev.relatedTarget)) return;  // moving to a button inside the view
    setTimeout(() => { if (dirty && !typing()) { dirty = false; refresh(); } }, 0);
  });

  function draw({ today, proposals, failed }) {
    // Where the keyboard was: the queue's current row, or a control with a key, gets it back.
    const a = document.activeElement;
    const inQueue = a && a.closest && a.closest('.rq');
    const inCard = !inQueue && a && card && card.contains(a) ? a : null;  // the card is kept: refocus the node
    const key = !inQueue && !inCard && a && view.contains(a) ? a.dataset.k : null;
    clear(main);
    clear(side);
    if (note) main.appendChild(el('div', { class: 'tdy-note' }, noticePanel(note)));
    main.appendChild(reviewSection(proposals, failed));
    main.appendChild(section('Overdue', today.overdue));
    main.appendChild(section('Due today', today.due_today));
    main.appendChild(section('Upcoming', today.upcoming));
    main.appendChild(section('Open tasks', today.open_tasks));
    if ((today.other || []).length) main.appendChild(section('Other', today.other));
    side.appendChild(nextTimeSection(today.next_time || {}));
    side.appendChild(section('Waiting', today.waiting, (w) => itemRow(w.item, { nudge: w.nudge })));
    side.appendChild(section('To read', today.to_read));
    if (inQueue) {
      const cur = main.querySelector('.rq .row.cur');
      if (cur) cur.focus({ preventScroll: true });
    } else if (inCard) {
      if (inCard.isConnected) inCard.focus({ preventScroll: true });
    } else if (key) {
      const n = view.querySelector(`[data-k="${CSS.escape(key)}"]`);
      if (n) n.focus({ preventScroll: true });
    }
  }

  // -- To review: the queue and the selected entry's card ------------------------------------
  // groups: /api/proposals; failedRows: /api/filings?status=failed ({entry_id, error, ts, text}).
  function reviewSection(groups, failedRows) {
    const queue = reviewQueue(groups, failedRows);
    // An entry being filed again (RETRY, RE-FILE) is in neither list until it is done: while its
    // card is open it keeps its row, so the card doesn't vanish under the owner's eyes.
    if (card && cardQ && !queue.some((q) => q.id === cardId) && card.refiling()) {
      queue.splice(Math.min(selectedIndex, queue.length), 0, { ...cardQ, failed: false, filing: true });
    }
    selectedId = pickSelection(queue, selectedId, selectedIndex);
    const box = el('section', { class: 'sec rv', 'aria-label': 'To review' }, sectionHead('To review', queue.length));
    if (!queue.length) {
      dropCard();
      box.appendChild(el('div', { class: 'empty rv-none' }, dot('g'), 'Nothing to review'));
      return box;
    }
    const slot = el('div', { class: 'rv-card' });
    const rows = new Map();
    const select = (id, { focus } = {}) => {
      selectedId = id;
      selectedIndex = Math.max(0, queue.findIndex((q) => q.id === id));
      for (const [rid, r] of rows) {
        const on = rid === id;
        r.classList.toggle('cur', on);
        r.setAttribute('aria-current', on ? 'true' : 'false');
        r.tabIndex = on ? 0 : -1;
      }
      slot.replaceChildren(cardFor(queue[selectedIndex]));
      if (focus) rows.get(id).focus({ preventScroll: false });
    };
    const list = el('div', { class: 'rq', role: 'group', 'aria-label': 'Entries to review (arrow keys move)',
      onkeydown: (ev) => {
        if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
        const to = moveSelection(queue, selectedId, ev.key);
        if (!to) return;
        ev.preventDefault();
        if (to !== selectedId) select(to, { focus: true });
      } });
    const today = localDay();
    for (const q of queue) {
      const r = el('button', { type: 'button', class: q.failed ? 'row q fl' : 'row q',
        title: q.failed ? `Filing failed: ${q.error}`.slice(0, 300) : null,
        onclick: () => select(q.id) },
        q.failed ? el('span', { class: 'sq' }) : el('span', { class: 'sq0', 'aria-hidden': 'true' }),
        el('span', { class: 'm n' }, q.failed || q.filing ? '–' : String(q.count)),
        el('span', { class: 'sr' }, q.failed ? 'Filing failed: ' : q.filing ? 'Filing: ' : q.count === 1 ? 'proposal: ' : 'proposals: '),
        el('span', { class: 'grow syn' }, q.synopsis),
        el('span', { class: 'm dt' }, entryStamp(q.id, today)));
      rows.set(q.id, r);
      list.appendChild(r);
    }
    box.append(list, slot);
    select(selectedId);
    return box;
  }

  // -- item sections ------------------------------------------------------------------------
  function section(title, rows, build = (it) => itemRow(it)) {
    const list = rows || [];
    return el('section', { class: 'sec', 'aria-label': title }, sectionHead(title, list.length),
      list.length ? list.map(build) : emptyRow('None'));
  }

  function nextTimeSection(groups) {
    const keys = Object.keys(groups);
    const total = keys.reduce((n, k) => n + groups[k].length, 0);
    const box = el('section', { class: 'sec', 'aria-label': 'Next time' }, sectionHead('Next time', total));
    if (!keys.length) box.appendChild(emptyRow('None'));
    for (const key of keys) {
      const name = names[key] || key;
      const link = !linkable || linkable.has(key);
      box.appendChild(el('h3', { class: 'grp' }, link
        ? el('a', { href: `#/project/${encodeURIComponent(key)}`, title: `Open ${name}` }, name)
        : el('span', {}, name)));
      for (const it of groups[key]) box.appendChild(itemRow(it, { inGroup: true }));
    }
    return box;
  }

  function itemRow(it, { nudge, inGroup } = {}) {
    const err = el('div', { class: 'err it-err', role: 'alert' });
    const doIt = async (patch) => {
      err.textContent = '';
      try {
        await api.patch(`/api/items/${encodeURIComponent(it.id)}`, patch);
        if (snoozeFor === it.id) snoozeFor = null;
        refresh();
      } catch (e) {
        ring.classList.remove('done');
        err.textContent = `Couldn't update: ${e.message}`;
      }
    };
    const reason = blocked();
    const ring = el('button', { type: 'button', class: 'dn', 'aria-label': `Done: ${it.text}`,
      title: reason || 'Done', disabled: reason ? true : null,
      onclick: () => { ring.classList.add('done'); doIt({ status: 'done' }); } });
    ring.dataset.k = `done:${it.id}`;

    const meta = itemMeta(it, names, localDay(), { place: !inGroup });
    const metaLine = meta.length ? el('div', { class: 'mt' }, meta.flatMap((p, i) => [
      i ? ' · ' : null, el('span', { class: p.warn ? 'p amb' : 'p' }, p.text)]).filter((x) => x !== null)) : null;

    // SNOOZE opens an inline 1 D / 3 D / 1 W on a line of its own under the text; Esc (or SNOOZE
    // again) folds it.
    const snoozeBtn = button('Snooze', { size: 40, title: reason || null, disabled: !!reason });
    snoozeBtn.dataset.k = `snz:${it.id}`;
    const pick = el('div', { class: 'snz-pick', hidden: true });
    pick.id = `snz-${it.id}`;
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
        el('div', { class: 't' }, it.text, nudge ? el('span', { class: 'nudge', title: 'Waiting a while: nudge them?' }, 'Nudge?') : null),
        metaLine,
        err),
      el('div', { class: 'snz' }, snoozeBtn),
      pick);
  }

  // -- live updates -------------------------------------------------------------------------
  for (const type of ['items.changed', 'filing.started', 'filing.done', 'filing.failed', 'ws.open']) {
    subs.push(api.onEvent(type, () => refresh()));
  }
  await refresh();
  return () => { dead = true; dropCard(); for (const d of subs.splice(0)) d(); };
}
