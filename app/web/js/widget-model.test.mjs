import test from 'node:test';
import assert from 'node:assert/strict';
import { TILE_LABEL, TILE_VIEW, tileSummary, popupRows, synopsisText, DRAFT_KEY, loadDraft, saveDraft, openPlan, tauriInvoke, openOutcome, OPEN_MAIN_TIMEOUT_MS } from './widget-model.js';

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
  assert.equal(TILE_VIEW.projects, 'browse');
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

test('usage falls back to seven_day', () => {
  const seven = { used: 31, resets_at: '2026-10-25T09:00', warn: false };
  assert.deepEqual(tileSummary('usage', W({ usage: { five_hour: null, seven_day: seven } })), { value: '31%', caption: '7d · resets 09:00', warn: false });
});

test('agents', () => {
  assert.deepEqual(tileSummary('agents', W({ agents: { running: 2, waiting: 1, rows: [] } })), { value: '2', caption: 'running · 1 waiting', warn: true });
  assert.deepEqual(tileSummary('agents', W()), { value: '0', caption: 'running', warn: false });
});

test('people, deadlines, review, projects, arm', () => {
  assert.deepEqual(tileSummary('people', W({ people: { waiting: [{}, {}], to_read: 0 } })), { value: '2', caption: 'waiting on others', warn: false });
  assert.deepEqual(tileSummary('deadlines', W({ deadlines: [{ due: '2026-10-25' }, { due: '2026-11-01' }] })), { value: '2', caption: 'next: 2026-10-25', warn: false });
  assert.deepEqual(tileSummary('deadlines', W()), { value: '0', caption: 'none', warn: false });
  assert.deepEqual(tileSummary('review', W({ review: { proposals: 2, failed: 1 } })), { value: '3', caption: 'entries to review', warn: true });
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

test('synopsis shows the drive banner while the root is not usable', () => {
  assert.equal(synopsisText(W({ synopsis: '2 due today', drive: null })), '2 due today');
  const drive = { state: 'missing', message: "BrainHub's drive isn't available.", held: 0 };
  assert.equal(synopsisText(W({ synopsis: 'ignored', drive })), drive.message);
  assert.equal(synopsisText(W({ drive: { ...drive, held: 2 } })), `2 captures held on this OS · ${drive.message}`);
  assert.equal(synopsisText(null), '');
});

function memStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), m };
}

test('capture draft: save, load, clear', () => {
  const s = memStorage();
  assert.equal(loadDraft(s), '');
  saveDraft(s, 'half a thought');
  assert.equal(loadDraft(s), 'half a thought');
  assert.equal(s.m.get(DRAFT_KEY), 'half a thought');
  saveDraft(s, '');
  assert.equal(loadDraft(s), '');
  assert.equal(s.m.has(DRAFT_KEY), false);
});

test('capture draft: storage missing or throwing never breaks capture', () => {
  const boom = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); }, removeItem() { throw new Error('x'); } };
  assert.equal(loadDraft(boom), '');
  assert.doesNotThrow(() => saveDraft(boom, 'x'));
  assert.doesNotThrow(() => saveDraft(boom, ''));
  assert.equal(loadDraft(null), '');
  assert.doesNotThrow(() => saveDraft(undefined, 'x'));
  assert.equal(loadDraft({ getItem: () => 42 }), '');
});

test('openPlan: the shell when it is there, else POST /api/open; the arm always posts', () => {
  const inv = () => {};
  assert.deepEqual(openPlan('agents', undefined, null), { invoke: null, view: 'agents', body: { target: 'app', view: 'agents' } });
  const p = openPlan('projects', undefined, inv);
  assert.equal(p.invoke, inv); assert.equal(p.view, 'browse'); assert.deepEqual(p.body, { target: 'app', view: 'browse' });
  assert.equal(openPlan('projects', 'project/robotarm', inv).view, 'project/robotarm');
  assert.equal(openPlan('review', undefined, inv).view, 'today');
  assert.deepEqual(openPlan('arm', undefined, inv), { invoke: null, view: null, body: { target: 'arm' } });
  assert.deepEqual(openPlan('nope', undefined, 'not a function'), { invoke: null, view: null, body: { target: 'app' } });
});

test('tauriInvoke finds the shell bridge or nothing', () => {
  assert.equal(tauriInvoke(null), null);
  assert.equal(tauriInvoke({}), null);
  assert.equal(tauriInvoke({ __TAURI__: {} }), null);
  assert.equal(tauriInvoke({ __TAURI__: { core: { invoke: 1 } } }), null);
  const core = { calls: [], invoke(cmd, args) { this.calls.push([cmd, args]); return 'ok'; } };
  const f = tauriInvoke({ __TAURI__: { core } });
  assert.equal(f('open_main', { view: 'agents' }), 'ok');
  assert.deepEqual(core.calls, [['open_main', { view: 'agents' }]]);
  const trap = { get __TAURI__() { throw new Error('denied'); } };
  assert.equal(tauriInvoke(trap), null);
});

test('agents popup meta leaves out "unknown" and empty parts', () => {
  const rows = (r) => popupRows('agents', W({ agents: { rows: [r] } }))[0].meta;
  assert.equal(rows({ project_name: 'P', status: 'waiting', where: 'unknown', next_time: true }), 'waiting · next time');
  assert.equal(rows({ project_name: 'P', status: 'running', where: 'terminal', next_time: false }), 'running · terminal');
  assert.equal(rows({ project_name: 'P', status: 'idle', where: '', next_time: false }), 'idle');
  assert.equal(rows({ project_name: 'P', status: ' ', where: null }), '');
  assert.equal(rows({ project_name: 'P', status: 'finished', where: 'IDE', next_time: true }), 'finished · IDE · next time');
});

test('openOutcome: the shell when open_main resolves, else the fallback (rejected, thrown, too slow)', async () => {
  assert.equal(OPEN_MAIN_TIMEOUT_MS, 3000);
  const never = (_t, _f) => 0; // a timer that never fires
  assert.equal(await openOutcome(() => Promise.resolve(null), 3000, never), 'shell');
  assert.equal(await openOutcome(() => 'sync value', 3000, never), 'shell');
  assert.equal(await openOutcome(() => Promise.reject(new Error('could not show the main window')), 3000, never), 'fallback');
  assert.equal(await openOutcome(() => { throw new Error('no bridge'); }, 3000, never), 'fallback');
  // No answer: the timer decides, with the timeout it was given.
  let asked;
  const now = (t, f) => { asked = t; f(); return 0; };
  assert.equal(await openOutcome(() => new Promise(() => {}), 1234, now), 'fallback');
  assert.equal(asked, 1234);
  // A real (short) timer, and an answer that comes in time.
  assert.equal(await openOutcome(() => new Promise(() => {}), 10), 'fallback');
  assert.equal(await openOutcome(() => new Promise((r) => setTimeout(r, 1)), 200), 'shell');
});
