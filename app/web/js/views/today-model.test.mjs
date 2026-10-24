// Run with: node --test app/web/js/views/today-model.test.mjs (pure logic, no DOM needed).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  localDay, snoozeUntil, SNOOZES, entryStamp, shortWhen, firstWords, reviewQueue, pickSelection,
  moveSelection, mapNames, projectSlugs, itemMeta, nameIn, groupName, stillProposed, scrollToShow, revealCard,
} from './today-model.js';

test('snoozeUntil is local 09:00, days ahead (across a month end)', () => {
  assert.equal(snoozeUntil(1, new Date(2026, 9, 24, 22, 15)), '2026-10-25T09:00');
  assert.equal(snoozeUntil(7, new Date(2026, 9, 28, 8, 0)), '2026-11-04T09:00');
  assert.deepEqual(SNOOZES.map((s) => s.value), [1, 3, 7]);
  assert.equal(localDay(new Date(2026, 0, 5)), '2026-01-05');
});

test('entry stamps and short dates drop the current year', () => {
  assert.equal(entryStamp('e-20261020-140322-ab12', '2026-10-24'), '10-20 14:03');
  assert.equal(entryStamp('e-20251231-235900-ab12', '2026-10-24'), '2025-12-31 23:59');
  assert.equal(entryStamp('nope', '2026-10-24'), '');
  assert.equal(shortWhen('2026-10-21', '2026-10-24'), '10-21');
  assert.equal(shortWhen('2026-10-28T09:00', '2026-10-24'), '10-28 09:00');
  assert.equal(shortWhen('2027-01-05', '2026-10-24'), '2027-01-05');
  assert.equal(shortWhen('soon', '2026-10-24'), 'soon');
});

test('firstWords cuts at a word and marks the cut', () => {
  assert.equal(firstWords('  short   text '), 'short text');
  const long = 'Tried a flux pen on the servo header, much cleaner joints at 330 °C. Need more solder.';
  const cut = firstWords(long, 40);
  assert.ok(cut.endsWith('…') && cut.length <= 41, cut);
  assert.ok(!/\s…$/.test(cut));
  assert.equal(firstWords(''), '');
});

test('the queue lists proposals, then failed filings not already listed', () => {
  const q = reviewQueue(
    [{ entry_id: 'e1', synopsis: 'Screws', text: 't1', proposals: [{}, {}] },
      { entry_id: 'e2', synopsis: '', text: 'Raw words here', proposals: [{}] }],
    [{ entry_id: 'e2', text: 'dup', error: 'x' }, { entry_id: 'e3', text: 'A foot pedal', error: 'claude exited 1' }]);
  assert.deepEqual(q.map((r) => [r.id, r.failed, r.count, r.synopsis]),
    [['e1', false, 2, 'Screws'], ['e2', false, 1, 'Raw words here'], ['e3', true, 0, 'A foot pedal']]);
  assert.equal(q[2].error, 'claude exited 1');
  assert.deepEqual(reviewQueue(null, undefined), []);
});

test('selection survives, moves on to the next after its entry goes, and clamps', () => {
  const q = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
  assert.equal(pickSelection(q, 'b', 1), 'b');
  assert.equal(pickSelection([{ id: 'a' }, { id: 'c' }], 'b', 1), 'c');
  assert.equal(pickSelection([{ id: 'a' }], 'c', 2), 'a');
  assert.equal(pickSelection(q, null), 'a');
  assert.equal(pickSelection([], 'a', 0), null);
});

test('arrow keys move within the queue and stop at its ends', () => {
  const q = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
  assert.equal(moveSelection(q, 'a', 'ArrowDown'), 'b');
  assert.equal(moveSelection(q, 'c', 'ArrowDown'), 'c');
  assert.equal(moveSelection(q, 'a', 'ArrowUp'), 'a');
  assert.equal(moveSelection(q, 'b', 'End'), 'c');
  assert.equal(moveSelection(q, 'c', 'Home'), 'a');
  assert.equal(moveSelection(q, 'b', 'Enter'), null);
  assert.equal(moveSelection([], 'b', 'ArrowDown'), null);
});

test('map names and the linkable project slugs', () => {
  const map = { areas: [{ slug: 'uni', name: 'Uni' }], projects: [{ slug: 'robot-arm', name: 'Robot arm' }],
    courses: [{ slug: 'thesis', name: 'Thesis' }], people: [{ slug: 'andrei' }] };
  assert.deepEqual(mapNames(map), { area: { uni: 'Uni' }, project: { 'robot-arm': 'Robot arm', thesis: 'Thesis' },
    person: { andrei: 'andrei' } });
  assert.deepEqual([...projectSlugs(map)], ['robot-arm', 'thesis']);
  assert.equal(projectSlugs(null), null);
  assert.deepEqual(mapNames(null), { area: {}, project: {}, person: {} });
});

test('item meta: where, the due date amber once late, the reminder without a due, waiting since', () => {
  const names = { project: { 'robot-arm': 'Robot arm' }, area: { uni: 'Uni' }, person: { andrei: 'Andrei' } };
  const today = '2026-10-24';
  assert.deepEqual(itemMeta({ kind: 'deadline', area: 'uni', project: null, due: '2026-10-21', remind_at: '2026-10-19' }, names, today),
    [{ text: 'Uni', warn: false }, { text: 'due 10-21', warn: true }]);
  assert.deepEqual(itemMeta({ kind: 'task', area: 'projects', project: 'robot-arm', due: '2026-10-24' }, names, today),
    [{ text: 'Robot arm', warn: false }, { text: 'due 10-24', warn: false }]);
  assert.deepEqual(itemMeta({ kind: 'reminder', area: 'personal', remind_at: '2026-10-28T09:00' }, names, today),
    [{ text: 'personal', warn: false }, { text: 'remind 10-28 09:00', warn: false }]);
  assert.deepEqual(itemMeta({ kind: 'waiting', area: 'projects', project: 'robot-arm', person: 'andrei', created: '2026-10-10T10:00:00' }, names, today),
    [{ text: 'Robot arm / Andrei', warn: false }, { text: 'since 10-10', warn: false }]);
  assert.deepEqual(itemMeta({ kind: 'next_time', area: 'projects', project: 'robot-arm', person: 'andrei' }, names, today, { place: false }),
    [{ text: 'Andrei', warn: false }]);
});

test('names are kept per kind: a person and a project sharing a slug show their own names', () => {
  const map = { areas: [{ slug: 'maria', name: 'Maria area' }], projects: [{ slug: 'maria', name: 'Maria (robot)' }],
    courses: [{ slug: 'maria', name: 'Course' }, { slug: 'ctrl', name: 'Control systems' }],
    people: [{ slug: 'maria', name: 'Maria Ionescu' }] };
  const names = mapNames(map);
  assert.equal(nameIn(names, 'person', 'maria'), 'Maria Ionescu');
  assert.equal(nameIn(names, 'project', 'maria'), 'Maria (robot)');  // a project wins over a course
  assert.equal(nameIn(names, 'project', 'ctrl'), 'Control systems');
  assert.equal(nameIn(names, 'area', 'maria'), 'Maria area');
  assert.equal(nameIn(names, 'person', 'nobody'), 'nobody');
  assert.equal(nameIn({}, 'person', 'x'), 'x');
  assert.deepEqual(itemMeta({ kind: 'waiting', project: 'maria', person: 'maria' }, names, '2026-10-24'),
    [{ text: 'Maria (robot) / Maria Ionescu', warn: false }]);
  assert.equal(groupName(names, 'maria'), 'Maria (robot)');
  assert.equal(groupName(mapNames({ areas: [{ slug: 'uni', name: 'Uni' }] }), 'uni'), 'Uni');
  assert.equal(groupName(names, 'unknown'), 'unknown');
  // a prototype name is a slug like any other
  assert.equal(nameIn(names, 'person', 'constructor'), 'constructor');
});

test('stillProposed keeps only what is still to confirm, in the order shown', () => {
  const ps = [{ id: 'p-1', status: 'proposed' }, { id: 'p-0', status: 'confirmed' }, { id: 'p-2', status: 'proposed' },
    { id: 'p-3', status: 'dismissed' }];
  assert.deepEqual(stillProposed(['p-0', 'p-2', 'p-1'], ps), ['p-2', 'p-1']);
  assert.deepEqual(stillProposed(['p-9'], ps), []);
  assert.deepEqual(stillProposed(['p-1'], null), []);
  assert.deepEqual(stillProposed(null, ps), []);
});

test('scrollToShow and revealCard', () => {
  assert.equal(scrollToShow(100, 200, 0, 500), 0);
  assert.equal(scrollToShow(450, 600, 0, 500), 100);       // below: scroll down to its bottom
  assert.equal(scrollToShow(-50, 20, 0, 500), -50);        // above: up to its top
  assert.equal(scrollToShow(300, 1200, 0, 500), 300);      // taller than the view: its top
  // the card below the fold comes up, but the selected row stays at the top at most
  assert.equal(revealCard(600, 1400, 120, 56, 700, 220), 64);
  assert.equal(revealCard(400, 900, 120, 56, 700, 220), 0);  // its first 220 px already show
  assert.equal(revealCard(560, 700, 300, 56, 700, 220), 0);  // 560..700 shows
  assert.equal(revealCard(600, 800, 300, 56, 700, 220), 100);
  assert.equal(revealCard(-300, 200, -340, 56, 700, 220), -396);  // scrolled past: back up, the row too
});

test('the pop-up: seen ids parse, prune and decide what is new', async () => {
  const m = await import('./today-model.js');
  assert.deepEqual(m.parseSeen('["e-1","e-2","e-1",3,"",null]'), ['e-1', 'e-2']);
  for (const junk of [null, undefined, '', 'nope', '{"a":1}', '42']) assert.deepEqual(m.parseSeen(junk), []);
  assert.deepEqual(m.unseenIds(['e-1', 'e-2', 'e-3'], new Set(['e-2'])), ['e-1', 'e-3']);
  assert.deepEqual(m.unseenIds(['e-1'], ['e-1']), []);
  assert.deepEqual(m.unseenIds([], null), []);
  assert.deepEqual(m.pruneSeen(new Set(['e-1', 'e-9']), ['e-1', 'e-2']), ['e-1']);
  assert.deepEqual(m.pruneSeen(['e-9'], []), []);
  assert.equal(m.REVIEW_SEEN_KEY, 'atlas.review.seen');
});

test('the pop-up opens by itself only for something new, never over typing or when read-only', async () => {
  const { shouldAutoOpen } = await import('./today-model.js');
  assert.equal(shouldAutoOpen({ open: false, typing: false, readonly: '', unseen: 1 }), true);
  assert.equal(shouldAutoOpen({ open: false, typing: false, readonly: '', unseen: 0 }), false);
  assert.equal(shouldAutoOpen({ open: true, typing: false, readonly: '', unseen: 2 }), false);
  assert.equal(shouldAutoOpen({ open: false, typing: true, readonly: '', unseen: 2 }), false);
  assert.equal(shouldAutoOpen({ open: false, typing: false, readonly: 'The drive is read-only', unseen: 2 }), false);
});

test('?review= asks for the pop-up, with an entry when it names one', async () => {
  const { reviewTarget, reviewNoun } = await import('./today-model.js');
  const q = (s) => new URLSearchParams(s);
  assert.equal(reviewTarget(q('')), null);
  assert.equal(reviewTarget(q('notice=setup-done')), null);
  assert.equal(reviewTarget(null), null);
  assert.deepEqual(reviewTarget(q('review=1')), { id: null });
  assert.deepEqual(reviewTarget(q('review=')), { id: null });
  assert.deepEqual(reviewTarget(q('review=e-20261024-022200-ab12')), { id: 'e-20261024-022200-ab12' });
  assert.deepEqual(reviewTarget(q('review=<b>x</b>')), { id: null });
  assert.equal(reviewNoun(1), 'entry to review');
  assert.equal(reviewNoun(0), 'entries to review');
  assert.equal(reviewNoun(7), 'entries to review');
});
