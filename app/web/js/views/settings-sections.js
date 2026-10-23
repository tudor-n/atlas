// Settings' sections (phase 4.5 design §4): BRAIN MAP, HIERARCHY, PREFERENCES, BACKUP, CLAUDE CODE and
// ABOUT, plus the small builders every section uses (the 2-column preference row, the section's
// action bar). TABLET, VOICE and SYNC live in tablet-settings.js, voice-settings.js and claude-sync.js.
// Each section is `async (ctx) => cleanup?`, ctx = {body, bar, state, isDead, setDot}. Text only via el().
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { button, dot } from '../components/ui.js';
import { createMapEditors, normalizeMap, hasErrors, savedNodes } from '../components/map-editor.js';
import { runtimeText } from './runtime-model.js';
import { prefLabel, backupDot, integrationDot, backupRows, counts, hierarchyRows } from './settings-model.js';

// -- builders shared by the sections (and the setup wizard's rows) ----------------------------

// A 2-column row: uppercase label and an optional hint on the left, the control on the right.
// forId ties the label to the control (a <label for>); without it the label is plain text.
export function prefRow(text, control, { hint, forId, cls } = {}) {
  return el('div', { class: cls ? `pf ${cls}` : 'pf' },
    el('div', { class: 'pfl' },
      forId ? el('label', { class: 'lb', for: forId }, text) : el('span', { class: 'lb' }, text),
      hint ? el('p', { class: 'hint' }, hint) : null),
    el('div', { class: 'pfc' }, control));
}

// A read-only value in a row's control column: mono for stamps, paths and numbers.
export const value = (text, kind) => el('span', { class: kind ? `pv ${kind}` : 'pv' }, text);

// A 40px sub-section header with a mono count (or any short text) on the right.
export function groupHead(text, count) {
  const n = el('span', { class: 'n' }, count === null || count === undefined ? '' : String(count));
  const node = el('div', { class: 'sect-h' }, el('h3', { class: 'lb' }, text), n);
  node.setCount = (c) => { n.textContent = c === null || c === undefined ? '' : String(c); };
  return node;
}

export const intro = (...text) => el('p', { class: 'st-intro' }, ...text);
export const failLine = (text) => el('div', { class: 'err st-fail', role: 'alert' }, text);

// An on/off toggle button (the arm's .pl: outlined, white when on) for a set of choices.
export function toggle(text, on, { onflip, title } = {}) {
  const b = el('button', { type: 'button', class: on ? 'b h40 tg on' : 'b h40 tg', 'aria-pressed': on ? 'true' : 'false', title }, text);
  b.addEventListener('click', () => {
    const next = !b.classList.contains('on');
    b.classList.toggle('on', next);
    b.setAttribute('aria-pressed', next ? 'true' : 'false');
    if (onflip) onflip(next);
  });
  b.isOn = () => b.classList.contains('on');
  return b;
}

// A section's 72px action bar: the status/error line on the left, its buttons on the right.
// The section's name sits on the left, as CAPTURE does on the capture bar right below it, so the two
// SAVE buttons can't be mistaken for each other. set(...buttons) replaces the buttons and shows the
// bar; with no buttons and no message it hides.
export function sectionBar(name) {
  const msg = el('div', { class: 'msg', role: 'alert', 'aria-live': 'polite' });
  const btns = el('div', { class: 'btns' });
  const node = el('div', { class: 'actbar st-bar', hidden: true }, name ? el('span', { class: 'lb nm' }, name) : null, msg, btns);
  return {
    node,
    say(text, ok) {
      msg.textContent = text || '';
      msg.className = ok ? 'msg ok' : 'msg err';
      if (text) node.hidden = false;
    },
    set(...buttons) {
      clear(btns);
      for (const b of buttons.flat()) if (b) btns.appendChild(b);
      node.hidden = !btns.childNodes.length && !msg.textContent;
    },
  };
}

// -- BRAIN MAP ----------------------------------------------------------------------------------
const MAP_PARTS = [['areas', 'AREAS'], ['projects', 'PROJECTS'], ['courses', 'UNI COURSES'], ['people', 'PEOPLE']];

export async function mapSection({ body, bar, state, isDead }) {
  let map;
  try { map = normalizeMap(await api.get('/api/map')); } catch (e) {
    if (!isDead()) body.appendChild(failLine(`Couldn't load the brain map: ${e.message}`));
    return;
  }
  if (isDead()) return;
  const saved = savedNodes(map);  // these slugs are read-only; new rows still auto-slug
  const heads = {};
  const recount = () => { for (const [k] of MAP_PARTS) heads[k].setCount(map[k].length); };
  const editors = createMapEditors(map, {
    onChange: () => { bar.say(''); recount(); }, isExisting: (n) => saved.has(n), os: state.os,
  });
  body.appendChild(intro('Where your notes go: the top-level areas, your projects and uni courses, and the people you write about. ',
    'Saving creates the folders and commits the change.'));
  for (const [k, t] of MAP_PARTS) {
    heads[k] = groupHead(t, map[k].length);
    body.appendChild(el('section', { class: 'st-grp' }, heads[k], editors[k].node));
  }
  const saveBtn = button('SAVE', { primary: true, onclick: async () => {
    if (hasErrors(editors.validateAndShow())) { bar.say('Fix the problems marked above, then save.'); return; }
    saveBtn.disabled = true;
    try {
      await api.put('/api/map', map);
      for (const n of savedNodes(map)) saved.add(n);  // now on the server: lock their slugs
      for (const [k] of MAP_PARTS) editors[k].refresh();
      if (!isDead()) bar.say('Saved. Folders are created and the change is committed.', true);
    } catch (e) {
      if (!isDead()) bar.say(e.message);
    } finally {
      saveBtn.disabled = false;
    }
  } });
  bar.set(saveBtn);
}

// -- HIERARCHY ----------------------------------------------------------------------------------
const KIND = { area: 'AREA', project: 'PROJECT', course: 'COURSE', person: 'PERSON' };

export async function hierarchySection({ body, isDead, head }) {
  const box = el('div', { class: 'st-tree' });
  body.append(intro('The brain as folders, with how many notes and items each holds. Open the brain folder in Obsidian for a graph view (see About).'), box);
  async function load() {
    try {
      const h = await api.get('/api/hierarchy');
      if (isDead()) return;
      if (head) head.setCount(counts(h));
      const rows = hierarchyRows(h);
      clear(box);
      if (!rows.length) { box.appendChild(el('div', { class: 'empty' }, 'Nothing in the brain yet.')); return; }
      for (const r of rows) {
        const name = r.kind === 'project'
          ? el('a', { class: 'tn', href: `#/project/${encodeURIComponent(r.slug)}` }, r.name)
          : el('span', { class: 'tn' }, r.name);
        box.appendChild(el('div', { class: `row tr lv${r.depth}` },
          el('span', { class: 'lb tk' }, KIND[r.kind] || String(r.kind || '').toUpperCase()),
          el('span', { class: 'grow' }, name),
          el('span', { class: 'sb' }, r.counts)));
      }
    } catch (e) {
      if (!isDead()) clear(box).appendChild(failLine(`Couldn't load the hierarchy: ${e.message}`));
    }
  }
  await load();
  const offs = [api.onEvent('map.changed', () => load()), api.onEvent('items.changed', () => load())];
  return () => { for (const off of offs) off(); };
}

// -- PREFERENCES (and BACKUP's two keys) ------------------------------------------------------
const MODELS = ['haiku', 'sonnet', 'opus'];
const WIDGET_TILES = ['today', 'usage', 'agents', 'people', 'deadlines', 'review', 'projects', 'arm'];
const BACKUP_KEYS = ['backup_remote', 'backup_debounce_s'];
const OWN_SECTION = /^(tablet|voice)_/;
const HINTS = {
  port: 'Takes effect after BrainHub restarts. Then install the Claude Code integration again and re-run desktop-config, and restart BrainHub Desktop.',
  claude_path: 'Command name of the claude CLI (like claude), not a path: each OS finds it on its own PATH. Takes effect after BrainHub restarts.',
  quiet_hours: 'The voice stays silent in these hours, and date-only reminders speak when they end.',
  backup_remote: 'Create an empty private repo first, then paste its URL.',
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

// One control per settings key: {control, forId, read}. read() returns the value or throws Error(message).
function buildField(key, val) {
  const id = `pref-${key}`;
  const name = prefLabel(key);
  if (key === 'quiet_hours') {
    const a = el('input', { type: 'time', id, class: 'm', value: val[0], 'aria-label': 'Quiet hours from' });
    const b = el('input', { type: 'time', class: 'm', value: val[1], 'aria-label': 'Until' });
    return {
      forId: id,
      control: el('div', { class: 'pair' }, a, el('span', { class: 'lb' }, 'TO'), b),
      read() {
        if (!a.value || !b.value) throw new Error('Pick both quiet-hours times.');
        return [a.value, b.value];
      },
    };
  }
  if (key === 'widget_tiles' && Array.isArray(val)) {
    const tiles = WIDGET_TILES.map((t) => ({ t, b: toggle(t.toUpperCase(), val.includes(t), { title: prefLabel(t) }) }));
    return {
      control: el('div', { class: 'tgs', role: 'group', 'aria-label': name }, tiles.map((x) => x.b)),
      read() {
        const on = new Set(tiles.filter((x) => x.b.isOn()).map((x) => x.t));
        if (!on.size) throw new Error('Pick at least one widget tile.');
        return [...val.filter((t) => on.has(t)), ...WIDGET_TILES.filter((t) => on.has(t) && !val.includes(t))];
      },
    };
  }
  if (typeof val === 'number') {
    const input = el('input', { type: 'number', id, class: 'm num', value: String(val), step: 'any' });
    return {
      forId: id, control: input,
      read() {
        const n = Number(input.value);
        if (input.value.trim() === '' || !Number.isFinite(n)) throw new Error(`${name} must be a number.`);
        return n;
      },
    };
  }
  if (key.endsWith('_model')) {
    const custom = el('input', { type: 'text', value: MODELS.includes(val) ? '' : String(val ?? ''),
      placeholder: 'model name', 'aria-label': `${name} (custom name)`, spellcheck: 'false' });
    const select = el('select', { id, onchange: sync },
      [...MODELS, 'custom'].map((m) => el('option', { value: m, selected: (MODELS.includes(val) ? val : 'custom') === m }, m)));
    function sync() { custom.hidden = select.value !== 'custom'; }
    sync();
    return {
      forId: id, control: el('div', { class: 'pair' }, select, custom),
      read() {
        if (select.value !== 'custom') return select.value;
        if (!custom.value.trim()) throw new Error(`${name}: type a model name or pick one from the list.`);
        return custom.value.trim();
      },
    };
  }
  // strings, and the nullable backup_remote (empty means null)
  const input = el('input', { type: 'text', id, value: val ?? '', spellcheck: 'false', class: /url|path|remote/.test(key) ? 'm wide' : 'wide',
    placeholder: key === 'backup_remote' ? 'https://github.com/you/brainhub-backup.git' : null });
  return {
    forId: id, control: input,
    read() {
      const v = input.value.trim();
      if (key === 'backup_remote') return v || null;
      if (key === 'claude_path' && !v) throw new Error('Claude path must not be empty.');
      return v;
    },
  };
}

// Loads /api/settings and builds the fields for `pick(keys)`. Returns {current, fields, keys, save}.
async function settingsForm(pick) {
  const holder = { current: await api.get('/api/settings') };
  const keys = pick(Object.keys(holder.current));
  const fields = Object.fromEntries(keys.map((k) => [k, buildField(k, holder.current[k])]));
  // Saves only the keys whose value changed: {ok, text}.
  holder.save = async () => {
    const patch = {};
    try {
      for (const k of keys) {
        const v = fields[k].read();
        if (JSON.stringify(v) !== JSON.stringify(holder.current[k])) patch[k] = v;
      }
    } catch (e) { return { ok: false, text: e.message }; }
    if (!Object.keys(patch).length) return { ok: true, text: 'Nothing changed.' };
    try {
      holder.current = await api.put('/api/settings', patch);
      return { ok: true, text: 'Saved.', changed: true };
    } catch (e) { return { ok: false, text: e.message }; }
  };
  holder.rows = () => keys.map((k) => prefRow(prefLabel(k).toUpperCase(), fields[k].control, { hint: HINTS[k], forId: fields[k].forId }));
  holder.keys = keys;
  return holder;
}

export async function preferencesSection({ body, bar, isDead }) {
  let form;
  try {
    // The tablet's and the voice's keys have their own sections, and so does backup.
    form = await settingsForm((all) => all.filter((k) => !BACKUP_KEYS.includes(k) && !OWN_SECTION.test(k)));
  } catch (e) {
    if (!isDead()) body.appendChild(failLine(`Couldn't load the preferences: ${e.message}`));
    return;
  }
  if (isDead()) return;
  body.append(intro('How BrainHub files, recalls, nudges and reports. Saved for both OSes.'), el('div', { class: 'pfs' }, form.rows()));
  const save = button('SAVE', { primary: true, onclick: async () => {
    save.disabled = true;
    const r = await form.save();
    save.disabled = false;
    if (!isDead()) bar.say(r.text, r.ok);
  } });
  bar.set(save);
}

// -- BACKUP -------------------------------------------------------------------------------------
export async function backupSection({ body, bar, state, isDead, setDot }) {
  let form;
  try { form = await settingsForm((all) => BACKUP_KEYS.filter((k) => all.includes(k))); } catch (e) {
    if (!isDead()) body.appendChild(failLine(`Couldn't load the backup settings: ${e.message}`));
    return;
  }
  if (isDead()) return;
  const statusHead = groupHead('STATUS');
  const statusBox = el('div', { class: 'pfs' });
  function drawStatus(b) {
    clear(statusBox);
    setDot('backup', backupDot(b));  // the rail follows a push made here or in the background
    const kind = backupDot(b);
    const n = clear(statusHead.querySelector('.n'));
    if (kind) n.append(dot(kind), kind === 'g' ? 'OK' : 'ERROR');
    if (!b) { statusBox.appendChild(el('div', { class: 'empty' }, "The backup status isn't known yet.")); return; }
    for (const [l, v, k] of backupRows(b)) statusBox.appendChild(prefRow(l.toUpperCase(), value(v, k)));
  }
  async function refreshStatus() {
    try {
      const s = await api.get('/api/state');
      if (!isDead()) drawStatus(s.backup);
    } catch (e) {
      if (!isDead()) bar.say(`Couldn't read the backup status: ${e.message}`);
    }
  }
  body.append(intro('Every change to the brain is pushed to a private git repo of yours, a little while after it happens.'),
    el('div', { class: 'pfs' }, form.rows()), el('section', { class: 'st-grp' }, statusHead, statusBox));
  drawStatus(state.backup);

  const save = button('SAVE', { primary: true, onclick: async () => {
    save.disabled = true;
    const r = await form.save();
    save.disabled = false;
    if (isDead()) return;
    bar.say(r.text, r.ok);
    if (r.ok) refreshStatus();
  } });
  const push = button('PUSH NOW', { onclick: async () => {
    push.disabled = true;
    bar.say('');
    try {
      const b = await api.post('/api/backup/push', {});
      if (isDead()) return;
      drawStatus(b);
      if (!b.remote_set) bar.say('Set a backup remote above and save it first.');
      else if (b.last_error) {
        bar.say(b.pending ? 'The push failed. The error is shown above; BrainHub will keep retrying.'
          : 'The push failed. The error is shown above.');
      } else bar.say('Pushed.', true);
    } catch (e) {
      if (!isDead()) bar.say(`Couldn't push: ${e.message}`);
    } finally {
      push.disabled = false;
    }
  } });
  bar.set(push, save);
  return api.onEvent('backup.status', ({ data }) => { if (!isDead()) drawStatus(data); });
}

// -- CLAUDE CODE (the integration) ---------------------------------------------------------------
const whenText = (iso) => (iso ? new Date(iso).toLocaleString() : 'never');

export async function claudeSection({ body, bar, isDead, setDot }) {
  const box = el('div', {});
  body.append(intro("Adds BrainHub's hooks and status line to your Claude Code user settings, so the Agents tab and the usage meter fill in. ",
    'A backup of the file is made first.'), box);

  function draw(s) {
    clear(box);
    setDot('claude', integrationDot(s));
    const ev = (s.events || []).length ? ` (${s.events.join(', ')})` : '';
    const port = s.port_matches ? 'yes' : (s.hooks_installed ? 'no. Install again so Claude Code reports to the right port.' : 'no');
    const state = (on, text) => el('span', { class: 'pv' }, dot(on ? 'g' : ''), text);
    const stale = (s.hooks_installed || s.statusline_installed) && s.paths_current === false;
    box.appendChild(el('div', { class: 'pfs' },
      prefRow('HOOKS', state(s.hooks_installed, s.hooks_installed ? `installed${ev}` : 'not installed')),
      prefRow('STATUS LINE', state(s.statusline_installed, s.statusline_installed ? 'installed' : 'not installed')),
      prefRow('PORT MATCHES', s.port_matches ? state(true, 'yes')
        : el('span', { class: s.hooks_installed ? 'pv amb' : 'pv' }, port)),
      prefRow('LAST HOOK EVENT', value(whenText(s.last_hook_at), 'm')),
      prefRow('LAST USAGE UPDATE', value(whenText(s.last_usage_at), 'm')),
      prefRow('SETTINGS FILE', value(s.settings_path || '–', 'm path'))),
      // Quiet: installed before `runtime init`, or its interpreter or script has moved since.
      stale ? el('p', { class: 'st-warn' }, el('span', { class: 'sq' }), 'Reinstall: the installed hooks point at an old Python or script.') : null);
    const run = (path, verb) => async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      bar.say('');
      try {
        const next = await api.post(path, {});
        if (!isDead()) { draw(next); bar.say(verb, true); }
      } catch (err) {
        if (!isDead()) { btn.disabled = false; bar.say(err.message); }
      }
    };
    bar.set(button('REMOVE', { onclick: run('/api/integration/uninstall', 'Removed.') }),
      button('INSTALL', { primary: true, onclick: run('/api/integration/install', 'Installed.') }));
  }
  try {
    const s = await api.get('/api/integration');
    if (!isDead()) draw(s);
  } catch (e) {
    if (!isDead()) box.appendChild(failLine(`Couldn't read the integration status: ${e.message}`));
  }
}

// -- ABOUT --------------------------------------------------------------------------------------
export async function aboutSection({ body, state }) {
  const rt = runtimeText(state.runtime);
  body.append(intro('BrainHub keeps your brain as plain Markdown files in a git repo.'), el('div', { class: 'pfs' },
    prefRow('BRAIN FOLDER', value(state.brain_path || '–', 'm path'),
      { hint: state.brain_path ? 'Open it in Obsidian for a graph view.' : null }),
    prefRow('RUNTIME', value(rt || '–', rt ? 'm' : 'f'),
      { hint: rt ? 'The commit and slot this hub runs from, and any update waiting.' : 'Not running from a runtime slot (a development run).' }),
    prefRow('THIS OS', value(state.os || '–', 'm'))));
}
