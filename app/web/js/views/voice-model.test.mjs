import { test } from 'node:test';
import assert from 'node:assert/strict';
import { blendRows, readBlend, voiceName, statusLine, widgetVoice, mb } from './voice-model.js';

const ready = { available: true, installed: true, enabled: true, paused_until: null, quiet_now: false,
  last_error: null, download: { running: false }, missing_bytes: 0 };

test('blend rows show whole percentages in order', () => {
  assert.deepEqual(blendRows({ bm_george: 0.7, bm_fable: 0.3 }), [{ id: 'bm_george', pct: 70 }, { id: 'bm_fable', pct: 30 }]);
  assert.deepEqual(blendRows({ af_heart: 2 }), [{ id: 'af_heart', pct: 100 }]);
  assert.deepEqual(blendRows(null), []);
});

test('readBlend normalises and refuses bad rows', () => {
  assert.deepEqual(readBlend([{ id: 'bm_george', pct: 70 }, { id: 'bm_fable', pct: 30 }]), { blend: { bm_george: 0.7, bm_fable: 0.3 } });
  assert.deepEqual(readBlend([{ id: 'bm_george', pct: 1 }, { id: '', pct: 5 }, { id: 'bf_emma', pct: 2 }]),
    { blend: { bm_george: 0.333, bf_emma: 0.667 } });
  assert.match(readBlend([]).error, /at least one/);
  assert.match(readBlend([{ id: 'a', pct: 1 }, { id: 'b', pct: 1 }, { id: 'c', pct: 1 }, { id: 'd', pct: 1 }]).error, /at most three/);
  assert.match(readBlend([{ id: 'a', pct: 1 }, { id: 'a', pct: 1 }]).error, /only appear once/);
  assert.match(readBlend([{ id: 'a', pct: 0 }]).error, /above 0/);
  assert.match(readBlend([{ id: 'a', pct: 'x' }]).error, /above 0/);
});

test('voice names', () => {
  assert.equal(voiceName('bm_george'), 'George (British man)');
  assert.equal(voiceName('af_heart'), 'Heart (American woman)');
  assert.equal(voiceName('zz'), 'zz');
});

test('status line', () => {
  assert.deepEqual(statusLine(ready), { text: 'On.', kind: 'status' });
  assert.match(statusLine({ ...ready, installed: false, missing_bytes: 120575669 }).text, /about 121 MB/);
  assert.equal(statusLine({ ...ready, installed: false, missing_bytes: 1, download: { error: 'checksum' } }).kind, 'error');
  assert.match(statusLine({ ...ready, download: { running: true, done: 5e6, total: 120e6 } }).text, /5 MB of 120 MB/);
  assert.equal(statusLine({ ...ready, enabled: false }).text, 'The voice is off.');
  assert.equal(statusLine({ ...ready, paused_until: '2026-10-23T14:05' }).text, 'Paused until 14:05.');
  assert.match(statusLine({ ...ready, quiet_now: true }).text, /Quiet hours/);
  assert.equal(statusLine({ ...ready, last_error: 'no audio player' }).kind, 'error');
  assert.match(statusLine({ available: false, reason: 'Read-only.' }).text, /Read-only/);
});

test('widget speaker', () => {
  assert.equal(widgetVoice({ ...ready, installed: false }).show, false);
  assert.equal(widgetVoice(null).show, false);
  assert.equal(widgetVoice(ready).action, 'off');
  assert.equal(widgetVoice({ ...ready, enabled: false }).action, 'on');
  const p = widgetVoice({ ...ready, paused_until: '2026-10-23T14:05' });
  assert.equal(p.action, 'resume');
  assert.match(p.title, /14:05/);
  assert.equal(widgetVoice({ ...ready, quiet_now: true }).muted, true);
});

test('mb', () => {
  assert.equal(mb(0), '1 MB');
  assert.equal(mb(325532387), '326 MB');
});

test('workoutWords: commas, trimmed, deduplicated, bounded', async () => {
  const { workoutWords } = await import('./voice-model.js');
  assert.deepEqual(workoutWords(' gym,  Leg   day , yoga,, GYM '), { list: ['gym', 'Leg day', 'yoga'] });
  assert.deepEqual(workoutWords(''), { list: [] });
  assert.match(workoutWords('gym, x').error, /2 to 40/);
  assert.match(workoutWords(Array.from({ length: 101 }, (_, i) => `w${i}`).join(',')).error, /at most 100/);
});

test('the event switches cover every voice_events key, the chatter ones included', async () => {
  const { EVENT_LABELS } = await import('./voice-model.js');
  assert.deepEqual(EVENT_LABELS.map(([k]) => k), ['agent_waiting', 'agent_finished', 'reminder', 'readback', 'reply',
    'briefing', 'self', 'nudge', 'react', 'due_today', 'tease', 'greet']);
});

test('the level preview follows the table, and the owner cap where it counts', async () => {
  const { levelPreview, LEVELS, levelName } = await import('./voice-model.js');
  assert.deepEqual(LEVELS.map((l) => l.label), ['MUTE', 'LOW', 'MEDIUM', 'HIGH', 'VERY HIGH']);
  assert.equal(levelName('debug'), 'VERY HIGH');
  assert.equal(levelName('nope'), 'MEDIUM');
  const med = levelPreview('medium');
  assert.match(med, /^4 unprompted lines an hour, due today again every 2 h, reacts to everything, callbacks and mood, no musings, quips with a cooldown\.$/);
  assert.match(levelPreview('low'), /2 unprompted lines.*every 3 h.*mistakes only.*no callbacks or mood.*1 in 3/);
  assert.match(levelPreview('high'), /8 unprompted.*every hour.*, musings,/);
  assert.match(levelPreview('mute'), /^No unprompted lines, no due-today repeats, no reactions.*keep their own switches\.$/);
  const dbg = levelPreview('debug');
  assert.match(dbg, /^No cap on unprompted lines.*every 2 min.*musings every 3 min.*a quip on every line\..*quiet hours.*2 h/);
  assert.match(levelPreview('medium', 1), /^1 unprompted line an hour \(yours\)/);
  assert.match(levelPreview('high', 8), /^8 unprompted lines an hour,/);  // the level's own: not "yours"
  assert.match(levelPreview('mute', 5), /^No unprompted lines/);  // Mute and Very high ignore the cap
  assert.match(levelPreview('debug', 5), /^No cap/);
});

test('readChatter checks the chatter fields', async () => {
  const { readChatter } = await import('./voice-model.js');
  const ok = { perHour: '', lastCall: '18:00', project: '', maxPct: '80' };
  assert.deepEqual(readChatter(ok), { patch: { voice_chatter_per_hour: null, voice_last_call: '18:00', voice_atlas_project: '', voice_sub_max_pct: 80 } });
  assert.equal(readChatter({ ...ok, perHour: ' 6 ' }).patch.voice_chatter_per_hour, 6);
  assert.equal(readChatter({ ...ok, perHour: '0' }).patch.voice_chatter_per_hour, 0);
  assert.equal(readChatter({ ...ok, lastCall: '07:30:00' }).patch.voice_last_call, '07:30');
  assert.equal(readChatter({ ...ok, project: ' atlas ' }).patch.voice_atlas_project, 'atlas');
  assert.equal(readChatter({ ...ok, project: 'brățară-v2.1' }).patch.voice_atlas_project, 'brățară-v2.1');
  for (const bad of [{ perHour: '61' }, { perHour: '2.5' }, { perHour: 'x' }, { perHour: '-1' }]) {
    assert.match(readChatter({ ...ok, ...bad }).error, /0 to 60/);
  }
  assert.match(readChatter({ ...ok, lastCall: '24:00' }).error, /like 18:00/);
  assert.match(readChatter({ ...ok, lastCall: '' }).error, /like 18:00/);
  assert.match(readChatter({ ...ok, project: '-atlas' }).error, /slug/);
  assert.match(readChatter({ ...ok, project: 'a b' }).error, /slug/);
  assert.match(readChatter({ ...ok, project: 'a'.repeat(81) }).error, /slug/);
  for (const pct of ['0', '101', '', '50.5']) assert.match(readChatter({ ...ok, maxPct: pct }).error, /1 to 100/);
});

test('Try it now offers the subscription patterns only while it is on', async () => {
  const { tryList, tryOutcome } = await import('./voice-model.js');
  const off = tryList(false).map((t) => t.what);
  assert.deepEqual(off, ['briefing', 'morning', 'evening', 'due_today', 'last_call', 'nudge', 'tease', 'musing', 'self', 'react']);
  assert.deepEqual(tryList(true).map((t) => t.what).slice(-3), ['sub_quips', 'sub_briefings', 'sub_review']);
  assert.equal(tryOutcome({ said: { text: 'Hi.', spoken: true } }), 'Said.');
  assert.match(tryOutcome({ said: { text: 'Hi.', spoken: false } }), /Captioned/);
  assert.match(tryOutcome({ said: null }), /Nothing to say/);
  assert.match(tryOutcome({ started: true }), /Started/);
  assert.match(tryOutcome({ started: false }), /Already running/);
  assert.match(tryOutcome(null), /Nothing to say/);
});

test('recent lines, presence, debug and the subscription line', async () => {
  const { lineRow, presenceLine, debugLine, subLine } = await import('./voice-model.js');
  assert.deepEqual(lineRow({ event: 'briefing', text: 'Welcome back.', origin: 'lines.md briefing#2', spoken: true, why: '', at: '2026-10-26T14:05:09' }),
    { text: 'Welcome back.', meta: '14:05:09 · briefing · lines.md briefing#2 · spoken' });
  assert.equal(lineRow({ event: 'self', text: 'x', origin: 'fixed', spoken: false, why: 'over budget', at: '2026-10-26T14:05:09' }).meta,
    '14:05:09 · self · fixed · captioned (over budget)');
  assert.deepEqual(lineRow(null), { text: '', meta: 'captioned' });
  assert.deepEqual(presenceLine({ state: 'present', last_seen: '2026-10-26T14:05:09', locked: false, source: 'app' }),
    { text: 'Present. Last seen 14:05:09 (app).', kind: 'g' });
  assert.deepEqual(presenceLine({ state: 'away', last_seen: null, locked: true, source: null }),
    { text: 'Away, screen locked. Not seen since the start.', kind: 'ring' });
  assert.equal(presenceLine({ state: 'idle', last_seen: '2026-10-26T14:05:09' }).kind, '');
  assert.equal(presenceLine(null).text, 'Presence unknown.');
  assert.equal(debugLine({ since: '2026-10-26T14:05:00', before: 'low' }), 'Very high since 14:05. Back to Low at 16:05, or at a restart.');
  assert.equal(debugLine(null), '');
  assert.equal(debugLine({ since: 'nonsense' }), '');
  assert.equal(subLine(null), '');
  assert.equal(subLine({}), '');
  assert.equal(subLine({ runs_week: 3, last: '2026-10-26 quips ok' }), 'Subscription: 3 runs this week, last 2026-10-26 quips ok.');
  assert.equal(subLine({ runs_week: 1, last: { what: 'review', at: '2026-10-26T03:00', nested: { x: 1 } } }),
    'Subscription: 1 run this week, last review · 2026-10-26T03:00.');
  assert.equal(subLine({ runs_week: 3, last: { quips: '2026-10-26T20:48:05', briefings: '2026-10-26T20:50:14', review: '2026-10-26T20:48:18' } }),
    'Subscription: 3 runs this week, last A 20:48 · B 20:50 · C 20:48.');
});

test('the widget tag shows only at Very high', async () => {
  const { widgetDebug } = await import('./voice-model.js');
  assert.equal(widgetDebug({ ...ready, chattiness: 'debug' }), true);
  assert.equal(widgetDebug({ ...ready, chattiness: 'high' }), false);
  assert.equal(widgetDebug(ready), false);
  assert.equal(widgetDebug(null), false);
});

test("ATLAS's week: shown only with something to say, dismissed suggestions dropped", async () => {
  const { weekView } = await import('./voice-model.js');
  assert.equal(weekView(null), null);
  assert.equal(weekView({ title: ' ', paragraphs: ['', 3] }), null);
  const w = weekView({ title: ' My week ', written_at: '2026-10-25T21:00', paragraphs: ['One.', ' ', 'Two.'],
    suggestions: [{ id: 's1', text: 'Fix it', status: 'open' }, { id: 's2', text: 'Drop', status: 'dismissed' },
      { id: 3, text: 'Done', status: 'confirmed' }, { id: 's4', text: '', status: 'open' }, { text: 'no id' }, null] });
  assert.deepEqual(w, { title: 'My week', written: '2026-10-25T21:00', paragraphs: ['One.', 'Two.'],
    suggestions: [{ id: 's1', text: 'Fix it', status: 'open' }, { id: '3', text: 'Done', status: 'confirmed' }] });
  assert.deepEqual(weekView({ paragraphs: ['Only this.'] }).suggestions, []);
});
