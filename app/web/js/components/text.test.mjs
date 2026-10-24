// Run with: node --test app/web/js/components/text.test.mjs (pure logic, no DOM needed).
import test from 'node:test';
import assert from 'node:assert/strict';
import { isEntryRef, isNoteRef } from './text.js';

test('note refs link with or without a heading', () => {
  assert.ok(isNoteRef('projects/robotarm/notes.md#Wrist'));
  assert.ok(isNoteRef('projects/robotarm/notes.md'));
  assert.ok(isNoteRef('personal/rețete.md'));
  assert.ok(!isNoteRef('notes.txt') && !isNoteRef('#x.md') && !isNoteRef(' notes.md') && !isNoteRef('a.md#'));
});

test('entry refs', () => {
  assert.ok(isEntryRef('e-20261020-140322-ab12'));
  assert.ok(!isEntryRef('e-1'));
});

test('filingFiles, tsParts and filingState', async () => {
  const { filingFiles, tsParts, filingState } = await import('./text.js');
  assert.deepEqual(filingFiles({ files: ['a.md', { path: 'b/c.md' }, '', null] }), ['a.md', 'b/c.md']);
  assert.deepEqual(filingFiles(null), []);
  assert.deepEqual(tsParts('2026-10-24T14:03:00'), { date: '2026-10-24', time: '14:03' });
  assert.deepEqual(filingState(null), { kind: 'none', text: 'NOT FILED' });
  assert.deepEqual(filingState({ status: 'failed' }), { kind: 'failed', text: 'FILING FAILED' });
  assert.equal(filingState({ status: 'running' }).kind, 'busy');
  assert.equal(filingState({ status: 'queued' }).kind, 'busy');
  assert.deepEqual(filingState({ status: 'done', files: ['x.md'] }), { kind: 'done', text: '' });
  assert.deepEqual(filingState({ status: 'done', files: [] }), { kind: 'done', text: 'FILED · NO NOTES' });
});
