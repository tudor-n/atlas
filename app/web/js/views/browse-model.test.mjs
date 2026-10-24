// Run with: node --test web/js/views/browse-model.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { railPlaces, findPlace, browsePath, localDay, dateCell } from './browse-model.js';

const MAP = {
  projects: [{ slug: 'robotarm', name: 'Robot arm' }, { slug: 'atlas', name: 'ATLAS' }, { name: 'no slug' }],
  courses: [{ slug: 'thesis', name: 'Bachelor thesis' }],
};

test('railPlaces: projects, then courses, as route paths', () => {
  assert.deepEqual(railPlaces(MAP).map((p) => [p.path, p.name, p.kind]), [
    ['project/robotarm', 'Robot arm', 'project'],
    ['project/atlas', 'ATLAS', 'project'],
    ['project/thesis', 'Bachelor thesis', 'course'],
  ]);
  assert.deepEqual(railPlaces(null), []);
  assert.deepEqual(railPlaces({ projects: 'x' }), []);
});

test('findPlace: a course lives under uni/, a project or an unknown slug under projects/', () => {
  assert.deepEqual(findPlace(MAP, 'thesis'),
    { slug: 'thesis', name: 'Bachelor thesis', kind: 'course', known: true, prefix: 'uni/thesis/' });
  assert.equal(findPlace(MAP, 'robotarm').prefix, 'projects/robotarm/');
  assert.deepEqual(findPlace(null, 'gone'),
    { slug: 'gone', name: 'gone', kind: 'project', known: false, prefix: 'projects/gone/' });
});

test('browsePath: the explicit route, else the last-used one, else deadlines', () => {
  assert.equal(browsePath(['people'], 'deadlines'), 'people');
  assert.equal(browsePath(['deadlines'], 'people'), 'deadlines');
  assert.equal(browsePath(['project', 'robotarm'], 'people'), 'project/robotarm');
  assert.equal(browsePath([], 'project/robotarm'), 'project/robotarm');
  assert.equal(browsePath([], 'people'), 'people');
  assert.equal(browsePath([], 'junk'), 'deadlines');
  assert.equal(browsePath(undefined, null), 'deadlines');
  assert.equal(browsePath(['project'], 'people'), 'people');
});

test('localDay and dateCell: mono short dates, overdue only for a past due date', () => {
  assert.equal(localDay(new Date(2026, 9, 4, 23, 59)), '2026-10-04');
  const today = '2026-10-24';
  assert.deepEqual(dateCell({ due: '2026-11-14' }, today), { text: '11-14', overdue: false, remind: false });
  assert.deepEqual(dateCell({ due: '2026-10-22' }, today), { text: '10-22', overdue: true, remind: false });
  assert.deepEqual(dateCell({ due: null, remind_at: '2026-10-25T09:00' }, today),
    { text: '10-25 09:00', overdue: false, remind: true });
  assert.equal(dateCell({ remind_at: '2026-10-01T09:00' }, today).overdue, false);
  assert.equal(dateCell({ due: '2027-01-05' }, today).text, '2027-01-05');
  assert.equal(dateCell({ due: '2026-10-24T14:00' }, today).overdue, false);
  assert.deepEqual(dateCell({}, today), { text: '–', overdue: false, remind: false });
});
