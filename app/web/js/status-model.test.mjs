import test from 'node:test';
import assert from 'node:assert/strict';
import { statusFrom, reviewCounts, reviewTitle } from './status-model.js';

// The full /api/widget shape (build_widget + drive + warnings).
const W = (o = {}) => ({
  synopsis: 'x', requests: 0, attention: false, tiles: [],
  today: { overdue: 0, due_today: 0, top: [] },
  usage: { five_hour: null, seven_day: null },
  agents: { running: 0, waiting: 0, finished: 0, idle: 0, rows: [] },
  people: { waiting: [], to_read: 0 },
  deadlines: [], review: { proposals: 0, failed: 0 }, projects: [],
  arm: { url: 'http://127.0.0.1:8080', reachable: false },
  drive: null, warnings: [],
  ...o,
});
const VOICE = { available: true, installed: true, enabled: true, paused_until: null, quiet_now: false };

test('a quiet hub: no chips, nothing amber', () => {
  const s = statusFrom(W(), null);
  assert.deepEqual(s.chips, []);
  assert.deepEqual([s.review.count, s.review.warn], [0, false]);
  assert.equal(s.usage, null);
  assert.deepEqual(s.agents, { running: 0, waiting: 0, dot: '', text: '0 RUNNING' });
  assert.equal(s.voice.show, false);
  assert.equal(s.voice.on, false);
});

test('without counts of its own, review falls back to proposals + failed, and failures get a chip', () => {
  const s = statusFrom(W({ review: { proposals: 3, failed: 2 } }), null);
  assert.deepEqual([s.review.count, s.review.warn], [5, true]);
  assert.equal(s.review.title, '5 to review · Today');
  assert.deepEqual(s.chips.map((c) => [c.text, c.kind]), [['2 FAILED', 'failed']]);
  assert.equal(s.chips[0].title, '2 filings failed');
  assert.equal(statusFrom(W({ review: { proposals: 0, failed: 1 } }), null).chips[0].title, '1 filing failed');
});

test('usage: the 5h window, else 7d, rounded, with its warn flag', () => {
  const five = { used: 42.6, resets_at: '2026-10-24T15:00', reset_passed: false, warn: false };
  assert.deepEqual(statusFrom(W({ usage: { five_hour: five, seven_day: null } }), null).usage,
    { label: '5H', pct: 43, warn: false });
  const hot = { ...five, used: 91, warn: true };
  assert.deepEqual(statusFrom(W({ usage: { five_hour: hot } }), null).usage, { label: '5H', pct: 91, warn: true });
  const seven = { used: 12, resets_at: '2026-10-28T09:00', warn: false };
  assert.deepEqual(statusFrom(W({ usage: { five_hour: { used: null }, seven_day: seven } }), null).usage,
    { label: '7D', pct: 12, warn: false });
  assert.equal(statusFrom(W({ usage: { five_hour: { used: 0, warn: false } } }), null).usage.pct, 0);
});

test('agents: green while running, amber while one waits', () => {
  assert.deepEqual(statusFrom(W({ agents: { running: 2, waiting: 0 } }), null).agents,
    { running: 2, waiting: 0, dot: 'g', text: '2 RUNNING' });
  assert.deepEqual(statusFrom(W({ agents: { running: 2, waiting: 1 } }), null).agents,
    { running: 2, waiting: 1, dot: 'a', text: '2 RUNNING · 1 WAITING' });
  assert.equal(statusFrom(W({ agents: { running: 0, waiting: 1 } }), null).agents.dot, 'a');
});

test('drive chips: read-only wins over DRIVE, and carries the reason', () => {
  const ro = { state: 'readonly', message: 'The data folder is read-only.', held: 0 };
  const s = statusFrom(W({ drive: ro }), null);
  assert.deepEqual(s.chips.map((c) => c.text), ['READ-ONLY']);
  assert.equal(s.chips[0].title, 'The data folder is read-only.');
  const missing = { state: 'missing', message: 'Drive D: is not mounted.', held: 2 };
  const m = statusFrom(W({ drive: missing }), null);
  assert.deepEqual(m.chips.map((c) => [c.text, c.kind]), [['DRIVE', 'drive']]);
  assert.equal(m.chips[0].title, 'Drive D: is not mounted. · 2 captures held on this OS');
  // the shell's fresher drive overrides the summary's, and null clears it
  assert.deepEqual(statusFrom(W({ drive: missing }), null, { drive: null }).chips, []);
  assert.equal(statusFrom(W(), null, { readonly: true }).chips[0].text, 'READ-ONLY');
  assert.ok(statusFrom(W(), null, { readonly: true }).chips[0].title);
});

test('offline: the chip first, and a missing summary is safe', () => {
  const s = statusFrom(null, null, { offline: true });
  assert.deepEqual(s.chips.map((c) => c.text), ['OFFLINE']);
  assert.deepEqual([s.review.count, s.review.warn], [0, false]);
  assert.equal(s.usage, null);
  assert.equal(s.agents.dot, '');
  const order = statusFrom(W({ review: { proposals: 0, failed: 1 }, drive: { state: 'missing', message: 'x' } }),
    null, { offline: true });
  assert.deepEqual(order.chips.map((c) => c.text), ['OFFLINE', 'DRIVE', '1 FAILED']);
});

test('the reduced app summary (no tiles data) gives the DRIVE chip and nothing else', () => {
  const reduced = { synopsis: 'Drive D: is not mounted.', attention: true, tiles: [],
    drive: { state: 'missing', message: 'Drive D: is not mounted.', held: 0 }, warnings: [] };
  const s = statusFrom(reduced, null);
  assert.deepEqual(s.chips.map((c) => c.text), ['DRIVE']);
  assert.deepEqual([s.review.count, s.review.warn], [0, false]);
  assert.equal(s.usage, null);
  assert.equal(s.agents.running, 0);
});

test('voice: hidden until installed; on, off and paused', () => {
  assert.equal(statusFrom(W(), { available: true, installed: false }).voice.show, false);
  const on = statusFrom(W(), VOICE).voice;
  assert.equal(on.show, true);
  assert.equal(on.on, true);
  assert.equal(on.action, 'off');
  const off = statusFrom(W(), { ...VOICE, enabled: false }).voice;
  assert.equal(off.on, false);
  assert.equal(off.action, 'on');
  const paused = statusFrom(W(), { ...VOICE, paused_until: '2026-10-24T16:30:00' }).voice;
  assert.equal(paused.on, false);
  assert.equal(paused.action, 'resume');
  assert.equal(statusFrom(W(), { ...VOICE, quiet_now: true }).voice.on, true);
});

test('review counts entries, as Today does; the proposals go in the title', () => {
  const groups = [{ entry_id: 'e-1', proposals: [{}, {}, {}] }, { entry_id: 'e-2', proposals: [{}] },
    { entry_id: 'e-3', proposals: [{}, {}] }];
  const failed = [{ entry_id: 'e-4' }, { entry_id: 'e-2' }];  // e-2 is listed twice: one entry
  const rc = reviewCounts(groups, failed);
  assert.deepEqual(rc, { entries: 4, proposals: 6, failed: 2 });
  const s = statusFrom(W({ review: { proposals: 6, failed: 2 } }), null, { review: rc });
  assert.equal(s.review.count, 4);
  assert.equal(s.review.warn, true);
  assert.equal(s.review.proposals, 6);
  assert.equal(s.review.title, '4 entries to review · 6 proposals · 2 failed filings · Today');
  assert.deepEqual(reviewCounts(null, undefined), { entries: 0, proposals: 0, failed: 0 });
  // the hub's own entries count is used when the top bar has none
  const e = statusFrom(W({ review: { proposals: 14, failed: 1, entries: 6 } }), null);
  assert.equal(e.review.count, 6);
  assert.equal(e.review.title, '6 entries to review · 14 proposals · 1 failed filing · Today');
  assert.equal(statusFrom(W(), null, { review: { entries: 0, proposals: 0, failed: 0 } }).review.title,
    'Nothing to review · Today');
  assert.equal(reviewTitle(1, 1, 0), '1 entry to review · 1 proposal · Today');
});
