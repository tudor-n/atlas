// Run with: node --test app/web/tablet/tablet.test.mjs (the tablet's pure ES5 logic, no DOM).
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { Keyboard, withUnit } = require('./kb.js');
const { ConfirmTap, HoldOrDouble, Backoff, Store, SendQueue } = require('./logic.js');

const UNITS = ['mm', 'cm', 'm', '°', 'mA', 'A', 'Ω'];

test('typing, shift once and caps lock', () => {
  const k = new Keyboard(UNITS);
  k.reset('', 'text', true, 100);
  k.press('shift', 0); k.press('h', 10); k.press('i', 20);
  assert.equal(k.text, 'Hi');
  k.press('shift', 1000); k.press('shift', 1200);  // two taps within 400 ms: lock
  k.press('o', 1300); k.press('k', 1400);
  assert.equal(k.text, 'HiOK');
  k.press('shift', 3000); k.press('x', 3100);  // a tap on a locked shift unlocks
  assert.equal(k.text, 'HiOKx');
});

test('space, enter, backspace and the length cap', () => {
  const k = new Keyboard(UNITS);
  k.reset('', 'text', true, 5);
  k.press('space', 0);
  assert.equal(k.text, '');  // no leading space
  k.press('a', 0); k.press('space', 0); k.press('space', 0);
  assert.equal(k.text, 'a ');
  k.press('enter', 0); k.press('b', 0); k.press('c', 0); k.press('d', 0);
  assert.equal(k.text, 'a \nbc');  // capped at 5
  k.press('bksp', 0);
  assert.equal(k.text, 'a \nb');
  const one = new Keyboard(UNITS);
  one.reset('412', 'number', false, 200);
  assert.equal(one.press('enter', 0), 'done');  // a one-line field: enter means SET
  assert.equal(one.press('hide', 0), 'hide');
});

test('modes: a number field starts on the keypad, a note on letters', () => {
  const k = new Keyboard(UNITS);
  k.reset('', 'number', false, 200);
  assert.equal(k.mode, 'num');
  k.press('mode:abc', 0); k.press('i', 0); k.press('d', 0);
  assert.equal(k.text, 'id');
  const n = new Keyboard(UNITS);
  n.reset('', 'text', true, 200);
  n.press('mode:num', 0);
  assert.equal(n.mode, 'sym');  // notes have no unit keypad
});

test('unit chips replace the unit, never stack', () => {
  assert.equal(withUnit('412', 'mm', UNITS), '412 mm');
  assert.equal(withUnit('412 mm', 'cm', UNITS), '412 cm');
  assert.equal(withUnit('412mm', 'm', UNITS), '412 m');
  assert.equal(withUnit('3 mA', 'A', UNITS), '3 A');
  assert.equal(withUnit('90 °', 'mm', UNITS), '90 mm');
  assert.equal(withUnit('', 'mm', UNITS), '');  // no value, no unit
  assert.equal(withUnit('arm', 'mm', UNITS), 'arm mm');  // "arm" doesn't end in a unit after a digit
  const k = new Keyboard(UNITS);
  k.reset('', 'number', false, 200);
  k.press('4', 0); k.press('1', 0); k.press('2', 0); k.press('unit:mm', 0);
  assert.equal(k.text, '412 mm');
});

test('IGNORE needs a second tap within 2 s', () => {
  const c = new ConfirmTap(2000);
  assert.equal(c.tap(0), 'armed');
  assert.equal(c.expired(1999), false);
  assert.equal(c.expired(2001), true);
  assert.equal(c.tap(2500), 'armed');  // too late: armed again
  assert.equal(c.tap(3000), 'confirmed');
});

test('HOME: hold 600 ms or double-tap within 400 ms', () => {
  const h = new HoldOrDouble(600, 400);
  h.down(0);
  assert.equal(h.check(300), null);
  assert.equal(h.check(650), 'go');
  assert.equal(h.up(700), null);  // already fired while held
  h.down(1000); assert.equal(h.up(1100), 'tap');
  h.down(1300); assert.equal(h.up(1350), 'go');  // second tap 250 ms after the first
  h.down(2000); assert.equal(h.up(2050), 'tap');
  h.down(3000); assert.equal(h.up(3050), 'tap');  // too slow for a double
  h.down(4000); h.cancel(); assert.equal(h.up(4100), null);
});

test('backoff', () => {
  const b = new Backoff();
  assert.deepEqual([b.fail(), b.fail(), b.fail(), b.fail(), b.fail(), b.fail()], [2000, 4000, 8000, 16000, 30000, 30000]);
  assert.equal(b.failing(), true);
  b.ok();
  assert.equal(b.failing(), false);
});

function memory() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}

test('the send queue survives a reload, in order', () => {
  const s = new Store(memory());
  const q = new SendQueue(s, 'q');
  q.push({ kind: 'note', text: 'a' });
  q.push({ kind: 'answer', id: 'r-1', answers: { 0: '412 mm' } });
  const again = new SendQueue(s, 'q');
  assert.equal(again.size(), 2);
  assert.equal(again.peek().text, 'a');
  again.shift();
  assert.equal(new SendQueue(s, 'q').peek().id, 'r-1');
  assert.ok(again.has((x) => x.id === 'r-1'));
});

test('storage that throws or is missing falls back', () => {
  const broken = new Store({ getItem() { throw new Error('denied'); }, setItem() { throw new Error('full'); }, removeItem() {} });
  assert.equal(broken.get('x', 7), 7);
  assert.equal(broken.set('x', 1), false);
  const none = new Store(null);
  assert.deepEqual(new SendQueue(none, 'q').items, []);
  const junk = memory(); junk.setItem('q', '{"not":"a list"}');
  assert.deepEqual(new SendQueue(new Store(junk), 'q').items, []);
});

test('new units never touch the text being typed (review)', () => {
  const k = new Keyboard(['mm']);
  k.reset('', 'number', false, 200);
  k.press('4', 0); k.press('2', 0);
  k.setUnits(['cm', 'mm']);
  assert.equal(k.text, '42');
  assert.equal(k.mode, 'num');
  k.press('unit:cm', 0);
  assert.equal(k.text, '42 cm');
});

test('a refused answer kept as a note goes back to the front of the queue', () => {
  const q = new SendQueue(new Store(memory()), 'q');
  q.push({ kind: 'note', text: 'later' });
  q.unshift({ kind: 'note', text: 'first' });
  assert.equal(q.peek().text, 'first');
});
