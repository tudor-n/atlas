import test from 'node:test';
import assert from 'node:assert/strict';
import { guessIntent, shownIntent, MAX_CHARS } from './intent-model.js';

const intent = (s) => guessIntent(s).intent;

test('a question mark at the end asks', () => {
  assert.deepEqual(guessIntent('the wrist servo torque?'), { intent: 'ask', reason: 'mark' });
  assert.equal(intent('Ana’s birthday?  '), 'ask');
});

test('a question word with an auxiliary asks, even without the mark', () => {
  for (const s of ['what did I say about the wrist servo', 'When is the dentist', "where's the multimeter",
    'how many bolts did I order', 'Who was at the meeting', 'what about the gearbox', 'why did the print fail']) {
    assert.deepEqual(guessIntent(s), { intent: 'ask', reason: 'wh' }, s);
  }
});

test('an auxiliary with a subject asks', () => {
  for (const s of ['did I pay the rent', 'have I called mum this week', 'is there a deadline for the report',
    'do we have spare servos', 'did the parcel arrive', 'was the meeting moved']) {
    assert.deepEqual(guessIntent(s), { intent: 'ask', reason: 'aux' }, s);
  }
});

test('a recall verb asks', () => {
  for (const s of ['find the notes on the arm calibration', 'show me what Ana said', 'tell me about the gearbox',
    'look up the servo order', 'search physics', 'list my open tasks', 'remind me what the plan was']) {
    assert.deepEqual(guessIntent(s), { intent: 'ask', reason: 'recall' }, s);
  }
});

test('tasks and chores are notes, even with a question mark', () => {
  for (const s of ['remind me to buy milk?', 'need to call the dentist tomorrow', 'todo fix the wrist servo',
    "don't forget the parcel", 'remember to pay the rent', 'note to self check the fuses']) {
    assert.deepEqual(guessIntent(s), { intent: 'note', reason: 'task' }, s);
  }
  for (const s of ['do the dishes', 'have the car serviced', 'call mum', 'buy servos', 'When I get home, call mom',
    'how to wire the relay board']) {
    assert.equal(intent(s), 'note', s);
  }
});

test('longer writing is a note whatever it holds', () => {
  assert.deepEqual(guessIntent('what did I say about the servo?\nalso buy milk'), { intent: 'note', reason: 'long' });
  assert.equal(intent('Should I call mom? Probably tomorrow. Also the servo is loud.'), 'note');
  assert.equal(intent('what ' + 'x'.repeat(MAX_CHARS) + '?'), 'note');
  assert.equal(intent('Was it Tuesday? Or Wednesday?'), 'ask');  // two sentences still count as short
});

test('empty is a note', () => {
  assert.deepEqual(guessIntent(''), { intent: 'note', reason: 'empty' });
  assert.deepEqual(guessIntent('   '), { intent: 'note', reason: 'empty' });
  assert.deepEqual(guessIntent(undefined), { intent: 'note', reason: 'empty' });
});

test("the owner's pick wins over the guess", () => {
  assert.equal(shownIntent('did I pay the rent', null), 'ask');
  assert.equal(shownIntent('did I pay the rent', 'note'), 'note');
  assert.equal(shownIntent('buy milk', 'ask'), 'ask');
  assert.equal(shownIntent('', 'ask'), 'ask');  // picked before typing
  assert.equal(shownIntent('', null), 'note');
  assert.equal(shownIntent('buy milk', 'bogus'), 'note');
});
