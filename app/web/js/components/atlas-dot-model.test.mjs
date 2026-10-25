import test from 'node:test';
import assert from 'node:assert/strict';
import { levelAt, radiusFor, step, settled, REST, PEAK, BOX } from './atlas-dot-model.js';

test('the level follows the envelope frame by frame, then runs out', () => {
  const env = [0, 25, 100, 49];
  assert.equal(levelAt(env, 40, 0), 0);
  assert.equal(levelAt(env, 40, 39.9), 0);
  assert.equal(levelAt(env, 40, 40), 0.5);
  assert.equal(levelAt(env, 40, 80), 1);
  assert.equal(levelAt(env, 40, 150), 0.7);
  assert.equal(levelAt(env, 40, 160), null);
});

test('a bad envelope or clock gives no level, and bad values are quiet', () => {
  assert.equal(levelAt(null, 40, 0), null);
  assert.equal(levelAt([50], 0, 0), null);
  assert.equal(levelAt([50], 40, -1), null);
  assert.equal(levelAt([], 40, 0), null);
  assert.equal(levelAt(['x', 400, -5], 40, 0), 0);
  assert.equal(levelAt(['x', 400, -5], 40, 40), 1);
  assert.equal(levelAt(['x', 400, -5], 40, 80), 0);
});

test('the radius spans rest to peak inside the box', () => {
  assert.equal(radiusFor(0), REST);
  assert.equal(radiusFor(null), REST);
  assert.equal(radiusFor(1), PEAK);
  assert.ok(PEAK <= BOX / 2 && REST < PEAK);
});

test('it swells faster than it settles, whatever the frame rate', () => {
  const up = step(REST, PEAK, 16) - REST;
  const down = PEAK - step(PEAK, REST, 16);
  assert.ok(up > down && up > 0 && down > 0);
  // two 8 ms steps land where one 16 ms step does
  assert.ok(Math.abs(step(step(REST, PEAK, 8), PEAK, 8) - step(REST, PEAK, 16)) < 1e-9);
  assert.equal(step(20, 25, 0), 20);
  assert.equal(step(20, 25, -5), 20);
  assert.ok(Math.abs(step(REST, PEAK, 10000) - PEAK) < 1e-6);
});

test('settled is close to rest', () => {
  assert.ok(settled(REST) && settled(REST + 0.04));
  assert.ok(!settled(REST + 0.1) && !settled(PEAK));
});
