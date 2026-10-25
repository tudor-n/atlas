// Pure intent model for the one input (voice-input decisions VI2): is what the owner typed a note to
// file or a question to ask? Instant local rules, no model call; the screen shows the guess live as a
// NOTE | ASK switch, so a wrong one costs a click. English only, like recall's stopwords. No DOM.
//
// A question is short (one line, at most MAX_CHARS, at most two sentences) and ends with "?", or opens
// like one: a question word with an auxiliary ("what did", "when's"), an auxiliary with a subject
// ("did I", "is there"), or a recall verb ("find", "show me", "remind me what"). Everything else is a
// note: a task ("remind me to…" wins even with a "?"), and any longer writing, whatever it contains.

export const MAX_CHARS = 160;

const TASK = /^(remind me to|reminder to|need to|i need to|todo|to do|to-do|don't forget|dont forget|remember to|note to self)\b/;
const RECALL = /^(find|show me|tell me|look up|search|list|remind me (what|when|where|who|how|if|whether|why|which))\b/;
const WH = /^(what|when|where|who|whom|whose|which|why|how)('s|'re|'d|'ve|'ll|\s+(is|are|was|were|did|do|does|have|has|had|can|could|should|would|will|am|much|many|long|often|far|old|about)\b)/;
// "did the parcel come" asks, but "do the dishes" and "have the car serviced" are chores: a noun
// subject only counts after the auxiliaries an imperative can't start with.
const AUX_SUBJECT = /^(did|do|does|have|has|had|is|are|was|were|am|can|could|should|would|will|shall)\s+(i|we|you|he|she|they|it|there)\b/;
const AUX_NOUN = /^(did|does|is|are|was|were|has)\s+(the|my|a|an|our|his|her|their)\b/;

export function guessIntent(text) {
  const raw = String(text || '').trim();
  if (!raw) return { intent: 'note', reason: 'empty' };
  if (/[\r\n]/.test(raw) || raw.length > MAX_CHARS || sentences(raw) > 2) return { intent: 'note', reason: 'long' };
  const s = raw.toLowerCase().replace(/[‘’]/g, "'").replace(/\s+/g, ' ');
  if (TASK.test(s)) return { intent: 'note', reason: 'task' };
  if (/\?\s*$/.test(s)) return { intent: 'ask', reason: 'mark' };
  if (RECALL.test(s)) return { intent: 'ask', reason: 'recall' };
  if (WH.test(s)) return { intent: 'ask', reason: 'wh' };
  if (AUX_SUBJECT.test(s) || AUX_NOUN.test(s)) return { intent: 'ask', reason: 'aux' };
  return { intent: 'note', reason: 'default' };
}

function sentences(s) {
  return s.split(/[.!?]+(?:\s+|$)/).filter((x) => x.trim()).length;
}

// The switch's state: the owner's pick wins (the input drops it once the field is emptied), else the
// live guess. A pick before anything is typed holds too.
export function shownIntent(text, pinned) {
  if (pinned === 'note' || pinned === 'ask') return pinned;
  return guessIntent(text).intent;
}
