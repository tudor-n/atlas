// Pure navigation model (phase 4.5 design §3): hash parsing, the six top-bar tabs plus Settings, which
// tab a route lights up, Settings' sections and Browse's default. No DOM. The hash routes are an API
// (the widget, the shell and older links send them), so every existing route keeps working.

// "#/journal?entry=e-1" -> {name: 'journal', params: [], param: null, query: URLSearchParams(entry=e-1)}
// Same parsing as app.js had: an empty hash is Today, empty path segments are dropped, segments are
// URI-decoded. `param` is the first segment after the name (a project slug, a settings section).
export function parseHash(hash) {
  const [path, qs = ''] = String(hash || '').replace(/^#\/?/, '').split('?');
  const parts = path.split('/').filter(Boolean).map(decode);
  const params = parts.slice(1);
  return { name: parts[0] || 'today', params, param: params.length ? params[0] : null, query: new URLSearchParams(qs) };
}

// A malformed escape ("%E0") is kept as typed instead of throwing out of the router.
function decode(s) {
  try { return decodeURIComponent(s); } catch (_) { return s; }
}

export const TABS = [
  { id: 'today', label: 'TODAY', key: '1' },
  { id: 'ask', label: 'ASK', key: '2' },
  { id: 'journal', label: 'JOURNAL', key: '3' },
  { id: 'browse', label: 'BROWSE', key: '4' },
  { id: 'agents', label: 'AGENTS', key: '5' },
  { id: 'tablet', label: 'TABLET', key: '6' },
];
export const SETTINGS = { id: 'settings', label: 'SETTINGS', key: '0' };

// Alt+<key> -> the tab or Settings id, or null.
export function tabByKey(key) {
  if (key === SETTINGS.key) return SETTINGS.id;
  const t = TABS.find((x) => x.key === key);
  return t ? t.id : null;
}

const TAB_OF = {
  today: 'today', ask: 'ask', journal: 'journal', tablet: 'tablet',
  agents: 'agents', usage: 'agents',
  browse: 'browse', people: 'browse', deadlines: 'browse', project: 'browse',
  settings: 'settings',
};

// The tab a route lights up: 'settings' for Settings, null for setup and anything unknown.
export function tabFor(name) {
  return Object.hasOwn(TAB_OF, name) ? TAB_OF[name] : null;
}

export const SETTINGS_SECTIONS = [
  { id: 'map', label: 'BRAIN MAP' },
  { id: 'hierarchy', label: 'HIERARCHY' },
  { id: 'preferences', label: 'PREFERENCES' },
  { id: 'backup', label: 'BACKUP' },
  { id: 'claude', label: 'CLAUDE CODE' },
  { id: 'tablet', label: 'TABLET' },
  { id: 'voice', label: 'VOICE' },
  { id: 'sync', label: 'SYNC' },
  { id: 'about', label: 'ABOUT' },
];
export const SETTINGS_DEFAULT = 'map';
const SECTION_IDS = new Set(SETTINGS_SECTIONS.map((s) => s.id));

// "#/settings/<section>" -> the section to render. The old in-page anchors were `tablet` and `voice`,
// which are section ids too, so they resolve to themselves. Anything else is the brain map.
export function settingsSection(param) {
  const p = String(param || '').toLowerCase();
  return SECTION_IDS.has(p) ? p : SETTINGS_DEFAULT;
}

// Browse: the rail's fixed entries, and what `#/browse` opens (the last-used one, else deadlines).
export const BROWSE_DEFAULT = 'deadlines';
const BROWSE_FIXED = new Set(['deadlines', 'people']);
const SLUG = /^[a-z0-9-]+$/;  // the server's view pattern (routes._VIEW_RX)

// lastUsed is a route path like 'people' or 'project/robotarm' (or junk from storage): a valid one
// comes back as is, anything else is BROWSE_DEFAULT.
export function browseTarget(lastUsed) {
  const v = typeof lastUsed === 'string' ? lastUsed : '';
  if (BROWSE_FIXED.has(v)) return v;
  const m = /^project\/(.+)$/.exec(v);
  if (m && SLUG.test(m[1])) return v;
  return BROWSE_DEFAULT;
}
