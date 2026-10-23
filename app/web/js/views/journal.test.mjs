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

test('parseDay: yyyy-mm-dd (padded), yyyymmdd, empty for all days, null when not a date', async () => {
  const { parseDay } = await import('./journal.js');
  assert.equal(parseDay('2026-10-24'), '2026-10-24');
  assert.equal(parseDay(' 2026-1-5 '), '2026-01-05');
  assert.equal(parseDay('20261024'), '2026-10-24');
  assert.equal(parseDay(''), '');
  assert.equal(parseDay('  '), '');
  assert.equal(parseDay('2026-02-30'), null);
  assert.equal(parseDay('2026-13-01'), null);
  assert.equal(parseDay('10/24/2026'), null);
  assert.equal(parseDay('soon'), null);
  assert.equal(parseDay('2024-02-29'), '2024-02-29');
});

test('addDays: across months and years; odd input kept', async () => {
  const { addDays } = await import('./journal.js');
  assert.equal(addDays('2026-10-24', 1), '2026-10-25');
  assert.equal(addDays('2026-10-31', 1), '2026-11-01');
  assert.equal(addDays('2026-01-01', -1), '2025-12-31');
  assert.equal(addDays('2024-02-28', 1), '2024-02-29');
  assert.equal(addDays('', 1), '');
});

test('areaName: map names for projects and areas, slugs for what the map does not know', async () => {
  const { areaName } = await import('./journal.js');
  const { mapNames } = await import('./today-model.js');
  const names = mapNames({ areas: [{ slug: 'uni', name: 'Uni' }, { slug: 'projects', name: 'Projects' }],
    projects: [{ slug: 'robot-arm', name: 'Robot arm' }], people: [{ slug: 'robot-arm', name: 'Not a project' }] });
  assert.equal(areaName('projects/robot-arm', names), 'Robot arm');
  assert.equal(areaName('uni', names), 'Uni');
  assert.equal(areaName('projects', names), 'Projects');
  assert.equal(areaName('people', names), 'People');
  assert.equal(areaName('projects/old-thing', names), 'old-thing');
  assert.equal(areaName('uni', mapNames(null)), 'Uni');
});
