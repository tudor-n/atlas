// Pure helpers for Browse (phase 4.5 design §4): the rail's entries, which page a route opens, where a
// project's or course's notes live, and the mono date column. No DOM; tested with node.
import { browseTarget } from '../nav-model.js';

// The rail's PROJECTS list: the map's projects, then its courses, as route paths.
export function railPlaces(map) {
  const m = map && typeof map === 'object' ? map : {};
  const list = (xs, kind) => (Array.isArray(xs) ? xs : [])
    .filter((x) => x && typeof x.slug === 'string' && x.slug)
    .map((x) => ({ path: `project/${x.slug}`, slug: x.slug, name: String(x.name || x.slug), kind }));
  return [...list(m.projects, 'project'), ...list(m.courses, 'course')];
}

// A project page's place in the brain: a course's notes live under uni/<slug>/, a project's (or a slug
// the map doesn't know) under projects/<slug>/.
export function findPlace(map, slug) {
  const hit = railPlaces(map).find((p) => p.slug === slug);
  const kind = hit ? hit.kind : 'project';
  return { slug, name: hit ? hit.name : slug, kind, known: !!hit,
    prefix: kind === 'course' ? `uni/${slug}/` : `projects/${slug}/` };
}

// The page a browse route shows, as a route path: `#/people` gives ['people'], `#/project/x` gives
// ['project', 'x'], and `#/browse` (no params) the last-used one (nav-model.browseTarget).
export function browsePath(params, lastUsed) {
  const p = Array.isArray(params) ? params : [];
  if (p[0] === 'project' && p[1]) return `project/${p[1]}`;
  if (p[0] === 'people' || p[0] === 'deadlines') return p[0];
  return browseTarget(lastUsed);
}

// Local "YYYY-MM-DD".
export function localDay(now) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

// The date column of an item row: its due date or, failing that, its reminder, in mono short form
// ("11-14", "10-25 09:00", the year only when it isn't this one). A due date before today is overdue.
export function dateCell(item, today) {
  const iso = item && (item.due || item.remind_at);
  if (!iso) return { text: '–', overdue: false, remind: false };
  const s = String(iso);
  const day = s.slice(0, 10);
  const time = s.length > 10 ? s.slice(11, 16) : '';
  const short = day.slice(0, 4) === String(today).slice(0, 4) ? day.slice(5) : day;
  return { text: time ? `${short} ${time}` : short, overdue: !!item.due && day < today, remind: !item.due };
}
