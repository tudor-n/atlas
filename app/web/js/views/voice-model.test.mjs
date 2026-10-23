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
