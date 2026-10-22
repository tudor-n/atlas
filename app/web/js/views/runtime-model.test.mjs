// Run with: node --test web/js/views/runtime-model.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { runtimeText } from './runtime-model.js';

const when = (iso) => `<${iso}>`;
const base = { commit: '306d896abcdef0123456789abcdef0123456789a', slot: 'a', checked_at: '2026-10-22T14:05:09',
  pending: null, shell_update: false };

test('runtimeText: no line in dev or legacy mode', () => {
  assert.equal(runtimeText(null, when), '');
  assert.equal(runtimeText(undefined, when), '');
});

test('runtimeText: short commit, slot and last check', () => {
  assert.equal(runtimeText(base, when), 'Runtime: 306d896 (slot a) · checked <2026-10-22T14:05:09>');
});

test('runtimeText: pending reason and widget update only when they apply', () => {
  assert.equal(runtimeText({ ...base, pending: 'CI pending' }, when),
    'Runtime: 306d896 (slot a) · checked <2026-10-22T14:05:09> · update pending: CI pending');
  assert.equal(runtimeText({ ...base, slot: 'b', pending: 'dirty working tree', shell_update: true }, when),
    'Runtime: 306d896 (slot b) · checked <2026-10-22T14:05:09> · update pending: dirty working tree · widget update available');
});

test('runtimeText: unknown parts stay quiet', () => {
  assert.equal(runtimeText({ commit: null, slot: null, checked_at: null, pending: null, shell_update: false }, when),
    'Runtime: unknown commit · not checked yet');
});

test('runtimeText: the default time format is the local one', () => {
  assert.equal(runtimeText(base), `Runtime: 306d896 (slot a) · checked ${new Date(base.checked_at).toLocaleString()}`);
});
