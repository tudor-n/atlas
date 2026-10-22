// Run with: node --test web/js/views/claude-sync-model.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseKeys, syncNotes } from './claude-sync-model.js';

test('parseKeys: commas, spaces and new lines; no blanks or repeats', () => {
  assert.deepEqual(parseKeys('theme, tui\noutputStyle  theme,,'), ['theme', 'tui', 'outputStyle']);
  assert.deepEqual(parseKeys(''), []);
  assert.deepEqual(parseKeys(undefined), []);
});

test('syncNotes: never run, and a store that was not there', () => {
  assert.deepEqual(syncNotes({ summary: null, available: true }),
    [{ text: "claude-sync hasn't run on this OS yet.", problem: false }]);
  const lines = syncNotes({ available: false, reason: 'shared store not found at /mnt/data/ClaudeShared',
    summary: { ran_at: '2026-10-22T09:00:00', available: false, reason: 'shared store not found at /mnt/data/ClaudeShared' } });
  assert.equal(lines.length, 2);
  assert.match(lines[0].text, /isn't available now/);
  assert.equal(lines[1].text, 'Last run 2026-10-22 09:00:00: nothing changed (shared store not found at /mnt/data/ClaudeShared).');
  assert.ok(lines.every((l) => !l.problem));
});

test('syncNotes: a full run with a clash, a failed push and a problem', () => {
  const lines = syncNotes({ available: true, summary: {
    ran_at: '2026-10-22T09:00:00', available: true,
    memory: { pairs: ['home', 'BrainHub'], pointed: [], merged: [{ into: 'memory/home', kept_both: ['a.linux.md'] }] },
    skills: { moved: ['x'], linked: [], conflicts: [] },
    settings: { recorded: ['model'], applied: [] },
    git: { repo: true, pushed: false, error: 'offline' },
    problems: { skills: ['y differs from the shared skill of that name'] },
  } });
  assert.deepEqual(lines.map((l) => l.text), [
    'Last run 2026-10-22 09:00:00: memory shared for home, BrainHub; 1 skill moved or linked; settings: 1 change sent, 0 received.',
    "Kept both versions of a note in memory/home: this OS's copy is a.linux.md.",
    'The backup push failed; it is retried at the next run. offline',
    'y differs from the shared skill of that name',
  ]);
  assert.deepEqual(lines.map((l) => l.problem), [false, false, true, true]);
});

test('syncNotes: a settings-only run and a store without git', () => {
  const lines = syncNotes({ available: true, summary: { ran_at: '2026-10-22T09:00:00', available: true,
    settings: { recorded: [], applied: ['model', 'tui'] }, git: { repo: false } } });
  assert.deepEqual(lines.map((l) => l.text), [
    'Last run 2026-10-22 09:00:00: settings: 0 changes sent, 2 received.',
    'The shared store has no git backup (run claude-sync init).',
  ]);
});
