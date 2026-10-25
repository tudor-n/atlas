import test from 'node:test';
import assert from 'node:assert/strict';
import { needsYou, barText, attention, statusStrip, DRAFT_KEY, loadDraft, saveDraft, openPlan, tauriInvoke, openOutcome, OPEN_MAIN_TIMEOUT_MS } from './widget-model.js';

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


const busy = () => W({
  agents: { running: 1, waiting: 1, rows: [
    { project_name: 'ATLAS', part: 'phase-4.5', status: 'waiting' },
    { project_name: 'DUMMY arm', part: 'finger-follow', status: 'running' }] },
  review: { proposals: 2, failed: 1, entries: 2 },
  requests: 1,
  today: { overdue: 1, due_today: 1, top: [{ text: 'Pay the rent', due: '2026-10-20' }, { text: 'Call the dentist', due: '2026-10-24' }] },
  usage: { five_hour: { used: 91.4, resets_at: '2026-10-24T15:34', warn: true }, seven_day: { used: 21, resets_at: '2026-10-28T12:34', warn: false } },
  people: { waiting: [{ name: 'Ana', text: 'lab report', since: '10-20', nudge: true }, { name: 'Bo', text: 'keys', since: '10-24', nudge: false }] },
});

test('needsYou: the most urgent first, with the page each row opens', () => {
  const rows = needsYou(busy());
  assert.deepEqual(rows.map((r) => [r.key, r.text, r.meta, r.view, r.warn]), [
    ['agent:0', 'ATLAS · phase-4.5', 'waiting for you', 'agents', true],
    ['review', '2 entries to review', '1 failed', 'today', true],
    ['requests', '1 tablet request', 'answer on the tablet', 'tablet', true],
    ['due:0', 'Pay the rent', 'overdue · 2026-10-20', 'today', true],
    ['due:1', 'Call the dentist', 'due today', 'today', false],
    ['usage:5-hour', '91% of the 5-hour limit used', 'resets 15:34', 'usage', true],
    ['nudge:0', 'Nudge Ana: lab report', 'since 10-20', 'people', false],
  ]);
});

test('needsYou: calm is empty; running agents and plain waiting are not listed', () => {
  assert.deepEqual(needsYou(W()), []);
  assert.deepEqual(needsYou(null), []);
  const calm = W({ agents: { running: 2, rows: [{ project_name: 'P', status: 'running' }] },
    people: { waiting: [{ name: 'Bo', text: 'keys', since: '10-24', nudge: false }] } });
  assert.deepEqual(needsYou(calm), []);
  // Older hubs count proposals + failed filings, not entries.
  assert.equal(needsYou(W({ review: { proposals: 1, failed: 0 } }))[0].text, '1 entry to review');
  assert.equal(needsYou(W({ agents: { rows: [{ status: 'waiting' }] } }))[0].text, 'An agent');
});

test('needsYou: an agent without a part is just its project (the hub sends "—" for none)', () => {
  const text = (row) => needsYou(W({ agents: { rows: [{ status: 'waiting', ...row }] } }))[0].text;
  assert.equal(text({ project_name: 'Proj1', part: '—' }), 'Proj1');
  assert.equal(text({ project_name: 'Proj1', part: '  ' }), 'Proj1');
  assert.equal(text({ part: '—' }), 'An agent');
  assert.equal(text({ project_name: 'Proj1', part: ' main ' }), 'Proj1 · main');
});

test('barText: what needs you in a few words, then the 5-hour usage', () => {
  assert.equal(barText(busy()), '1 agent needs you · 2 to review · 1 request · 2 due · 1 nudge · 5h 91%');
  assert.equal(barText(W()), 'All clear');
  assert.equal(barText(W({ usage: { five_hour: { used: 38, resets_at: 'x' } } })), 'All clear · 5h 38%');
  const two = W({ agents: { rows: [{ status: 'waiting' }, { status: 'waiting' }] } });
  assert.equal(barText(two), '2 agents need you');
  assert.equal(barText(null), '');
});

test('barText shows the drive banner while the root is not usable', () => {
  const drive = { state: 'missing', message: "ATLAS's drive isn't available.", held: 0 };
  assert.equal(barText(W({ drive })), drive.message);
  assert.equal(barText(W({ drive: { ...drive, held: 2 } })), `2 captures held on this OS · ${drive.message}`);
});

test('attention: only rows that warn (and the drive banner) raise it', () => {
  assert.equal(attention(busy()), true);
  assert.equal(attention(W()), false);
  assert.equal(attention(W({ today: { overdue: 0, due_today: 1, top: [{ text: 'x', due: 'd' }] } })), false);
  assert.equal(attention(W({ drive: { message: 'm', held: 0 } })), true);
  assert.equal(attention(null), false);
});

test('statusStrip: usage, agents running, the arm', () => {
  assert.deepEqual(statusStrip(busy()), [
    { text: '5H 91%', warn: true }, { text: '7D 21%', warn: false }, { text: '1 RUNNING', warn: false }, { text: 'ARM OFF', warn: false }]);
  assert.deepEqual(statusStrip(W({ arm: { reachable: true } })), [{ text: '0 RUNNING', warn: false }, { text: 'ARM ON', warn: false }]);
  assert.deepEqual(statusStrip(null), []);
});

test('openPlan: the shell when it is there, else POST /api/open', () => {
  const inv = () => {};
  assert.deepEqual(openPlan('agents', null), { invoke: null, view: 'agents', body: { target: 'app', view: 'agents' } });
  const p = openPlan('today', inv);
  assert.equal(p.invoke, inv); assert.equal(p.view, 'today'); assert.deepEqual(p.body, { target: 'app', view: 'today' });
  assert.deepEqual(openPlan(null, 'not a function'), { invoke: null, view: null, body: { target: 'app' } });
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
