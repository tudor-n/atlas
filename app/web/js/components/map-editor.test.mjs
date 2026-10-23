// Run with: node --test app/web/js/components/map-editor.test.mjs (pure logic, no DOM needed).
import test from 'node:test';
import assert from 'node:assert/strict';
import { slugify, validateMap, defaultMap, normalizeMap, pathOs, renameProjectRefs, dropProjectRefs } from './map-editor.js';

test('slugify follows brainmap.slugify', () => {
  assert.equal(slugify('Acme Café'), 'acme-cafe');
  assert.equal(slugify('  Signals & Systems! '), 'signals-systems');
  assert.equal(slugify('Über   Cool'), 'uber-cool');
  assert.equal(slugify('***'), '');
});

test('default map is valid', () => {
  assert.deepEqual(Object.values(validateMap(defaultMap())).flat(), []);
});

test('validateMap: slug format, duplicates, company, unknown project, reserved slugs', () => {
  const m = defaultMap();
  m.projects.push({ slug: 'Bad Slug', name: 'A', is_company: true, folders: [] },
    { slug: 'a', name: 'B', is_company: true, folders: [] }, { slug: 'a', name: 'C', is_company: false, folders: [] },
    { slug: 'log', name: 'L', is_company: false, folders: [] });
  m.people.push({ slug: 'ana', name: 'Ana', projects: ['nope'] }, { slug: 'people', name: 'P', projects: [] });
  m.areas.push({ slug: 'log', name: 'Log' }, { slug: 'extra', name: 'Extra' });
  const e = validateMap(m);
  assert.ok(e.projects.some((x) => /must use only a-z/.test(x)));
  assert.ok(e.projects.some((x) => /already uses/.test(x)));
  assert.ok(e.projects.some((x) => /Only one project/.test(x)));
  assert.ok(e.projects.some((x) => /"log" is reserved/.test(x)));
  assert.ok(e.people.some((x) => /doesn't exist/.test(x)));
  assert.ok(e.people.some((x) => /"people" is reserved/.test(x)));
  assert.ok(e.areas.some((x) => /"log" is reserved/.test(x)) && !e.areas.some((x) => /extra/.test(x)));
});

test('pathOs follows brainmap.path_os', () => {
  assert.equal(pathOs('D:\\RobotArmControl'), 'windows');
  assert.equal(pathOs('d:/x'), 'windows');
  assert.equal(pathOs('\\\\nas\\share'), 'windows');
  assert.equal(pathOs('/home/u/dev/x'), 'linux');
  for (const odd of ['relative\\x', 'D:', '', null]) assert.equal(pathOs(odd), null);
});

test('normalizeMap: os_folders round-trip, legacy folders move to their column and folders is dropped', () => {
  const m = normalizeMap({ projects: [{ slug: 'robotarm', name: 'RobotArm', is_company: false,
    folders: ['D:\\Old', '/home/u/old', 'relative', 'D:\\RobotArmControl'],
    os_folders: { windows: ['D:\\RobotArmControl'], linux: ['/home/u/dev/RobotArmControl', 5], mac: ['/x'] } }] });
  const p = m.projects[0];
  assert.ok(!('folders' in p));
  // A legacy entry of no known shape lands under Windows (maps from before dual boot were made there),
  // where validateMap flags it instead of it being dropped silently.
  assert.deepEqual(p.os_folders, { windows: ['D:\\RobotArmControl', 'D:\\Old', 'relative'],
    linux: ['/home/u/dev/RobotArmControl', '/home/u/old'] });
  assert.deepEqual(normalizeMap(JSON.parse(JSON.stringify(m))), m);
  assert.deepEqual(normalizeMap({ projects: [{ slug: 'x', name: 'X' }] }).projects[0].os_folders, { windows: [], linux: [] });
});

test('validateMap: a folder in the wrong column is a project error', () => {
  const m = defaultMap();
  const p = { slug: 'robotarm', name: 'RobotArm', is_company: false,
    os_folders: { windows: ['/home/u/x', 'relative'], linux: ['D:\\RobotArmControl'] } };
  m.projects.push(p);
  const e = validateMap(m).projects;
  assert.ok(e.some((x) => x.includes('/home/u/x') && x.includes('Folders on Linux')));
  assert.ok(e.some((x) => x.includes('D:\\RobotArmControl') && x.includes('Folders on Windows')));
  assert.ok(e.some((x) => x.includes('"relative"')));
  p.os_folders = { windows: ['D:\\RobotArmControl', '\\\\nas\\x'], linux: ['/home/u/x'] };
  assert.deepEqual(validateMap(m).projects, []);
});

test('typing a second project through another project\'s slug does not steal its people', () => {
  const m = defaultMap();
  const a = { slug: 'robot-arm', name: 'Robot Arm', is_company: false, folders: [] };
  const b = { slug: '', name: '', is_company: false, folders: [] };
  m.projects.push(a, b);
  m.people.push({ slug: 'ana', name: 'Ana', projects: ['robot-arm'] });
  // B is typed "Robot Arm 2": its slug walks through robot-arm on the way to robot-arm-2
  let prev = b.slug;
  for (const s of ['r', 'robot', 'robot-arm', 'robot-arm-2']) { b.slug = s; renameProjectRefs(m, prev, s); prev = s; }
  assert.deepEqual(m.people[0].projects, ['robot-arm']);
});

test('renaming the owning project moves its links; removing drops them', () => {
  const m = defaultMap();
  const a = { slug: 'robot-arm', name: 'Robot Arm', is_company: false, folders: [] };
  m.projects.push(a);
  m.people.push({ slug: 'ana', name: 'Ana', projects: ['robot-arm'] });
  a.slug = 'robot-arm-2'; renameProjectRefs(m, 'robot-arm', 'robot-arm-2');
  assert.deepEqual(m.people[0].projects, ['robot-arm-2']);
  m.projects = []; dropProjectRefs(m, 'robot-arm-2');
  assert.deepEqual(m.people[0].projects, []);
});

test('savedNodes marks the loaded nodes only (their slugs are read-only in Settings)', async () => {
  const { savedNodes } = await import('./map-editor.js');
  const m = defaultMap();
  m.projects.push({ slug: 'robotarm', name: 'RobotArm', is_company: false, folders: [] });
  const saved = savedNodes(m);
  const fresh = { slug: '', name: '', is_company: false, folders: [] };
  m.projects.push(fresh);
  assert.ok(saved.has(m.projects[0]) && saved.has(m.areas[0]));
  assert.ok(!saved.has(fresh));
});

test('invalidFields marks the field each validateMap problem sits in', async () => {
  const { invalidFields } = await import('./map-editor.js');
  const m = defaultMap();
  assert.deepEqual(invalidFields(m), []);
  m.areas.push({ slug: 'Bad Slug', name: '' });
  m.projects.push(
    { slug: 'robot-arm', name: 'Robot arm', is_company: true, os_folders: { windows: ['/home/tudor/dev/RobotArmControl'], linux: ['/home/tudor/dev/RobotArmControl'] } },
    { slug: 'robot-arm', name: 'Robot arm 2', is_company: true, os_folders: { windows: [], linux: ['D:\\RobotArmControl', 'relative'] } },
  );
  m.courses.push({ slug: 'people', name: 'People' });
  m.people.push({ slug: 'andrei', name: 'Andrei', projects: ['nope'] });
  assert.deepEqual(invalidFields(m), [
    { key: 'areas', index: 4, field: 'slug' },
    { key: 'areas', index: 4, field: 'name' },
    { key: 'projects', index: 1, field: 'slug' },
    { key: 'courses', index: 0, field: 'slug' },
    { key: 'projects', index: 0, field: 'folders-windows' },
    { key: 'projects', index: 1, field: 'folders-linux' },
    { key: 'projects', index: 0, field: 'company' },
    { key: 'projects', index: 1, field: 'company' },
  ]);
  // every list with a problem in validateMap has at least one marked field, except a person's missing project
  const errs = validateMap(m);
  for (const key of ['areas', 'projects', 'courses']) {
    assert.ok(errs[key].length && invalidFields(m).some((x) => x.key === key), key);
  }
});
