// Pure helpers for Settings' sections and its rail (phase 4.5 design §4) and the setup wizard's
// progress bar. No DOM; tested with node (settings-model.test.mjs).

// "backup_debounce_s" -> "Backup debounce s" (the old field label; the rail and rows show it uppercase).
export const prefLabel = (key) => String(key).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

// -- the rail's status dots: 'g' (green), 'a' (amber) or null (no dot) --------------------------

// /api/state.backup: amber on an error, green once a push went through, else nothing.
export function backupDot(b) {
  if (!b || !b.remote_set) return null;
  if (b.last_error) return 'a';
  return b.last_ok ? 'g' : null;
}

// /api/integration: green when hooks and status line are in and point at this hub; amber when
// something is installed but stale (another port, an old interpreter or script); else nothing.
export function integrationDot(s) {
  if (!s || !(s.hooks_installed || s.statusline_installed)) return null;
  if (!s.port_matches || s.paths_current === false) return 'a';
  return s.hooks_installed && s.statusline_installed ? 'g' : 'a';
}

// /api/tablet-lan: green while the LAN site listens, amber when it is on but can't.
export function tabletDot(s) {
  if (!s || !s.tablet_lan) return null;
  return s.listening ? 'g' : 'a';
}

// /api/voice: green when it speaks, amber on a failed download or a failed line, else nothing.
export function voiceDot(v) {
  if (!v || !v.available) return null;
  const d = v.download || {};
  if (d.running) return null;
  if (!v.installed) return d.error ? 'a' : null;
  if (v.last_error) return 'a';
  return v.enabled && !v.paused_until ? 'g' : null;
}

// notes: syncNotes(view) from claude-sync-model. Amber on any problem, green after a clean run.
export function syncDot(view, notes) {
  if ((notes || []).some((n) => n.problem)) return 'a';
  return view && view.summary && view.summary.available !== false ? 'g' : null;
}

// -- backup status rows ------------------------------------------------------------------------
// [[label, value, kind]]: kind 'm' (mono), 'err' or ''. `when` formats an ISO stamp.
export function backupRows(b, when = (iso) => new Date(iso).toLocaleString()) {
  if (!b) return [];
  return [
    ['Remote', b.remote_set ? 'set' : 'not set', ''],
    ['Last successful push', b.last_ok ? when(b.last_ok) : 'never', 'm'],
    ['Waiting to push', b.pending ? 'yes' : 'no', ''],
    ['Last error', b.last_error || 'none', b.last_error ? 'err' : ''],
  ];
}

// -- hierarchy ---------------------------------------------------------------------------------
export function counts(n) {
  return `${n.files} ${n.files === 1 ? 'file' : 'files'} · ${n.items} ${n.items === 1 ? 'item' : 'items'}`;
}

// The /api/hierarchy tree as rows, depth first: [{name, kind, slug, depth, counts}]. The root itself
// is left out (its counts head the section); its children are depth 0.
export function hierarchyRows(tree) {
  const out = [];
  const walk = (n, depth) => {
    for (const c of n.children || []) {
      out.push({ name: c.name, kind: c.kind, slug: c.slug, depth: Math.min(depth, 3), counts: counts(c) });
      walk(c, depth + 1);
    }
  };
  if (tree) walk(tree, 0);
  return out;
}

// -- setup's progress bar ----------------------------------------------------------------------
// Step i against the current step: 'done', 'cur' or 'todo'.
export const stepState = (i, current) => (i < current ? 'done' : i === current ? 'cur' : 'todo');
