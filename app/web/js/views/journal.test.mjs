// Run with: node --test app/web/js/views/journal.test.mjs (pure logic, no DOM needed).
import test from 'node:test';
import assert from 'node:assert/strict';
import { entryAreas } from './journal.js';

test('entryAreas: top folder, or projects/<slug> for projects', () => {
  const e = { filing: { files: ['projects/robotarm/notes.md', 'people/ana.md', 'general/x.md',
    'projects/robotarm/wrist.md', { path: 'uni/fizica/lab.md' }, 'projects/readme.md'] } };
  assert.deepEqual(entryAreas(e), ['general', 'people', 'projects', 'projects/robotarm', 'uni']);
});

test('entryAreas: no filing, no files, odd separators', () => {
  assert.deepEqual(entryAreas({ filing: null }), []);
  assert.deepEqual(entryAreas({}), []);
  assert.deepEqual(entryAreas({ filing: { files: ['projects\\tracegreen\\plan.md', ''] } }), ['projects/tracegreen']);
});
