// Run with: node --test app/web/js/components/filing-status.test.mjs (pure logic, no DOM needed).
import test from 'node:test';
import assert from 'node:assert/strict';
import { filingStatus } from './filing-status.js';

const started = '2026-10-23T10:00:00';  // local time, as the hub sends it
const at = (s) => new Date(2026, 9, 23, 10, 0, s).getTime();

test('names the step and counts whole seconds', () => {
  assert.deepEqual(filingStatus('running', { started, step: 'reading projects/robotarm/notes.md' }, at(4.9)),
    { text: 'Filing… reading projects/robotarm/notes.md', elapsed: '4 s' });
});

test('before the first step, or without progress', () => {
  assert.deepEqual(filingStatus('running', { started, step: null }, at(0)), { text: 'Filing…', elapsed: '0 s' });
  assert.deepEqual(filingStatus('running', null, at(3)), { text: 'Filing…', elapsed: '' });
  assert.deepEqual(filingStatus('running', { started: 'nope', step: 7 }, at(3)), { text: 'Filing…', elapsed: '' });
});

test('a clock behind the start never shows negative seconds', () => {
  assert.equal(filingStatus('running', { started, step: null }, at(0) - 5000).elapsed, '0 s');
});

test('queued waits its turn', () => {
  assert.deepEqual(filingStatus('queued', { started, step: 'x' }, at(9)), { text: 'Waiting to be filed…', elapsed: '' });
});
