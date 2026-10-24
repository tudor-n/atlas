import test from 'node:test';
import assert from 'node:assert/strict';
import { LEGACY_KEYS, adoptLegacy } from './legacy-keys.js';
import { DRAFT_KEY, loadDraft } from './widget-model.js';

function mem(init = {}) {
  const m = new Map(Object.entries(init));
  return { m, getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}

test('the two drafts have a legacy key each', () => {
  assert.deepEqual(Object.keys(LEGACY_KEYS).sort(), ['atlas.setup.v1', 'atlas.widget.draft']);
  assert.equal(LEGACY_KEYS[DRAFT_KEY], 'brainhub.widget.draft');
});

test('the old key is read once when the new one is absent, then moved', () => {
  const s = mem({ 'brainhub.setup.v1': '{"step":2}' });
  adoptLegacy(s, 'atlas.setup.v1');
  assert.equal(s.getItem('atlas.setup.v1'), '{"step":2}');
  assert.equal(s.getItem('brainhub.setup.v1'), null);
  s.setItem('atlas.setup.v1', '{"step":3}');
  adoptLegacy(s, 'atlas.setup.v1');
  assert.equal(s.getItem('atlas.setup.v1'), '{"step":3}');
});

test('a new key that is there wins, and the old one is left alone', () => {
  const s = mem({ 'atlas.widget.draft': 'new', 'brainhub.widget.draft': 'old' });
  adoptLegacy(s, 'atlas.widget.draft');
  assert.equal(s.getItem('atlas.widget.draft'), 'new');
  assert.equal(s.getItem('brainhub.widget.draft'), 'old');
});

test('the widget draft typed before the rename comes back', () => {
  const s = mem({ 'brainhub.widget.draft': 'half a thought' });
  assert.equal(loadDraft(s), 'half a thought');
  assert.equal(s.getItem('brainhub.widget.draft'), null);
  assert.equal(s.getItem(DRAFT_KEY), 'half a thought');
});

test('missing, unknown or throwing storage is no error', () => {
  adoptLegacy(null, 'atlas.setup.v1');
  const s = mem({ x: '1' });
  adoptLegacy(s, 'atlas.browse');
  assert.deepEqual([...s.m.keys()], ['x']);
  const bad = { getItem() { throw new Error('blocked'); } };
  adoptLegacy(bad, 'atlas.setup.v1');
  assert.equal(loadDraft(bad), '');
});
