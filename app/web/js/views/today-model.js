// Today's pure helpers (phase 4.5 design §4): the review queue, its selection and keyboard moves,
// item meta lines, the snooze presets. No DOM, so they are node-tested (today-model.test.mjs).
// Every string here is data for el()/textContent; nothing becomes markup.

const pad = (n) => String(n).padStart(2, '0');

// The local date of `d` as "YYYY-MM-DD".
export const localDay = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

// Local 09:00 of the day `days` from now, as a local ISO datetime (no timezone).
export function snoozeUntil(days, now = new Date()) {
  const d = new Date(now.getTime());
  d.setDate(d.getDate() + days);
  return `${localDay(d)}T09:00`;
}

// The inline segmented control's cells: value = days.
export const SNOOZES = [{ value: 1, label: '1 D' }, { value: 3, label: '3 D' }, { value: 7, label: '1 W' }];
export const SNOOZE_NAMES = { 1: '1 day', 3: '3 days', 7: '1 week' };

// "e-20261020-140322-ab12" -> "10-20 14:03" (the year only when it isn't `today`'s), or ''.
export function entryStamp(entryId, today = localDay()) {
  const m = /^e-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(String(entryId || ''));
  if (!m) return '';
  const day = m[1] === today.slice(0, 4) ? `${m[2]}-${m[3]}` : `${m[1]}-${m[2]}-${m[3]}`;
  return `${day} ${m[4]}:${m[5]}`;
}

// A local ISO date or datetime, short: "10-21", "10-28 09:00", or with its year when not this one.
export function shortWhen(iso, today = localDay()) {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(String(iso || ''));
  if (!m) return String(iso || '');
  const day = m[1] === today.slice(0, 4) ? `${m[2]}-${m[3]}` : `${m[1]}-${m[2]}-${m[3]}`;
  return m[4] ? `${day} ${m[4]}:${m[5]}` : day;
}

// The first words of a text, cut at a word boundary: a synopsis stand-in for an entry not filed yet.
export function firstWords(text, max = 90) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  const sp = cut.lastIndexOf(' ');
  return `${(sp > max * 0.6 ? cut.slice(0, sp) : cut).replace(/[\s,.;:–-]+$/, '')}…`;
}

// The TO REVIEW queue: one row per entry with proposals (/api/proposals), then each failed filing
// (/api/filings?status=failed) not already listed. {id, failed, count, synopsis, text, error}.
export function reviewQueue(groups, failedRows) {
  const out = [];
  const seen = new Set();
  for (const g of groups || []) {
    seen.add(g.entry_id);
    out.push({ id: g.entry_id, failed: false, count: (g.proposals || []).length,
      synopsis: g.synopsis || firstWords(g.text) || g.entry_id, text: g.text || '', error: '' });
  }
  for (const f of failedRows || []) {
    if (seen.has(f.entry_id)) continue;
    seen.add(f.entry_id);
    out.push({ id: f.entry_id, failed: true, count: 0, synopsis: firstWords(f.text) || f.entry_id,
      text: f.text || '', error: f.error || '' });
  }
  return out;
}

// Which entry stays selected after the queue changes: the same one while it is still there, else
// the one now at its old place (the next one, after a confirm empties it), else the last; null if empty.
export function pickSelection(queue, prevId, prevIndex = 0) {
  if (!queue.length) return null;
  if (prevId && queue.some((q) => q.id === prevId)) return prevId;
  const i = Math.max(0, Math.min(Number(prevIndex) || 0, queue.length - 1));
  return queue[i].id;
}

// ↑/↓ (and Home/End) inside the queue: the id to select next, or null when the key isn't a move.
export function moveSelection(queue, currentId, key) {
  if (!queue.length) return null;
  const i = Math.max(0, queue.findIndex((q) => q.id === currentId));
  const to = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: queue.length - 1 }[key];
  if (to === undefined) return null;
  return queue[Math.max(0, Math.min(to, queue.length - 1))].id;
}

// slug -> display name, from /api/map (projects, courses, areas, people). A missing map gives {}.
export function mapNames(map) {
  const out = {};
  for (const key of ['areas', 'projects', 'courses', 'people']) {
    for (const x of (map && map[key]) || []) if (x && x.slug) out[x.slug] = x.name || x.slug;
  }
  return out;
}

// The slugs a NEXT TIME header may link to (#/project/<slug>): projects and courses. null = unknown
// (no map): link every header, as before.
export function projectSlugs(map) {
  if (!map) return null;
  return new Set([...(map.projects || []), ...(map.courses || [])].map((x) => x.slug));
}

// An item's meta line as parts [{text, warn}]: where (project, else area; and the person), the due
// date (amber once its day has passed), the reminder when there is no due date, and how long a
// waiting item has waited. `place: false` leaves out the project or area (a NEXT TIME row sits under
// its project's header already).
export function itemMeta(it, names = {}, today = localDay(), { place = true } = {}) {
  const name = (slug) => names[slug] || slug;
  const parts = [];
  const where = [!place ? '' : it.project ? name(it.project) : it.area ? name(it.area) : '', it.person ? name(it.person) : '']
    .filter(Boolean).join(' / ');
  if (where) parts.push({ text: where, warn: false });
  const late = (iso) => String(iso).slice(0, 10) < today;
  if (it.due) parts.push({ text: `due ${shortWhen(it.due, today)}`, warn: late(it.due) });
  else if (it.remind_at) parts.push({ text: `remind ${shortWhen(it.remind_at, today)}`, warn: late(it.remind_at) });
  if (it.kind === 'waiting' && it.created) parts.push({ text: `since ${shortWhen(String(it.created).slice(0, 10), today)}`, warn: false });
  return parts;
}
