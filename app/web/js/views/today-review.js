// Today's review pop-up (owner feedback 2026-10-24, decisions T-P1…): a modal <dialog> that shows only
// what is waiting for confirmation. A header (TO REVIEW n, ×), the queue of entries as a rail on the
// left (↑/↓/Home/End move through it) and the selected entry's confirm card as the main area, its
// action bar (TELL IT WHAT'S WRONG, CONFIRM ALL) fixed at the foot. Esc or × closes it; it closes by
// itself once the last entry is handled. Today owns the data (it calls update() on every redraw) and
// decides when it opens (today.js). Pure helpers: today-model.js. Styles: css/views/today.css.
import { el, clear } from '../dom.js';
import { confirmCard } from '../components/confirm-card.js';
import { button } from '../components/ui.js';
import { reviewQueue, pickSelection, moveSelection, entryStamp, localDay, scrollToShow } from './today-model.js';

// Survive redraws, closes and visits: the selected entry and its place in the queue.
let selectedId = null;
let selectedIndex = 0;

// Keep a queue row visible inside the rail's own scroll (.rvd-q is positioned: the rows' offset parent).
function keepRowInList(list, row) {
  const d = scrollToShow(row.offsetTop, row.offsetTop + row.offsetHeight, list.scrollTop, list.scrollTop + list.clientHeight);
  if (d) list.scrollTop += d;
}

// blocked(): why writes can't work now (read-only root), or ''. onChange(): a card changed something
// (Today refreshes). onOpen(ids): the pop-up shows these entries (Today marks them seen).
// onClosed(opener): it closed; opener is what had the keyboard before it opened (or null).
export function reviewDialog({ blocked, onChange, onOpen, onClosed }) {
  let groups = [];
  let failedRows = [];
  let queue = [];
  let rows = new Map();
  let opener = null;
  let escHandled = false;  // an Esc the card used itself (edit, correction box): it doesn't close

  // The selected entry's card, kept across redraws (C1-3): replaced only when the selection moves.
  let card = null;
  let cardId = null;
  let cardQ = null;
  const dropCard = () => { if (card) { card.destroy(); card.remove(); } card = null; cardId = null; cardQ = null; };
  // A kept card reloads (sequence-guarded, focus kept): a card still showing a proposal handled
  // elsewhere would send it with CONFIRM ALL (R-F1-6).
  const cardFor = (q) => {
    if (card && cardId === q.id) { card.reload(); return card; }
    dropCard();
    card = confirmCard(q.id, { entryText: q.text, blocked, onChange });
    cardId = q.id;
    cardQ = q;
    return card;
  };

  const count = el('span', { class: 'n' }, '0');
  const close = button('×', { size: 40, cls: 'x', title: 'Close (Esc)', onclick: () => dlg.close() });
  close.setAttribute('aria-label', 'Close the review');
  const list = el('div', { class: 'rvd-q', role: 'group', 'aria-label': 'Entries to review (arrow keys move)',
    onkeydown: (ev) => {
      if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
      const to = moveSelection(queue, selectedId, ev.key);
      if (!to) return;
      ev.preventDefault();
      if (to !== selectedId) select(to, { focus: true });
    } });
  const slot = el('div', { class: 'rvd-c' });
  const dlg = el('dialog', { class: 'rvd', 'aria-labelledby': 'rvd-t' },
    el('div', { class: 'rvd-p' },
      el('div', { class: 'rvd-h' },
        el('h2', { class: 'lb', id: 'rvd-t' }, 'To review'), count,
        el('span', { class: 'grow' }),
        el('span', { class: 'hn', 'aria-hidden': 'true' }, '↑ ↓ move · Esc closes'),
        close),
      el('div', { class: 'rvd-b' }, list, slot)));

  // Esc first goes to what the card has open (an edit, the correction box): those handle it and
  // prevent its default, and the pop-up stays.
  dlg.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && ev.defaultPrevented) escHandled = true; });
  dlg.addEventListener('cancel', (ev) => { if (escHandled) ev.preventDefault(); escHandled = false; });
  dlg.addEventListener('close', () => {
    dropCard();
    clear(list);
    clear(slot);
    rows = new Map();
    const o = opener;
    opener = null;
    onClosed(o && o.isConnected ? o : null);
  });

  // The queue the pop-up shows: entries with proposals, then failed filings. An entry being filed
  // again (RETRY, RE-FILE) is in neither list until it is done: while its card is open it keeps its
  // row, so the card doesn't vanish under the owner's eyes.
  function build() {
    const q = reviewQueue(groups, failedRows);
    if (card && cardQ && !q.some((x) => x.id === cardId) && card.refiling()) {
      q.splice(Math.min(selectedIndex, q.length), 0, { ...cardQ, failed: false, filing: true });
    }
    return q;
  }

  function select(id, { focus } = {}) {
    const moved = id !== cardId;
    selectedId = id;
    selectedIndex = Math.max(0, queue.findIndex((q) => q.id === id));
    for (const [rid, r] of rows) {
      const on = rid === id;
      r.classList.toggle('cur', on);
      r.setAttribute('aria-current', on ? 'true' : 'false');
      r.tabIndex = on ? 0 : -1;
    }
    const c = cardFor(queue[selectedIndex]);
    if (slot.firstChild !== c) slot.replaceChildren(c);
    if (moved) slot.scrollTop = 0;
    const row = rows.get(id);
    if (row && list.isConnected) {
      keepRowInList(list, row);
      if (focus) row.focus({ preventScroll: true });
    }
  }

  function rowFor(q, today) {
    const state = q.failed ? el('span', { class: 'st amb' }, 'Failed')
      : q.filing ? el('span', { class: 'st' }, 'Filing')
        : el('span', { class: 'st' }, `${q.count} to confirm`);
    return el('button', { type: 'button', class: q.failed ? 'row q fl' : 'row q',
      title: q.failed ? `Filing failed: ${q.error}`.slice(0, 300) : null,
      onclick: () => select(q.id, { focus: true }) },
      q.failed ? el('span', { class: 'sq' }) : el('span', { class: 'sq0', 'aria-hidden': 'true' }),
      el('span', { class: 'grow' },
        el('span', { class: 'syn' }, q.synopsis),
        el('span', { class: 'mt' }, state, el('span', { class: 'dt' }, entryStamp(q.id, today)))));
  }

  // Redraw the open pop-up: the queue (its scroll and the keyboard kept), the selected card.
  function draw() {
    const a = document.activeElement;
    const inQueue = a && list.contains(a);
    const inCard = !inQueue && a && card && card.contains(a) ? a : null;  // the card is kept: refocus the node
    // The card was busy (its buttons disabled, the focus nowhere) and meant to take it back.
    const nowhere = !a || a === document.body || a === dlg;
    const cardWants = !inQueue && !inCard && nowhere && card && card.wantsFocus();
    const prevCard = card;
    const top = list.scrollTop;
    selectedId = pickSelection(queue, selectedId, selectedIndex);
    count.textContent = String(queue.length);
    rows = new Map();
    clear(list);
    const today = localDay();
    for (const q of queue) {
      const r = rowFor(q, today);
      rows.set(q.id, r);
      list.appendChild(r);
    }
    list.scrollTop = top;
    select(selectedId);
    if (inQueue) {
      const cur = rows.get(selectedId);
      if (cur) cur.focus({ preventScroll: true });
    } else if ((inCard || cardWants) && card !== prevCard) {
      // The selected entry was done with and the selection moved on: the keyboard goes to the next
      // entry's first ✓ (R-F1-8), not to <body>.
      card.focusFirst();
    } else if (inCard && inCard.isConnected) {
      inCard.focus({ preventScroll: true });
    } else if (nowhere && !cardWants) {
      const cur = rows.get(selectedId);
      if (cur) cur.focus({ preventScroll: true });
    }
  }

  return {
    node: dlg,
    isOpen: () => dlg.open,
    ids: () => queue.map((q) => q.id),
    has: (id) => queue.some((q) => q.id === id),
    // New lists from Today's refresh. Returns the queue's length (as the pop-up would show it).
    update(g, f) {
      groups = g || [];
      failedRows = f || [];
      queue = build();
      if (!dlg.open) return queue.length;
      if (!queue.length) { dlg.close(); return 0; }  // the last entry was handled
      draw();
      onOpen(queue.map((q) => q.id));
      return queue.length;
    },
    // Open (or, already open, move to) the review. id: that entry selected; focusCard: its first ✓
    // takes the keyboard (the tray's REVIEW), else the selected queue row does. False: nothing to show.
    open({ id = null, focusCard = false } = {}) {
      if (!queue.length || !dlg.isConnected) return false;
      if (id && queue.some((q) => q.id === id)) selectedId = id;
      if (!dlg.open) {
        const a = document.activeElement;
        opener = a && a !== document.body && !dlg.contains(a) ? a : null;
        dlg.showModal();
        slot.scrollTop = 0;
      }
      draw();
      if (focusCard && card) card.focusFirst();
      else if (!card || !card.contains(document.activeElement)) {
        const cur = rows.get(selectedId);
        if (cur) cur.focus({ preventScroll: true });
      }
      onOpen(queue.map((q) => q.id));
      return true;
    },
    close() { if (dlg.open) dlg.close(); },
    destroy() { if (dlg.open) dlg.close(); dropCard(); },
  };
}
