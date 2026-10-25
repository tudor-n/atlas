// Pure helpers for Settings → Voice and the widget's speaker (phase 4 design §7). Tested in node.

export const EVENT_LABELS = [
  ['agent_waiting', 'An agent needs you'],
  ['agent_finished', 'An agent finished a long run'],
  ['reminder', 'Reminders'],
  ['readback', 'Read back what I type on the tablet'],
  ['reply', 'Reply when I write something down or ask'],
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
