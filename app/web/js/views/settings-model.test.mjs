// Run with: node --test web/js/views/settings-model.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  prefLabel, backupDot, integrationDot, tabletDot, voiceDot, syncDot, backupRows, counts, hierarchyRows, stepState,
} from './settings-model.js';

test('prefLabel keeps the old field labels', () => {
  assert.equal(prefLabel('backup_debounce_s'), 'Backup debounce s');
  assert.equal(prefLabel('arm_url'), 'Arm url');
});

test('backupDot: amber on error, green after a push, nothing otherwise', () => {
  assert.equal(backupDot(null), null);
  assert.equal(backupDot({ remote_set: false, last_error: 'x' }), null);
  assert.equal(backupDot({ remote_set: true, last_ok: null, last_error: null }), null);
  assert.equal(backupDot({ remote_set: true, last_ok: '2026-10-24T10:00:00', last_error: null }), 'g');
  assert.equal(backupDot({ remote_set: true, last_ok: '2026-10-24T10:00:00', last_error: 'denied' }), 'a');
});

test('integrationDot', () => {
  assert.equal(integrationDot(null), null);
  assert.equal(integrationDot({ hooks_installed: false, statusline_installed: false }), null);
  const ok = { hooks_installed: true, statusline_installed: true, port_matches: true, paths_current: true };
  assert.equal(integrationDot(ok), 'g');
  assert.equal(integrationDot({ ...ok, port_matches: false }), 'a');
  assert.equal(integrationDot({ ...ok, paths_current: false }), 'a');
  assert.equal(integrationDot({ ...ok, statusline_installed: false }), 'a');
});

test('tabletDot and voiceDot', () => {
  assert.equal(tabletDot({ tablet_lan: false }), null);
  assert.equal(tabletDot({ tablet_lan: true, listening: '192.168.0.200' }), 'g');
  assert.equal(tabletDot({ tablet_lan: true, listening: null }), 'a');
  const v = { available: true, installed: true, enabled: true, paused_until: null, last_error: null, download: {} };
  assert.equal(voiceDot(v), 'g');
  assert.equal(voiceDot({ ...v, enabled: false }), null);
  assert.equal(voiceDot({ ...v, paused_until: '2026-10-24T12:00' }), null);
  assert.equal(voiceDot({ ...v, last_error: 'no audio device' }), 'a');
  assert.equal(voiceDot({ ...v, installed: false, download: { error: 'timeout' } }), 'a');
  assert.equal(voiceDot({ ...v, installed: false, download: { running: true } }), null);
  assert.equal(voiceDot({ available: false }), null);
});

test('syncDot', () => {
  assert.equal(syncDot({ summary: null }, [{ problem: false }]), null);
  assert.equal(syncDot({ summary: { available: true } }, [{ problem: false }]), 'g');
  assert.equal(syncDot({ summary: { available: true } }, [{ problem: true }]), 'a');
  assert.equal(syncDot({ summary: { available: false } }, []), null);
});

test('backupRows', () => {
  assert.deepEqual(backupRows(null), []);
  const rows = backupRows({ remote_set: true, last_ok: 'T', pending: false, last_error: 'denied' }, (x) => `at ${x}`);
  assert.deepEqual(rows, [
    ['Remote', 'set', ''], ['Last successful push', 'at T', 'm'], ['Waiting to push', 'no', ''], ['Last error', 'denied', 'err'],
  ]);
  assert.deepEqual(backupRows({ remote_set: false, last_ok: null, pending: true, last_error: null })[1], ['Last successful push', 'never', 'f']);
});

test('hierarchyRows flattens depth first without the root', () => {
  const tree = { name: 'brain', files: 4, items: 3, children: [
    { name: 'Projects', kind: 'area', slug: 'projects', files: 2, items: 1, children: [
      { name: 'Robot arm', kind: 'project', slug: 'robot-arm', files: 1, items: 1, children: [] }] },
    { name: 'Uni', kind: 'area', slug: 'uni', files: 0, items: 0, children: [] }] };
  assert.deepEqual(hierarchyRows(tree).map((r) => [r.name, r.depth, r.counts]), [
    ['Projects', 0, '2 files · 1 item'], ['Robot arm', 1, '1 file · 1 item'], ['Uni', 0, '0 files · 0 items']]);
  assert.equal(counts({ files: 1, items: 2 }), '1 file · 2 items');
  assert.deepEqual(hierarchyRows(null), []);
});

test('stepState', () => {
  assert.deepEqual([0, 1, 2, 3].map((i) => stepState(i, 2)), ['done', 'done', 'cur', 'todo']);
});
