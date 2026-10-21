import test from 'node:test';
import assert from 'node:assert/strict';
import { TILE_LABEL, TILE_VIEW, tileSummary, popupRows } from './widget-model.js';

const W = (o = {}) => ({
  synopsis: 'x', attention: false, tiles: [],
  today: { overdue: 0, due_today: 0, top: [] },
  usage: { five_hour: null, seven_day: null },
  agents: { running: 0, waiting: 0, finished: 0, idle: 0, rows: [] },
  people: { waiting: [], to_read: 0 },
  deadlines: [], review: { proposals: 0, failed: 0 }, projects: [],
  arm: { url: 'http://127.0.0.1:8080', reachable: false },
  ...o,
});

test('labels and views', () => {
  assert.equal(TILE_LABEL.review, 'To review');
  assert.equal(TILE_LABEL.arm, 'Robot arm');
  assert.equal(TILE_VIEW.review, 'today');
  assert.equal(TILE_VIEW.projects, 'settings');
  assert.equal(TILE_VIEW.arm, undefined);
});

test('today', () => {
  assert.deepEqual(tileSummary('today', W({ today: { overdue: 0, due_today: 3, top: [] } })), { value: '3', caption: 'due today', warn: false });
  assert.deepEqual(tileSummary('today', W({ today: { overdue: 1, due_today: 2, top: [] } })), { value: '3', caption: 'due · 1 overdue', warn: true });
});

test('usage', () => {
  const five = { used: 62.4, resets_at: '2026-10-21T14:20', warn: true };
  assert.deepEqual(tileSummary('usage', W({ usage: { five_hour: five, seven_day: null } })), { value: '62%', caption: '5h · resets 14:20', warn: true });
  assert.deepEqual(tileSummary('usage', W()), { value: '—', caption: 'no data yet', warn: false });
  assert.deepEqual(tileSummary('usage', W({ usage: { five_hour: { used: null }, seven_day: null } })), { value: '—', caption: 'no data yet', warn: false });
});

test('agents', () => {
  assert.deepEqual(tileSummary('agents', W({ agents: { running: 2, waiting: 1, rows: [] } })), { value: '2', caption: 'running · 1 waiting', warn: true });
  assert.deepEqual(tileSummary('agents', W()), { value: '0', caption: 'running', warn: false });
});

test('people, deadlines, review, projects, arm', () => {
  assert.deepEqual(tileSummary('people', W({ people: { waiting: [{}, {}], to_read: 0 } })), { value: '2', caption: 'waiting on others', warn: false });
  assert.deepEqual(tileSummary('deadlines', W({ deadlines: [{ due: '2026-10-25' }, { due: '2026-11-01' }] })), { value: '2', caption: 'next: 2026-10-25', warn: false });
  assert.deepEqual(tileSummary('deadlines', W()), { value: '0', caption: 'none', warn: false });
  assert.deepEqual(tileSummary('review', W({ review: { proposals: 2, failed: 1 } })), { value: '3', caption: 'to confirm', warn: true });
  assert.equal(tileSummary('review', W()).warn, false);
  const projects = [{ slug: 'a', open: 2 }, { slug: 'b', open: 0 }, { slug: 'c', open: 1 }];
  assert.deepEqual(tileSummary('projects', W({ projects })), { value: '2', caption: 'with open items', warn: false });
  assert.deepEqual(tileSummary('arm', W({ arm: { reachable: true } })), { value: 'on', caption: 'reachable', warn: false });
  assert.deepEqual(tileSummary('arm', W()), { value: 'off', caption: 'offline', warn: false });
});

test('popupRows caps at 6 and projects carry view paths', () => {
  const top = Array.from({ length: 9 }, (_, i) => ({ text: 't' + i, due: '2026-10-2' + (i % 9) }));
  assert.equal(popupRows('today', W({ today: { overdue: 0, due_today: 9, top } })).length, 6);
  const projects = Array.from({ length: 8 }, (_, i) => ({ slug: 'p' + i, name: 'P' + i, open: i + 1, next_time: 0 }));
  const rows = popupRows('projects', W({ projects }));
  assert.equal(rows.length, 6);
  assert.equal(rows[0].view, 'project/p0');
  assert.equal(rows[0].text, 'P0');
  assert.match(rows[0].meta, /1 open/);
  assert.deepEqual(popupRows('projects', W({ projects: [{ slug: 'x', name: 'X', open: 0 }] })), []);
});

test('popupRows for other tiles', () => {
  assert.deepEqual(popupRows('usage', W({ usage: { five_hour: { used: 50, resets_at: '2026-10-21T14:20' }, seven_day: { used: 10, resets_at: '2026-10-25T09:00' } } })).map((r) => r.text), ['5h window', '7d window']);
  assert.deepEqual(popupRows('usage', W()), []);
  const a = popupRows('agents', W({ agents: { rows: [{ project_name: 'Proj', part: 'api', status: 'running', where: 'cli', next_time: false }] } }));
  assert.equal(a[0].text, 'Proj · api'); assert.match(a[0].meta, /running/);
  const p = popupRows('people', W({ people: { waiting: [{ person: 'x', name: 'Ann', text: 'reply', since: '2026-10-10', nudge: true }] } }));
  assert.equal(p[0].text, 'Ann: reply');
  assert.equal(popupRows('deadlines', W({ deadlines: [{ text: 'd', due: '2026-10-25', project: 'p' }] }))[0].meta, '2026-10-25');
  assert.equal(popupRows('review', W({ review: { proposals: 2, failed: 1 } })).length, 2);
  assert.equal(popupRows('arm', W()).length, 0);
});
