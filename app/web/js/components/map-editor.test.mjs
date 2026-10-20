// Run with: node --test app/web/js/components/map-editor.test.mjs (pure logic, no DOM needed).
import test from 'node:test';
import assert from 'node:assert/strict';
import { slugify, validateMap, defaultMap, renameProjectRefs, dropProjectRefs } from './map-editor.js';

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
