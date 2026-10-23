import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  parseHash, TABS, SETTINGS, tabByKey, tabFor, SETTINGS_SECTIONS, SETTINGS_DEFAULT, settingsSection,
  BROWSE_DEFAULT, browseTarget,
} from './nav-model.js';

const q = (r) => Object.fromEntries(r.query);

test('every route the widget can send parses and lights its tab', () => {
  // widget-model TILE_VIEW values, popup project rows, and the phase 4.5 additions
  const cases = [
    ['#/today', 'today', null, 'today'],
    ['#/usage', 'usage', null, 'agents'],
    ['#/agents', 'agents', null, 'agents'],
    ['#/people', 'people', null, 'browse'],
    ['#/deadlines', 'deadlines', null, 'browse'],
    ['#/settings', 'settings', null, 'settings'],
    ['#/browse', 'browse', null, 'browse'],
    ['#/project/robotarm', 'project', 'robotarm', 'browse'],
    ['#/project/my-course-2', 'project', 'my-course-2', 'browse'],
    ['#/settings/voice', 'settings', 'voice', 'settings'],
    ['#/ask', 'ask', null, 'ask'],
    ['#/journal', 'journal', null, 'journal'],
    ['#/tablet', 'tablet', null, 'tablet'],
    ['#/setup', 'setup', null, null],
  ];
  for (const [hash, name, param, tab] of cases) {
    const r = parseHash(hash);
    assert.equal(r.name, name, hash);
    assert.equal(r.param, param, hash);
    assert.deepEqual(r.params, param ? [param] : [], hash);
    assert.equal(tabFor(r.name), tab, hash);
  }
});

test('parse edge cases match the old router', () => {
  for (const h of ['', '#', '#/', '#//', undefined, null]) {
    const r = parseHash(h);
    assert.equal(r.name, 'today', String(h));
    assert.deepEqual(r.params, []);
    assert.equal(r.param, null);
  }
  assert.equal(parseHash('#today').name, 'today');                // no slash after #
  assert.equal(parseHash('#//journal//').name, 'journal');        // empty segments dropped
  assert.deepEqual(q(parseHash('#/journal?entry=e-1')), { entry: 'e-1' });
  assert.deepEqual(q(parseHash('#/today?notice=setup-done')), { notice: 'setup-done' });
  assert.deepEqual(q(parseHash('#/?x=1')), { x: '1' });
  assert.equal(parseHash('#/?x=1').name, 'today');
  const enc = parseHash('#/project/caf%C3%A9/a%2Fb');
  assert.deepEqual(enc.params, ['café', 'a/b']);
  assert.equal(enc.param, 'café');
  assert.deepEqual(q(parseHash('#/journal?a=1?b=2')), { a: '1' });  // a second ? ends the query, as before
  assert.equal(parseHash('#/project/%E0').param, '%E0');            // malformed escape: kept, no throw
  assert.equal(parseHash('#/nowhere').name, 'nowhere');              // the router sends unknowns to Today
  assert.equal(tabFor('nowhere'), null);
  assert.equal(tabFor('constructor'), null);
});

test('same result as the parseHash app.js has today', (t) => {
  const src = readFileSync(new URL('./app.js', import.meta.url), 'utf8');
  const m = /function parseHash\(\) \{([\s\S]*?)\n\}/.exec(src);
  if (!m) { t.skip('app.js no longer has its own parseHash'); return; }
  const old = new Function('location', m[1]);
  for (const hash of ['', '#/', '#/today', '#/usage', '#/project/robotarm', '#/journal?entry=e-1',
    '#//people//', '#/project/caf%C3%A9', '#/settings/tablet', '#browse', '#/a/b/c?x=1&y=2']) {
    const a = old({ hash });
    const b = parseHash(hash);
    assert.equal(b.name, a.name, hash);
    assert.deepEqual(b.params, a.params, hash);
    assert.equal(b.query.toString(), a.query.toString(), hash);
  }
});

test('tabs, keys and Settings', () => {
  assert.deepEqual(TABS.map((x) => x.id), ['today', 'ask', 'journal', 'browse', 'agents', 'tablet']);
  assert.deepEqual(TABS.map((x) => x.key), ['1', '2', '3', '4', '5', '6']);
  assert.deepEqual(TABS.map((x) => x.label), ['TODAY', 'ASK', 'JOURNAL', 'BROWSE', 'AGENTS', 'TABLET']);
  assert.deepEqual(SETTINGS, { id: 'settings', label: 'SETTINGS', key: '0' });
  assert.equal(tabByKey('1'), 'today');
  assert.equal(tabByKey('6'), 'tablet');
  assert.equal(tabByKey('0'), 'settings');
  assert.equal(tabByKey('7'), null);
});

test('settings sections', () => {
  assert.deepEqual(SETTINGS_SECTIONS.map((s) => s.id),
    ['map', 'hierarchy', 'preferences', 'backup', 'claude', 'tablet', 'voice', 'sync', 'about']);
  assert.equal(SETTINGS_DEFAULT, 'map');
  assert.equal(settingsSection(null), 'map');
  assert.equal(settingsSection(undefined), 'map');
  assert.equal(settingsSection(''), 'map');
  assert.equal(settingsSection('nope'), 'map');
  assert.equal(settingsSection('tablet'), 'tablet');   // the old anchors
  assert.equal(settingsSection('voice'), 'voice');
  assert.equal(settingsSection('Backup'), 'backup');
  for (const s of SETTINGS_SECTIONS) assert.equal(settingsSection(s.id), s.id);
  assert.equal(settingsSection(parseHash('#/settings/sync').param), 'sync');
});

test('browse opens the last-used entry, else deadlines', () => {
  assert.equal(BROWSE_DEFAULT, 'deadlines');
  assert.equal(browseTarget(null), 'deadlines');
  assert.equal(browseTarget(''), 'deadlines');
  assert.equal(browseTarget(42), 'deadlines');
  assert.equal(browseTarget('people'), 'people');
  assert.equal(browseTarget('deadlines'), 'deadlines');
  assert.equal(browseTarget('project/robotarm'), 'project/robotarm');
  assert.equal(browseTarget('project/'), 'deadlines');
  assert.equal(browseTarget('project/Bad Slug'), 'deadlines');
  assert.equal(browseTarget('project/a/b'), 'deadlines');
  assert.equal(browseTarget('today'), 'deadlines');
  assert.equal(browseTarget('browse'), 'deadlines');
});
