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
