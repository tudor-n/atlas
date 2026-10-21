// Settings: brain-map editor, hierarchy tree, preferences and backup status.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { createMapEditors, normalizeMap, hasErrors, savedNodes } from '../components/map-editor.js';

const MODELS = ['haiku', 'sonnet', 'opus'];
const WIDGET_TILES = ['today', 'usage', 'agents', 'people', 'deadlines', 'review', 'projects', 'arm'];
const BACKUP_KEYS = ['backup_remote', 'backup_debounce_s'];
const HINTS = {
  port: 'Takes effect after BrainHub restarts. After changing it, install the Claude Code integration again.',
  claude_path: 'Command or full path of the claude CLI. Takes effect after BrainHub restarts.',
  quiet_hours: 'Stored now; used when reminders arrive.',
  backup_debounce_s: 'Seconds to wait after a change before pushing.',
  nudge_days: 'Days before an open item gets a nudge.',
  default_lead_days: 'Default days of notice for deadlines.',
  recall_top_n: 'How many notes recall looks at.',
  usage_warn_pct: 'Usage at or above this % shows as a warning.',
  agents_end_linger_s: 'Seconds a closed session stays listed.',
  agents_stale_h: 'Hours without any event before a session is dropped.',
  arm_url: 'Robot arm server address (opened from the widget).',
  widget_tiles: 'Tiles shown in the expanded widget.',
};
const label = (key) => key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

// -- Hierarchy tree ---------------------------------------------------------------------------
function counts(n) {
  return `${n.files} ${n.files === 1 ? 'file' : 'files'} · ${n.items} ${n.items === 1 ? 'item' : 'items'}`;
}

function treeNode(n, open) {
  const text = el('span', {}, n.name, ' ', el('span', { class: 'meta' }, counts(n)));
  if (n.children.length) {
    return el('li', {}, el('details', { open: open || null },
      el('summary', {}, text),
      el('ul', { class: 'tree' }, n.children.map((c) => treeNode(c, false)))));
  }
  const body = n.kind === 'project'
    ? el('a', { href: `#/project/${encodeURIComponent(n.slug)}` }, n.name)
    : el('span', {}, n.name);
  return el('li', { class: 'leaf' }, body, ' ', el('span', { class: 'meta' }, counts(n)));
}

// -- Preferences fields -----------------------------------------------------------------------
// Builds one control per settings key. read() returns the current value, or throws Error(message).
function buildField(key, value) {
  const hint = HINTS[key] ? el('div', { class: 'meta' }, HINTS[key]) : null;
  const wrap = (control) => el('div', { class: 'pref' }, el('label', { class: 'field' }, label(key), control), hint);

  if (key === 'quiet_hours') {
    const a = el('input', { type: 'time', value: value[0] });
    const b = el('input', { type: 'time', value: value[1] });
    return {
      node: el('div', { class: 'pref' }, el('div', { class: 'edit-grid' },
        el('label', { class: 'field' }, 'Quiet hours from', a), el('label', { class: 'field' }, 'Until', b)), hint),
      read() {
        if (!a.value || !b.value) throw new Error('Pick both quiet-hours times.');
        return [a.value, b.value];
      },
    };
  }
  if (key === 'widget_tiles' && Array.isArray(value)) {
    const boxes = WIDGET_TILES.map((t) => ({ t, box: el('input', { type: 'checkbox', checked: value.includes(t) }) }));
    return {
      node: el('div', { class: 'pref' }, el('fieldset', {}, el('legend', {}, label(key)),
        boxes.map(({ t, box }) => el('label', { class: 'field' }, box, ' ', label(t)))), hint),
      read() {
        const on = new Set(boxes.filter((b) => b.box.checked).map((b) => b.t));
        if (!on.size) throw new Error('Pick at least one widget tile.');
        return [...value.filter((t) => on.has(t)), ...WIDGET_TILES.filter((t) => on.has(t) && !value.includes(t))];
      },
    };
  }
  if (typeof value === 'number') {
    const input = el('input', { type: 'number', value: String(value), step: 'any' });
    return {
      node: wrap(input),
      read() {
        const n = Number(input.value);
        if (input.value.trim() === '' || !Number.isFinite(n)) throw new Error(`${label(key)} must be a number.`);
        return n;
      },
    };
  }
  if (key.endsWith('_model')) {
    const custom = el('input', { type: 'text', value: MODELS.includes(value) ? '' : String(value ?? ''),
      placeholder: 'model name', 'aria-label': `${label(key)} (custom name)`, spellcheck: 'false' });
    const select = el('select', { onchange: sync },
      [...MODELS, 'custom'].map((m) => el('option', { value: m, selected: (MODELS.includes(value) ? value : 'custom') === m }, m)));
    function sync() { custom.hidden = select.value !== 'custom'; }
    sync();
    return {
      node: el('div', { class: 'pref' }, el('label', { class: 'field' }, label(key), select), custom, hint),
      read() {
        if (select.value !== 'custom') return select.value;
        if (!custom.value.trim()) throw new Error(`${label(key)}: type a model name or pick one from the list.`);
        return custom.value.trim();
      },
    };
  }
  // strings, and the nullable backup_remote (empty means null)
  const input = el('input', { type: 'text', value: value ?? '', spellcheck: 'false',
    placeholder: key === 'backup_remote' ? 'https://github.com/you/brainhub-backup.git' : null });
  return {
    node: wrap(input),
    read() {
      const v = input.value.trim();
      if (key === 'backup_remote') return v || null;
      if (key === 'claude_path' && !v) throw new Error('Claude path must not be empty.');
      return v;
    },
  };
}

export async function render(container) {
  let dead = false;
  const subs = [];
  const cleanup = () => { dead = true; for (const off of subs) off(); };

  container.appendChild(el('h1', {}, 'Settings'));
  const hintLine = el('p', { class: 'muted' });
  container.appendChild(hintLine);

  // ---- Brain map
  const mapMsg = el('div', { role: 'alert' });
  const mapBox = el('div', {});
  container.appendChild(el('h2', {}, 'Brain map'));
  container.appendChild(mapBox);

  // ---- Hierarchy
  const treeBox = el('div', {});
  container.appendChild(el('h2', {}, 'Hierarchy'));
  container.appendChild(treeBox);

  async function loadTree() {
    try {
      const h = await api.get('/api/hierarchy');
      if (dead) return;
      clear(treeBox).appendChild(el('ul', { class: 'tree root' }, treeNode(h, true)));
    } catch (e) {
      if (!dead) clear(treeBox).appendChild(el('div', { class: 'error', role: 'alert' }, `Couldn't load the hierarchy: ${e.message}`));
    }
  }

  // ---- Preferences and backup
  const prefBox = el('div', {});
  const prefMsg = el('div', { role: 'alert' });
  const backupBox = el('div', {});
  const backupMsg = el('div', { role: 'alert' });
  const statusBox = el('div', {});
  container.appendChild(el('h2', {}, 'Preferences'));
  container.appendChild(prefBox);
  container.appendChild(el('h2', {}, 'Backup'));
  container.appendChild(backupBox);

  let current = {};
  const fields = {};

  function say(box, text, ok) {
    clear(box);
    if (text) box.appendChild(el('div', { class: ok ? 'status' : 'error' }, text));
  }

  // Saves only the keys whose value changed. Server errors show inline.
  async function saveKeys(keys, box) {
    const patch = {};
    try {
      for (const k of keys) {
        const v = fields[k].read();
        if (JSON.stringify(v) !== JSON.stringify(current[k])) patch[k] = v;
      }
    } catch (e) {
      say(box, e.message);
      return false;
    }
    if (!Object.keys(patch).length) { say(box, 'Nothing changed.', true); return true; }
    try {
      current = await api.put('/api/settings', patch);
      say(box, 'Saved.', true);
      return true;
    } catch (e) {
      say(box, e.message);
      return false;
    }
  }

  function drawBackupStatus(b) {
    clear(statusBox);
    if (!b) return;
    const when = b.last_ok ? new Date(b.last_ok).toLocaleString() : 'never';
    statusBox.appendChild(el('dl', { class: 'review-list' },
      el('dt', {}, 'Remote'), el('dd', {}, b.remote_set ? 'set' : 'not set'),
      el('dt', {}, 'Last successful push'), el('dd', {}, when),
      el('dt', {}, 'Waiting to push'), el('dd', {}, b.pending ? 'yes' : 'no'),
      el('dt', {}, 'Last error'), el('dd', {}, b.last_error || 'none')));
    if (b.last_error) statusBox.appendChild(el('div', { class: 'error' }, b.last_error));
  }

  async function refreshStatus() {
    try {
      const s = await api.get('/api/state');
      if (dead) return;
      drawBackupStatus(s.backup);
    } catch (e) {
      if (!dead) say(backupMsg, `Couldn't read the backup status: ${e.message}`);
    }
  }

  const pushBtn = el('button', { type: 'button', onclick: async () => {
    pushBtn.disabled = true;
    say(backupMsg, '');
    try {
      const b = await api.post('/api/backup/push', {});
      if (dead) return;
      drawBackupStatus(b);
      if (!b.remote_set) say(backupMsg, 'Set a backup remote above and save it first.');
      else if (b.last_error) {
        say(backupMsg, b.pending ? 'The push failed. The error is shown above; BrainHub will keep retrying.'
          : 'The push failed. The error is shown above.');
      }
      else say(backupMsg, 'Pushed.', true);
    } catch (e) {
      say(backupMsg, `Couldn't push: ${e.message}`);
    } finally {
      pushBtn.disabled = false;
    }
  } }, 'Push now');

  // Load everything; each part reports its own failure.
  let state = {};
  try { state = await api.get('/api/state'); } catch (_) { /* the shell banner already says so */ }
  if (dead) return cleanup;
  if (state.brain_path) {
    hintLine.appendChild(document.createTextNode('Open '));
    hintLine.appendChild(el('code', {}, state.brain_path));
    hintLine.appendChild(document.createTextNode(' in Obsidian for a graph view.'));
  }

  try {
    const map = normalizeMap(await api.get('/api/map'));
    if (dead) return cleanup;
    const saved = savedNodes(map);  // these slugs are read-only; new rows still auto-slug
    const editors = createMapEditors(map, { onChange: () => say(mapMsg, ''), isExisting: (n) => saved.has(n) });
    const saveBtn = el('button', { type: 'button', class: 'primary', onclick: async () => {
      if (hasErrors(editors.validateAndShow())) { say(mapMsg, 'Fix the problems above, then save.'); return; }
      saveBtn.disabled = true;
      try {
        await api.put('/api/map', map);
        for (const n of savedNodes(map)) saved.add(n);  // now on the server: lock their slugs
        for (const k of ['areas', 'projects', 'courses', 'people']) editors[k].refresh();
        say(mapMsg, 'Saved. Folders are created and the change is committed.', true);
        loadTree();
      } catch (e) {
        say(mapMsg, e.message);
      } finally {
        saveBtn.disabled = false;
      }
    } }, 'Save brain map');
    mapBox.appendChild(el('div', {},
      el('h3', {}, 'Areas'), editors.areas.node,
      el('h3', {}, 'Projects'), editors.projects.node,
      el('h3', {}, 'Uni courses'), editors.courses.node,
      el('h3', {}, 'People'), editors.people.node,
      el('div', { class: 'actions' }, saveBtn), mapMsg));
  } catch (e) {
    mapBox.appendChild(el('div', { class: 'error', role: 'alert' }, `Couldn't load the brain map: ${e.message}`));
  }

  await loadTree();

  try {
    current = await api.get('/api/settings');
    if (dead) return cleanup;
    for (const [k, v] of Object.entries(current)) fields[k] = buildField(k, v);
    const prefKeys = Object.keys(current).filter((k) => !BACKUP_KEYS.includes(k));
    prefBox.appendChild(el('div', {}, prefKeys.map((k) => fields[k].node),
      el('div', { class: 'actions' }, el('button', { type: 'button', class: 'primary', onclick: () => saveKeys(prefKeys, prefMsg) }, 'Save preferences')),
      prefMsg));
    backupBox.appendChild(el('div', {}, BACKUP_KEYS.filter((k) => fields[k]).map((k) => fields[k].node),
      el('p', { class: 'muted' }, 'Create an empty private repo first, then paste its URL.'),
      el('div', { class: 'actions' },
        el('button', { type: 'button', class: 'primary', onclick: async () => { if (await saveKeys(BACKUP_KEYS, backupMsg)) refreshStatus(); } }, 'Save backup settings'),
        pushBtn),
      backupMsg, statusBox));
    drawBackupStatus(state.backup);
  } catch (e) {
    prefBox.appendChild(el('div', { class: 'error', role: 'alert' }, `Couldn't load the preferences: ${e.message}`));
  }

  subs.push(api.onEvent('map.changed', () => loadTree()));
  subs.push(api.onEvent('items.changed', () => loadTree()));
  subs.push(api.onEvent('backup.status', ({ data }) => drawBackupStatus(data)));
  return cleanup;
}
