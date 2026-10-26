import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Throttle, installPresencePing, PING_EVERY_MS } from './presence-model.js';

test('the throttle lets one through a minute', () => {
  const t = new Throttle();
  assert.equal(PING_EVERY_MS, 60000);
  assert.equal(t.take(1000), true);
  assert.equal(t.take(1001), false);
  assert.equal(t.take(60999), false);
  assert.equal(t.take(61000), true);
  assert.equal(t.take(500), true);  // the clock went backwards: let it through
  assert.equal(t.take(600), false);
});

function fakeTarget() {
  const handlers = new Map();
  return {
    handlers,
    addEventListener(type, fn, opts) { assert.equal(opts.capture, true); handlers.set(type, fn); },
    removeEventListener(type, fn) { if (handlers.get(type) === fn) handlers.delete(type); },
    fire(type) { const fn = handlers.get(type); if (fn) fn({ type }); },
  };
}

test('keys and pointer presses ping, throttled, and failures are swallowed', async () => {
  const target = fakeTarget();
  let clock = 0;
  let sent = 0;
  const off = installPresencePing(target, () => { sent++; return Promise.reject(new Error('offline')); }, { now: () => clock });
  assert.deepEqual([...target.handlers.keys()].sort(), ['keydown', 'pointerdown']);
  target.fire('keydown');
  target.fire('pointerdown');
  assert.equal(sent, 1);
  clock = 30000;
  target.fire('keydown');
  assert.equal(sent, 1);
  clock = 60000;
  target.fire('pointerdown');
  assert.equal(sent, 2);
  await new Promise((r) => setTimeout(r, 0));  // the rejected sends raise nothing
  off();
  assert.equal(target.handlers.size, 0);
});

test('a send that throws at once is swallowed too', () => {
  const target = fakeTarget();
  installPresencePing(target, () => { throw new Error('boom'); }, { now: () => 0 });
  assert.doesNotThrow(() => target.fire('keydown'));
});
