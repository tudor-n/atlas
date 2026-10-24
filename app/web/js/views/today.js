// Today (spec 3.2; phase 4.5 design §4; owner feedback 2026-10-24, T-P1…). Two columns from 1100px:
// on the left OVERDUE, DUE TODAY, UPCOMING, OPEN TASKS and OTHER (anything open that fits nowhere else,
// so nothing confirmed ever vanishes); on the right NEXT TIME (by project), WAITING, TO READ. Empty
// sections are left out. What waits for confirmation is not on the page: it is one line at the top
// ("7 entries to review", REVIEW) and a pop-up (today-review.js) that opens by itself when the queue
// holds an entry this viewer hasn't been shown yet, never while the owner is typing anywhere.
// Pure helpers: today-model.js. Styles: css/views/today.css.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { setReviewHandler, whenFree } from '../components/confirm-card.js';
import { button, notice as noticePanel, sectionHead, seg } from '../components/ui.js';
import { readonlyReason } from '../drive.js';
import {
  SNOOZES, SNOOZE_NAMES, snoozeUntil, mapNames, groupName, projectSlugs, itemMeta, localDay,
  REVIEW_SEEN_KEY, parseSeen, unseenIds, pruneSeen, shouldAutoOpen, reviewTarget, reviewNoun,
} from './today-model.js';
import { reviewDialog } from './today-review.js';

export { snoozeUntil };

// Survives re-renders and visits: the open snooze.
let snoozeFor = null;

// The entries this viewer has been shown in the pop-up: in memory, and in localStorage so a reload
// doesn't open it again for the same ones (try/catch: storage may be off or full).
let seen = new Set();
function loadSeen() {
  try { for (const id of parseSeen(localStorage.getItem(REVIEW_SEEN_KEY))) seen.add(id); } catch (_) { /* no storage */ }
}
function saveSeen() {
  try { localStorage.setItem(REVIEW_SEEN_KEY, JSON.stringify([...seen])); } catch (_) { /* no storage */ }
}

const TYPING = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
// The owner is typing somewhere in the app: the capture box, a tray card's field, a field on a view.
const typingAnywhere = () => {
  const a = document.activeElement;
  return !!(a && (TYPING.has(a.tagName) || a.isContentEditable));
};

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
  let names = mapNames(null);
  let linkable = null;
  let waitingToOpen = false;  // something new came while the owner was typing: open once they stop

  const view = el('div', { class: 'vw split today' });
  const main = el('div', { class: 'main', tabindex: '-1', 'aria-label': 'Today' });
  const side = el('div', { class: 'side w400' });
  const note = query && NOTICES[query.get('notice')];
  const target = reviewTarget(query);  // #/today?review=… (the top bar's REVIEW n, off Today)
  let reviewBtn = null;                // the line's REVIEW, while there is something to review

  const rv = reviewDialog({
    blocked,
    onChange: () => refresh(),
    onOpen: (ids) => {
      let added = false;
      for (const id of ids) if (!seen.has(id)) { seen.add(id); added = true; }
      if (added) saveSeen();
    },
    // Back on Today: to what had the keyboard before, else the line's REVIEW, else the column.
    onClosed: (opener) => {
      if (dead) return;
      (opener || (reviewBtn && reviewBtn.isConnected ? reviewBtn : main)).focus({ preventScroll: true });
    },
  });
  view.append(main, side, rv.node);
  container.appendChild(view);

  function typing() {
    const a = document.activeElement;
    return a && view.contains(a) && TYPING.has(a.tagName);
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

  // -- the pop-up opens by itself for something new (T-P2) -----------------------------------
  function maybeOpen() {
    if (dead) return;
    loadSeen();  // another window of the app may have shown some meanwhile
    const unseen = unseenIds(rv.ids(), seen);
    if (!shouldAutoOpen({ open: rv.isOpen(), typing: false, readonly, unseen: unseen.length })) { waitingToOpen = false; return; }
    if (typingAnywhere()) { waitingToOpen = true; return; }  // the focusout below tries again
    waitingToOpen = false;
    rv.open({ id: unseen[0] });  // on the first new entry
  }
  // Leaving a field anywhere (the capture box too): after the press that moved the focus has landed
  // (a pop-up at pointer-down would take the click), and only when the focus didn't go to another field.
  const onFocusOut = () => { if (waitingToOpen) whenFree(maybeOpen); };
  document.addEventListener('focusout', onFocusOut, true);
  subs.push(() => document.removeEventListener('focusout', onFocusOut, true));

  function draw({ today, proposals, failed }) {
    // Where the keyboard was on the page (not in the pop-up, which keeps its own): a control with a key.
    const a = document.activeElement;
    const key = a && (main.contains(a) || side.contains(a)) ? a.dataset.k : null;
    const n = rv.update(proposals, failed);
    // Forget the entries that left the queue, so the stored list stays small.
    const kept = pruneSeen(seen, rv.ids());
    if (kept.length !== seen.size) { seen = new Set(kept); saveSeen(); }
    clear(main);
    clear(side);
    reviewBtn = null;
    if (note) main.appendChild(el('div', { class: 'tdy-note' }, noticePanel(note)));
    if (n) main.appendChild(reviewLine(n));
    const lists = [['Overdue', today.overdue], ['Due today', today.due_today], ['Upcoming', today.upcoming],
      ['Open tasks', today.open_tasks], ['Other', today.other]].filter(([, l]) => (l || []).length);
    for (const [title, l] of lists) main.appendChild(section(title, l));
    if (!lists.length) main.appendChild(el('div', { class: 'empty tdy-none' }, 'Nothing due, nothing open.'));
    const nt = today.next_time || {};
    const sides = [];
    if (Object.keys(nt).length) sides.push(nextTimeSection(nt));
    if ((today.waiting || []).length) sides.push(section('Waiting', today.waiting, (w) => itemRow(w.item, { nudge: w.nudge })));
    if ((today.to_read || []).length) sides.push(section('To read', today.to_read));
    side.append(...sides);
    if (!sides.length) side.appendChild(el('div', { class: 'empty tdy-none' }, 'Nothing waiting, nothing to read.'));
    if (key) {
      const k = view.querySelector(`[data-k="${CSS.escape(key)}"]`);
      if (k) k.focus({ preventScroll: true });
    }
    whenFree(maybeOpen);
  }

  // -- the one review line: an amber square, "7 entries to review", REVIEW ---------------------
  function reviewLine(n) {
    reviewBtn = button('Review', { size: 40, onclick: () => rv.open() });
    reviewBtn.dataset.k = 'review';
    reviewBtn.setAttribute('aria-haspopup', 'dialog');
    return el('div', { class: 'row rv-line' },
      el('span', { class: 'sq' }),
      el('span', { class: 'grow' }, el('span', { class: 'm n' }, String(n)), ' ', reviewNoun(n)),
      reviewBtn);
  }

  // -- item sections ------------------------------------------------------------------------
  function section(title, rows, build = (it) => itemRow(it)) {
    const list = rows || [];
    return el('section', { class: 'sec', 'aria-label': title }, sectionHead(title, list.length), list.map(build));
  }

  function nextTimeSection(groups) {
    const keys = Object.keys(groups);
    const total = keys.reduce((n, k) => n + groups[k].length, 0);
    const box = el('section', { class: 'sec', 'aria-label': 'Next time' }, sectionHead('Next time', total));
    for (const key of keys) {
      const name = groupName(names, key);
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
    // A faint text button (the column's lightest control); it outlines on hover, focus and while open.
    const snoozeBtn = button('Snooze', { size: 40, cls: 'tx', title: reason || null, disabled: !!reason });
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

  // -- the tray's REVIEW and the top bar's REVIEW n, while Today is the current view -----------
  // The tray's REVIEW (an entry id) opens the pop-up on that entry, its first ✓ taking the keyboard,
  // instead of a second full card in the tray; an entry not in the queue (still filing) stays the
  // tray's. The top bar's REVIEW n (null) opens it as it was.
  subs.push(setReviewHandler((id) => {
    if (dead) return false;
    if (id === null) { rv.open(); return true; }
    if (!rv.has(id)) return false;
    rv.open({ id, focusCard: true });
    return true;
  }));

  // -- live updates -------------------------------------------------------------------------
  for (const type of ['items.changed', 'filing.started', 'filing.done', 'filing.failed', 'ws.open']) {
    subs.push(api.onEvent(type, () => refresh()));
  }
  loadSeen();
  await refresh();
  if (target && !dead) {
    // Asked for by the route (#/today?review=…): open it, and drop the ask from the address so a
    // reload doesn't ask again (no hashchange, so the view stays).
    const q = new URLSearchParams(query);
    q.delete('review');
    const rest = q.toString();
    history.replaceState(history.state, '', `#/today${rest ? `?${rest}` : ''}`);
    rv.open({ id: target.id, focusCard: !!target.id });
  }
  return () => { dead = true; rv.destroy(); for (const d of subs.splice(0)) d(); };
}
