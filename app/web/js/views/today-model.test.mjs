// Run with: node --test app/web/js/views/today-model.test.mjs (pure logic, no DOM needed).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  localDay, snoozeUntil, SNOOZES, entryStamp, shortWhen, firstWords, reviewQueue, pickSelection,
  moveSelection, mapNames, projectSlugs, itemMeta,
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
  assert.deepEqual(mapNames(map), { uni: 'Uni', 'robot-arm': 'Robot arm', thesis: 'Thesis', andrei: 'andrei' });
  assert.deepEqual([...projectSlugs(map)], ['robot-arm', 'thesis']);
  assert.equal(projectSlugs(null), null);
  assert.deepEqual(mapNames(null), {});
});

test('item meta: where, the due date amber once late, the reminder without a due, waiting since', () => {
  const names = { 'robot-arm': 'Robot arm', uni: 'Uni', andrei: 'Andrei' };
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
