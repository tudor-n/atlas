import test from 'node:test';
import assert from 'node:assert/strict';
import { Twice } from './press-model.js';

test('the first tap arms, a second within the window goes', () => {
  const t = new Twice();
  assert.equal(t.ms, 2000);
  assert.equal(t.armed(0), false);
  assert.equal(t.tap(1000), 'armed');
  assert.equal(t.armed(1500), true);
  assert.equal(t.tap(2999), 'go');
  assert.equal(t.armed(3000), false);
});

test('after going, the next tap arms again', () => {
  const t = new Twice(500);
  t.tap(0);
  assert.equal(t.tap(100), 'go');
  assert.equal(t.tap(200), 'armed');
  assert.equal(t.tap(300), 'go');
});

test('once the window runs out it re-arms instead of going', () => {
  const t = new Twice(2000);
  assert.equal(t.tap(0), 'armed');
  assert.equal(t.armed(1999), true);
  assert.equal(t.armed(2000), false);
  assert.equal(t.tap(2000), 'armed');
  assert.equal(t.tap(3999), 'go');
});

test('reset disarms', () => {
  const t = new Twice();
  t.tap(0);
  t.reset();
  assert.equal(t.armed(10), false);
  assert.equal(t.tap(10), 'armed');
});

test('a clock that steps back never fires', () => {
  const t = new Twice();
  t.tap(5000);
  assert.equal(t.armed(4000), false);
  assert.equal(t.tap(4000), 'armed');
});
