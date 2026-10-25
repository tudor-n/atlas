// Pure intent model for the one input (voice-input decisions VI2): is what the owner typed a note to
// file or a question to ask? Instant local rules, no model call; the screen shows the guess live as a
// NOTE | ASK switch, so a wrong one costs a click. English only, like recall's stopwords. No DOM.
//
// A question is short (one line, at most MAX_CHARS, at most two sentences), and its last sentence asks:
// it ends with "?", or opens like a question. That is a question word with an auxiliary ("what did",
// "when's"), an auxiliary with a subject ("did I", "is there"), or a recall verb aimed at what was
// written ("find my notes on…", "show me what…", "tell me about…", "list my…", "remind me what…").
// Everything else is a note: any sentence that is a task ("remind me to…", "need to…") makes it one even
// with a "?", and so does a to-do that only starts like a search ("find a better bearing", "search for
// cheaper servos", "list of parts"), and any longer writing, whatever it contains. A note is the safe
// side: it is in the log either way.

export const MAX_CHARS = 160;

const TASK = /^(remind me to|reminder to|need to|i need to|todo|to do|to-do|don't forget|dont forget|remember to|note to self)\b/;
const WORDS = 'what|when|where|who|how|if|whether|why|which';
const RECALL = new RegExp('^(?:'
  + '(?:find|search(?: for)?|look up|pull up)\\s+(?:(?:me|all|every|the|my)\\s+)*(?:notes?|entries|entry|everything|anything|what|where|when|who|which)\\b'
  + `|tell me (?:about|${WORDS})\\b`
  + '|show me (?:what|where|when|who|how|which|my|the|all|everything)\\b'
  + '|list (?:my|all|every|the)\\b'
  + `|remind me (?:${WORDS})\\b)`);
const WH = /^(what|when|where|who|whom|whose|which|why|how)('s|'re|'d|'ve|'ll|\s+(is|are|was|were|did|do|does|have|has|had|can|could|should|would|will|am|much|many|long|often|far|old|about)\b)/;
// "did the parcel come" asks, but "do the dishes" and "have the car serviced" are chores: a noun
// subject only counts after the auxiliaries an imperative can't start with.
const AUX_SUBJECT = /^(did|do|does|have|has|had|is|are|was|were|am|can|could|should|would|will|shall)\s+(i|we|you|he|she|they|it|there)\b/;
const AUX_NOUN = /^(did|does|is|are|was|were|has)\s+(the|my|a|an|our|his|her|their)\b/;

export function guessIntent(text) {
  const raw = String(text || '').trim();
  if (!raw) return { intent: 'note', reason: 'empty' };
  const parts = sentences(raw.toLowerCase().replace(/[\u2018\u2019]/g, "'").replace(/\s+/g, ' '));
  if (/[\r\n]/.test(raw) || raw.length > MAX_CHARS || parts.length > 2) return { intent: 'note', reason: 'long' };
  if (parts.some((p) => TASK.test(p))) return { intent: 'note', reason: 'task' };
  const last = parts[parts.length - 1];
  if (/\?\s*$/.test(last)) return { intent: 'ask', reason: 'mark' };
  if (RECALL.test(last)) return { intent: 'ask', reason: 'recall' };
  if (WH.test(last)) return { intent: 'ask', reason: 'wh' };
  if (AUX_SUBJECT.test(last) || AUX_NOUN.test(last)) return { intent: 'ask', reason: 'aux' };
  return { intent: 'note', reason: 'default' };
}

// "is it the mg996r? need two more." -> ['is it the mg996r?', 'need two more.']
function sentences(s) {
  return (s.match(/[^.!?]+[.!?]*/g) || []).map((x) => x.trim()).filter((x) => x && /[^.!?]/.test(x));
}

// The switch's state: the owner's pick wins (the input drops it once the field is emptied), else the
// live guess. A pick before anything is typed holds too.
export function shownIntent(text, pinned) {
  if (pinned === 'note' || pinned === 'ask') return pinned;
  return guessIntent(text).intent;
}
