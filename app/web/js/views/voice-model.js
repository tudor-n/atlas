// Pure helpers for Settings → Voice, the widget's speaker and ATLAS's week on the ATLAS screen (phase 4
// design §7, chatter spec §4–§8). Tested in node.

export const EVENT_LABELS = [
  ['agent_waiting', 'An agent needs you'],
  ['agent_finished', 'An agent finished a long run'],
  ['reminder', 'Reminders'],
  ['readback', 'Read back what I type on the tablet'],
  ['reply', 'Reply when I write something down or ask'],
  ['briefing', 'Briefings when I come back, morning and evening'],
  ['self', 'Remarks about myself'],
  ['nudge', 'Nudges about things left waiting'],
  ['react', 'React to what I just did'],
  ['due_today', 'Keep reminding me about things due today'],
  ['tease', "Tease me about ATLAS's own to-dos (High and up)"],
];

export const mb = (bytes) => `${Math.max(1, Math.round(bytes / 1e6))} MB`;

// {bm_george: 0.7, bm_fable: 0.3} -> [{id, pct}] in the order given, as whole percentages.
export function blendRows(blend) {
  const entries = Object.entries(blend || {});
  const total = entries.reduce((s, [, w]) => s + w, 0) || 1;
  return entries.map(([id, w]) => ({ id, pct: Math.round((w / total) * 100) }));
}

// The editor's rows back to a blend: 1-3 distinct voices, each above 0 %. Weights are fractions
// that sum to 1 (the hub normalises anyway). Returns {blend} or {error}.
export function readBlend(rows) {
  const used = rows.filter((r) => r.id);
  if (!used.length) return { error: 'Pick at least one voice.' };
  if (used.length > 3) return { error: 'Blend at most three voices.' };
  const ids = new Set(used.map((r) => r.id));
  if (ids.size !== used.length) return { error: 'Each voice can only appear once in the blend.' };
  const pcts = used.map((r) => Number(r.pct));
  if (pcts.some((p) => !Number.isFinite(p) || p <= 0)) return { error: 'Every voice needs a share above 0 %.' };
  const total = pcts.reduce((s, p) => s + p, 0);
  const blend = {};
  used.forEach((r, i) => { blend[r.id] = Math.round((pcts[i] / total) * 1000) / 1000; });
  return { blend };
}

// "bm_george" -> "George (British man)"
export function voiceName(id) {
  const m = /^([ab])([fm])_(.+)$/.exec(id || '');
  if (!m) return id;
  const who = `${m[1] === 'b' ? 'British' : 'American'} ${m[2] === 'f' ? 'woman' : 'man'}`;
  return `${m[3][0].toUpperCase()}${m[3].slice(1)} (${who})`;
}

const hhmm = (iso) => (iso ? iso.slice(11, 16) : '');

// The one line under the Voice heading: what state it's in, plainly.
export function statusLine(v) {
  if (!v || !v.available) return { text: (v && v.reason) || 'The voice is unavailable.', kind: 'meta' };
  const d = v.download || {};
  if (d.running) return { text: `Downloading the voice… ${mb(d.done)} of ${mb(d.total)}`, kind: 'meta' };
  if (!v.installed) {
    const failed = d.error ? `The last download failed: ${d.error}. ` : '';
    return { text: `${failed}Not installed yet: the voice needs a one-time download of about ${mb(v.missing_bytes)}.`, kind: d.error ? 'error' : 'meta' };
  }
  if (!v.enabled) return { text: 'The voice is off.', kind: 'meta' };
  if (v.paused_until) return { text: `Paused until ${hhmm(v.paused_until)}.`, kind: 'meta' };
  if (v.quiet_now) return { text: 'Quiet hours now: nothing is spoken until they end.', kind: 'meta' };
  if (v.last_error) return { text: `On, but the last line failed: ${v.last_error}`, kind: 'error' };
  return { text: 'On.', kind: 'status' };
}

// The widget's speaker: hidden until the voice is installed; a click switches it on or off, or
// ends a pause. {show, icon, title, action}: action is 'off', 'on' or 'resume'.
export function widgetVoice(v) {
  if (!v || !v.available || !v.installed) return { show: false };
  if (!v.enabled) return { show: true, icon: '🔇', title: 'Voice off · click to switch it on', action: 'on', muted: true };
  if (v.paused_until) {
    return { show: true, icon: '🔇', title: `Voice paused until ${hhmm(v.paused_until)} · click to resume`, action: 'resume', muted: true };
  }
  const quiet = v.quiet_now ? ' (quiet hours now)' : '';
  return { show: true, icon: '🔊', title: `Voice on${quiet} · click to switch it off`, action: 'off', muted: !!v.quiet_now };
}

// Settings → Voice → Workout words: the field's text as the setting's list. Commas separate; spaces
// around a word don't count; duplicates (any case) fold into one. {list} or {error}.
export function workoutWords(text) {
  const seen = new Set();
  const list = [];
  for (const raw of String(text || '').split(',')) {
    const w = raw.trim().replace(/\s+/g, ' ');
    if (!w) continue;
    if (w.length < 2 || w.length > 40) return { error: `Workout words: "${w}" must be 2 to 40 characters.` };
    if (seen.has(w.toLowerCase())) continue;
    seen.add(w.toLowerCase());
    list.push(w);
  }
  if (list.length > 100) return { error: 'Workout words: at most 100.' };
  return { list };
}

// -- chattiness (chatter spec §4) ---------------------------------------------------------------

// The levels as the segmented control shows them; `debug` is the owner's Very high.
export const LEVELS = [
  { value: 'mute', label: 'MUTE' }, { value: 'low', label: 'LOW' }, { value: 'medium', label: 'MEDIUM' },
  { value: 'high', label: 'HIGH' }, { value: 'debug', label: 'VERY HIGH' },
];
const LEVEL_NAME = Object.fromEntries(LEVELS.map((l) => [l.value, l.label]));
export const levelName = (v) => LEVEL_NAME[v] || LEVEL_NAME.medium;

// The level table (spec §4): what each level does unprompted. cap null = no cap.
const TABLE = {
  mute: { cap: 0, due: '', react: '', mood: false, musing: '', quips: '' },
  low: { cap: 2, due: 'every 3 h', react: 'mistakes', mood: false, musing: '', quips: '1 in 3' },
  medium: { cap: 4, due: 'every 2 h', react: 'all', mood: true, musing: '', quips: 'cooldown' },
  high: { cap: 8, due: 'every hour', react: 'all', mood: true, musing: 'on', quips: 'cooldown' },
  debug: { cap: null, due: 'every 2 min', react: 'all', mood: true, musing: 'every 3 min', quips: 'every' },
};
// The levels whose cap the owner's lines-an-hour overrides (the hub ignores it at Mute and Very high).
export const capOverridable = (lvl) => lvl !== 'mute' && lvl !== 'debug';
export const levelCap = (lvl) => (TABLE[lvl] || TABLE.medium).cap;

// The one preview line under CHATTINESS. perHour: the owner's lines an hour (a number), or null.
export function levelPreview(lvl, perHour = null) {
  const t = TABLE[lvl] || TABLE.medium;
  const cap = Number.isInteger(perHour) && capOverridable(lvl) ? perHour : t.cap;
  const mine = cap !== t.cap ? ' (yours)' : '';
  const lines = cap === null ? 'no cap on unprompted lines' : cap === 0 ? 'no unprompted lines'
    : `${cap} unprompted ${cap === 1 ? 'line' : 'lines'} an hour${mine}`;
  const parts = [
    lines,
    t.due ? `due today again ${t.due}` : 'no due-today repeats',
    { '': 'no reactions', mistakes: 'reacts to mistakes only', all: 'reacts to everything' }[t.react],
    t.mood ? 'callbacks and mood' : 'no callbacks or mood',
    !t.musing ? 'no musings' : t.musing === 'on' ? 'musings' : `musings ${t.musing}`,
    { '': 'no quips on new events', '1 in 3': 'a quip on 1 in 3 new events', cooldown: 'quips with a cooldown',
      every: 'a quip on every line' }[t.quips],
  ];
  const line = `${parts.join(', ')}.`;
  const head = line[0].toUpperCase() + line.slice(1);
  if (lvl === 'mute') return `${head} The events above keep their own switches.`;
  if (lvl === 'debug') return `${head} It ignores quiet hours and drops back by itself after 2 h or at a restart.`;
  return head;
}

// The chatter fields of the form, as settings: {patch} or {error}. The hub checks them again.
export function readChatter({ perHour, lastCall, project, maxPct }) {
  const ph = String(perHour ?? '').trim();
  let cap = null;
  if (ph !== '') {
    cap = Number(ph);
    if (!Number.isInteger(cap) || cap < 0 || cap > 60) return { error: "Lines an hour: a whole number from 0 to 60, or empty for the level's." };
  }
  const time = String(lastCall || '').trim().slice(0, 5);  // a time field may add :ss
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) return { error: 'Last call: a time like 18:00.' };
  const slug = String(project || '').trim();
  if (slug && (slug.length > 80 || !/^[\p{L}\p{N}_][\p{L}\p{N}_.-]*$/u.test(slug))) {
    return { error: "ATLAS's project: a project's slug from the map, or empty to find it." };
  }
  const pct = Number(String(maxPct ?? '').trim() || NaN);
  if (!Number.isInteger(pct) || pct < 1 || pct > 100) return { error: 'Skip above: a whole number from 1 to 100 %.' };
  return { patch: { voice_chatter_per_hour: cap, voice_last_call: time, voice_atlas_project: slug, voice_sub_max_pct: pct } };
}

// Very high's Try it now (POST /api/chatter/try {what}): the subscription's patterns only while it's on.
const TRY = [['briefing', 'BRIEFING'], ['morning', 'MORNING'], ['evening', 'EVENING'], ['due_today', 'DUE TODAY'],
  ['last_call', 'LAST CALL'], ['nudge', 'NUDGE'], ['tease', 'TEASE'], ['musing', 'MUSING'], ['self', 'SELF'],
  ['react', 'REACT']];
const TRY_SUB = [['sub_quips', 'A · QUIPS'], ['sub_briefings', 'B · BRIEFING'], ['sub_review', 'C · WEEK']];
export const tryList = (subOn) => [...TRY, ...(subOn ? TRY_SUB : [])].map(([what, label]) => ({ what, label }));

// What a Try press did, for the status line: {said: {text,…} | null} or {started}.
export function tryOutcome(res) {
  if (res && 'started' in res) return res.started ? 'Started. Its line shows below when it is done.' : 'Already running, or nothing to do.';
  if (res && res.said) return res.said.spoken ? 'Said.' : 'Captioned, not spoken.';
  return 'Nothing to say for that right now.';
}

const clock = (iso) => (iso ? String(iso).slice(11, 19) : '');

// One RECENT LINES row: the text, then when · event · origin · spoken or captioned (and why).
export function lineRow(line) {
  const l = line || {};
  const how = l.spoken ? 'spoken' : 'captioned';
  const why = l.why ? `${how} (${l.why})` : how;
  return { text: String(l.text || ''), meta: [clock(l.at), l.event, l.origin, why].filter(Boolean).join(' · ') };
}

// The presence line at the top of TRY IT NOW: {text, kind} (kind: a dot, 'g' while present).
export function presenceLine(p) {
  if (!p || !p.state) return { text: 'Presence unknown.', kind: 'ring' };
  const word = { present: 'Present', idle: 'Idle', away: 'Away' }[p.state] || p.state;
  const locked = p.locked ? ', screen locked' : '';
  const seen = p.last_seen ? ` Last seen ${clock(p.last_seen)}${p.source ? ` (${p.source})` : ''}.` : ' Not seen since the start.';
  return { text: `${word}${locked}.${seen}`, kind: p.state === 'present' ? 'g' : p.state === 'away' ? 'ring' : '' };
}

// Very high drops back by itself after two hours: when, and to what.
export function debugLine(debug) {
  if (!debug || !debug.since) return '';
  const since = new Date(debug.since);
  if (Number.isNaN(since.getTime())) return '';
  const back = new Date(since.getTime() + 2 * 3600e3);
  const hm = (d) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const names = { mute: 'Mute', low: 'Low', medium: 'Medium', high: 'High' };
  const to = names[debug.before] || 'the level before';
  return `Very high since ${hm(since)}. Back to ${to} at ${hm(back)}, or at a restart.`;
}

// The subscription's runs, shown only when the hub reports them (any shape: runs_week and last).
export function subLine(sub) {
  if (!sub || typeof sub !== 'object') return '';
  const bits = [];
  if (Number.isFinite(sub.runs_week)) bits.push(`${sub.runs_week} ${sub.runs_week === 1 ? 'run' : 'runs'} this week`);
  const last = sub.last;
  if (typeof last === 'string' && last) bits.push(`last ${last}`);
  else if (last && typeof last === 'object') {
    const vals = Object.values(last).filter((x) => ['string', 'number', 'boolean'].includes(typeof x)).map(String);
    if (vals.length) bits.push(`last ${vals.join(' ')}`);
  }
  return bits.length ? `Subscription: ${bits.join(', ')}.` : '';
}

// The widget's DEBUG CHATTER tag: while the level is Very high, when /api/voice says so.
export const widgetDebug = (v) => !!(v && v.chattiness === 'debug');

// -- ATLAS's week (chatter spec §6 C) -------------------------------------------------------------

// GET /api/chatter/week's week, cleaned for the screen, or null when there is nothing to show.
// Dismissed suggestions drop out; the rest keep their status (open or confirmed).
export function weekView(week) {
  if (!week || typeof week !== 'object') return null;
  const str = (x) => (typeof x === 'string' ? x.trim() : '');
  const paragraphs = (Array.isArray(week.paragraphs) ? week.paragraphs : []).map(str).filter(Boolean);
  const title = str(week.title);
  if (!title && !paragraphs.length) return null;
  const suggestions = (Array.isArray(week.suggestions) ? week.suggestions : [])
    .filter((x) => x && (typeof x.id === 'string' || typeof x.id === 'number') && str(x.text) && x.status !== 'dismissed')
    .map((x) => ({ id: String(x.id), text: str(x.text), status: x.status === 'confirmed' ? 'confirmed' : 'open' }));
  return { title, written: str(week.written_at), paragraphs, suggestions };
}
