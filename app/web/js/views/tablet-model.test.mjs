// Run with: node --test app/web/js/views/tablet-model.test.mjs (pure logic, no DOM needed).
import test from 'node:test';
import assert from 'node:assert/strict';
import { countdown, lanLine, noteState, parseUnits } from './tablet-model.js';

test('a tablet note is filed only when the owner asks', () => {
  assert.deepEqual(noteState({ filing: null }), { label: 'not filed', canFile: true });
  assert.equal(noteState({ filing: 'running' }).canFile, false);
  assert.deepEqual(noteState({ filing: 'failed' }), { label: 'filing failed', canFile: true });
  assert.deepEqual(noteState({ filing: 'done' }), { label: 'filed', canFile: false });
});

test('the LAN line says what the hub is doing', () => {
  const base = { tablet_lan: true, tablet_address: '192.168.0.200', port: 8090, listening: null, error: null };
  assert.match(lanLine({ ...base, tablet_lan: false }), /^Off/);
  assert.match(lanLine({ ...base, tablet_address: '' }), /no address/);
  assert.equal(lanLine({ ...base, listening: '192.168.0.200' }), 'Listening on http://192.168.0.200:8090/tablet');
  assert.match(lanLine({ ...base, error: 'Address already in use' }), /Can't listen on 192\.168\.0\.200: Address already in use/);
  assert.match(lanLine(base), /doesn't have 192\.168\.0\.200 right now/);
});

test('units and the countdown', () => {
  assert.deepEqual(parseUnits(' mm, cm,,°, '), ['mm', 'cm', '°']);
  assert.equal(countdown(299.4), '4:59');
  assert.equal(countdown(-3), '0:00');
});

test('noteDot: green filed, grey filing, amber failed, none before filing', async () => {
  const { noteDot } = await import('./tablet-model.js');
  assert.equal(noteDot({ filing: null }), null);
  assert.equal(noteDot({ filing: 'queued' }), '');
  assert.equal(noteDot({ filing: 'running' }), '');
  assert.equal(noteDot({ filing: 'failed' }), 'a');
  assert.equal(noteDot({ filing: 'done' }), 'g');
});

test('shortWhen: the time today, month-day this year, the full date otherwise', async () => {
  const { shortWhen } = await import('./tablet-model.js');
  const now = new Date(2026, 9, 24, 12, 0);
  assert.equal(shortWhen('2026-10-24T00:51:23', now), '00:51');
  assert.equal(shortWhen('2026-10-21T09:00:00', now), '10-21 09:00');
  assert.equal(shortWhen('2025-12-31T23:59', now), '2025-12-31 23:59');
  assert.equal(shortWhen('2026-10-24', now), '10-24');
  assert.equal(shortWhen('garbage', now), 'garbage');
  assert.equal(shortWhen(null, now), '');
});
