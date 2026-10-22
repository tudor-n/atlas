import test from 'node:test';
import assert from 'node:assert/strict';
import { heldText, driveText, readonlyReason } from './drive.js';

const MSG = "BrainHub's drive isn't available.";

test('held count', () => {
  assert.equal(heldText(0), '');
  assert.equal(heldText(undefined), '');
  assert.equal(heldText(1), '1 capture held on this OS');
  assert.equal(heldText(3), '3 captures held on this OS');
});

test('banner text', () => {
  assert.equal(driveText(null), '');
  assert.equal(driveText({ state: 'missing', message: MSG, held: 0 }), MSG);
  assert.equal(driveText({ state: 'readonly', message: MSG, held: 2 }), `${MSG} · 2 captures held on this OS`);
});

test('read-only reason disables confirm and tick', () => {
  assert.equal(readonlyReason(null), '');
  assert.equal(readonlyReason({ state: 'missing', message: MSG, held: 0 }), '');
  assert.equal(readonlyReason({ state: 'readonly', message: MSG, held: 1 }), MSG);
  assert.ok(readonlyReason({ state: 'readonly', message: '' }));  // never an empty title
});
