// Run with: node --test web/js/views/agents-model.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { STATUS_LABEL, statusText, sinceText, nextTimeText, resetText, linkText } from './agents-model.js';

const now = new Date(2026, 9, 21, 12, 0, 0);

test('STATUS_LABEL', () => {
  assert.deepEqual(STATUS_LABEL, { idle: 'ready', running: 'running', waiting: 'waiting for you', finished: 'finished' });
});

test('sinceText: just now, minutes, hours, days', () => {
  assert.equal(sinceText('2026-10-21T11:59:30', now), 'just now');
  assert.equal(sinceText('2026-10-21T12:00:00', now), 'just now');
  assert.equal(sinceText('2026-10-21T11:55:00', now), '5 min');
  assert.equal(sinceText('2026-10-21T09:55:00', now), '2 h 5 min');
  assert.equal(sinceText('2026-10-18T12:00:00', now), '3 d');
});

test('nextTimeText: plural, singular, none', () => {
  assert.equal(nextTimeText({ next_time: 2, project_name: 'robotarm' }), '2 next-time notes for robotarm');
  assert.equal(nextTimeText({ next_time: 1, project_name: 'robotarm' }), '1 next-time note for robotarm');
  assert.equal(nextTimeText({ next_time: 0, project_name: 'robotarm' }), null);
  assert.equal(nextTimeText({ project_name: 'robotarm' }), null);
});

test('linkText: only an unmapped row with a suggestion offers the link', () => {
  const suggest = { slug: 'robotarm', name: 'RobotArm', folder: '/home/u/dev/RobotArmControl' };
  assert.equal(linkText({ mapped: false, suggest }), 'link to RobotArm');
  assert.equal(linkText({ mapped: false, suggest: null }), null);
  assert.equal(linkText({ mapped: false }), null);
  assert.equal(linkText({ mapped: true, suggest }), null);
});

test('resetText: future and passed', () => {
  assert.equal(resetText({ resets_at: '2026-10-21T14:05', reset_passed: false }, now), 'resets 14:05 (in 2 h 5 min)');
  assert.equal(resetText({ resets_at: '2026-10-21T14:20', reset_passed: true }, now), 'reset at 14:20 · no data since');
  assert.equal(resetText({ resets_at: '2026-10-22T10:30', reset_passed: false }, now), 'resets 10-22 10:30 (in 22 h 30 min)');
});

test('statusText: an ended session shows as ended whatever its last status', () => {
  assert.equal(statusText({ status: 'waiting', ended: false }), 'waiting for you');
  assert.equal(statusText({ status: 'running', ended: true }), 'ended');
  assert.equal(statusText({ status: 'odd' }), 'odd');
});
