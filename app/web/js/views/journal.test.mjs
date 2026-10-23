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

test('dayLabel: the weekday of the calendar date, odd input kept', async () => {
  const { dayLabel } = await import('./journal.js');
  assert.equal(dayLabel('2026-10-24'), '2026-10-24 · SAT');
  assert.equal(dayLabel('2026-10-19'), '2026-10-19 · MON');
  assert.equal(dayLabel('2024-02-29'), '2024-02-29 · THU');
  assert.equal(dayLabel(''), '');
  assert.equal(dayLabel('soon'), 'soon');
});

test('areaCounts: per area over the loaded entries, the selected one kept at 0', async () => {
  const { areaCounts } = await import('./journal.js');
  const es = [
    { filing: { files: ['projects/robotarm/notes.md', 'uni/thesis.md'] } },
    { filing: { files: ['projects/robotarm/parts.md'] } },
    { filing: null },
  ];
  assert.deepEqual(areaCounts(es), [['projects/robotarm', 2], ['uni', 1]]);
  assert.deepEqual(areaCounts(es, 'personal'), [['personal', 0], ['projects/robotarm', 2], ['uni', 1]]);
  assert.deepEqual(areaCounts([]), []);
});
