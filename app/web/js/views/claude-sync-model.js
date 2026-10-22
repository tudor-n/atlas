// Pure helpers for Settings → "Claude Code sync" (no DOM).

// The owner's per-OS keys as typed: names split on commas, spaces or new lines, no blanks or repeats.
export function parseKeys(text) {
  const out = [];
  for (const k of String(text ?? '').split(/[\s,]+/)) {
    if (k && !out.includes(k)) out.push(k);
  }
  return out;
}

const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const when = (iso) => (iso ? String(iso).replace('T', ' ') : 'at an unknown time');

// The quiet note from GET /api/claude-sync: a list of { text, problem } lines.
export function syncNotes(view) {
  const lines = [];
  const note = (text) => lines.push({ text, problem: false });
  const problem = (text) => lines.push({ text, problem: true });
  if (view && view.available === false && view.reason) note(`The shared store isn't available now: ${view.reason}`);
  const s = view && view.summary;
  if (!s) {
    note('claude-sync hasn\'t run on this OS yet.');
    return lines;
  }
  if (s.available === false) {
    note(`Last run ${when(s.ran_at)}: nothing changed (${s.reason || 'the shared store was not available'}).`);
    return lines;
  }
  const parts = [];
  const mem = s.memory;
  if (mem && Array.isArray(mem.pairs)) {
    parts.push(mem.pairs.length ? `memory shared for ${mem.pairs.join(', ')}` : 'no memory to share yet');
  }
  const sk = s.skills;
  if (sk && Array.isArray(sk.moved)) {
    const n = sk.moved.length + sk.linked.length;
    if (n) parts.push(`${count(n, 'skill', 'skills')} moved or linked`);
  }
  const st = s.settings;
  if (st && Array.isArray(st.recorded)) {
    parts.push(`settings: ${count(st.recorded.length, 'change', 'changes')} sent, ${st.applied.length} received`);
  }
  note(`Last run ${when(s.ran_at)}${parts.length ? `: ${parts.join('; ')}` : ''}.`);
  for (const m of (mem && Array.isArray(mem.merged)) ? mem.merged : []) {
    if (m.kept_both && m.kept_both.length) {
      note(`Kept both versions of a note in ${m.into}: this OS's copy is ${m.kept_both.join(', ')}.`);
    }
  }
  const git = s.git || {};
  if (git.repo === false) note('The shared store has no git backup (run claude-sync init).');
  if (git.pushed === false) problem(`The backup push failed; it is retried at the next run. ${git.error || ''}`.trim());
  for (const list of Object.values(s.problems || {})) {
    for (const p of Array.isArray(list) ? list : []) problem(p);
  }
  return lines;
}
