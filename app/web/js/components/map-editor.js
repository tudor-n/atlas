// Brain-map editors shared by the setup wizard and Settings. Four editors (areas, projects, courses,
// people) edit one plain `map` object in place: {areas, projects, courses, people}.
// validateMap() mirrors brainmap.validate_map on the server, so mistakes show next to the right editor.
import { el, clear } from '../dom.js';

export const DEFAULT_AREAS = ['general', 'projects', 'uni', 'personal'];
// Top-level brain folders (mirrors brainmap._RESERVED). Only the four default areas may use the default slugs.
const RESERVED = ['log', '_items', 'people', ...DEFAULT_AREAS];
const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;
// A project's folders per OS (Plan decision P7): os_folders = {windows: [...], linux: [...]}.
const OS_NAMES = ['windows', 'linux'];
const OS_LABEL = { windows: 'Windows', linux: 'Linux' };
const OS_EXAMPLE = { windows: 'D:\\RobotArmControl', linux: '/home/you/dev/RobotArmControl' };

// Same rules as brainmap.path_os: a drive letter or UNC path is Windows, a leading "/" is Linux, else null.
export function pathOs(folder) {
  if (typeof folder !== 'string') return null;
  if (/^(?:[A-Za-z]:[\\/]|\\\\)/.test(folder)) return 'windows';
  return folder.startsWith('/') ? 'linux' : null;
}

// Same rules as brainmap.slugify: NFKD, drop combining marks, lowercase, non-alphanumeric runs to "-", trimmed.
export function slugify(name) {
  const folded = String(name).normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
  return folded.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

export function defaultMap() {
  return {
    areas: DEFAULT_AREAS.map((s) => ({ slug: s, name: s.charAt(0).toUpperCase() + s.slice(1) })),
    projects: [], courses: [], people: [],
  };
}

// Coerce anything (saved state, a server map) into a well-formed map. Unknown node keys are kept.
export function normalizeMap(raw) {
  const m = defaultMap();
  if (!raw || typeof raw !== 'object') return m;
  const nodes = (v) => (Array.isArray(v) ? v.filter((n) => n && typeof n === 'object' && !Array.isArray(n)) : []);
  const strs = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []);
  const base = (n) => ({ ...n, slug: typeof n.slug === 'string' ? n.slug : '', name: typeof n.name === 'string' ? n.name : '' });
  // Legacy `folders` entries move to the column their shape belongs to, and `folders` is dropped: the
  // editor saves os_folders only. An entry of no known shape goes under Windows (maps from before dual
  // boot were made there), where validateMap flags it rather than it vanishing.
  const osFolders = (n) => {
    const per = n.os_folders && typeof n.os_folders === 'object' ? n.os_folders : {};
    const out = Object.fromEntries(OS_NAMES.map((os) => [os, strs(per[os])]));
    for (const f of strs(n.folders)) out[pathOs(f) || 'windows'].push(f);
    for (const os of OS_NAMES) out[os] = [...new Set(out[os])];
    return out;
  };
  if (Array.isArray(raw.areas)) m.areas = nodes(raw.areas).map(base);
  m.projects = nodes(raw.projects).map((n) => {
    const p = { ...base(n), is_company: n.is_company === true, os_folders: osFolders(n) };
    delete p.folders;
    return p;
  });
  m.courses = nodes(raw.courses).map(base);
  m.people = nodes(raw.people).map((n) => ({ ...base(n), projects: strs(n.projects) }));
  return m;
}

const TYPES = [['areas', 'area'], ['projects', 'project'], ['courses', 'course'], ['people', 'person']];

export function validateMap(m) {
  const out = { areas: [], projects: [], courses: [], people: [] };
  for (const [key, label] of TYPES) {
    const seen = new Set();
    for (const n of m[key]) {
      const slug = n.slug || '';
      const who = n.name || slug || 'a new entry';
      if (!SLUG.test(slug)) out[key].push(`${who}: the folder name ${JSON.stringify(slug)} must use only a-z, 0-9 and single dashes.`);
      else if (seen.has(slug)) out[key].push(`${who}: another ${label} already uses the folder name "${slug}".`);
      seen.add(slug);
      if (!String(n.name || '').trim()) out[key].push(`The ${label} "${slug}" needs a name.`);
      if (RESERVED.includes(slug) && !(key === 'areas' && DEFAULT_AREAS.includes(slug))) {
        out[key].push(`${who}: "${slug}" is reserved. Pick another folder name.`);
      }
    }
  }
  for (const p of m.projects) {
    const who = p.name || p.slug || 'a new project';
    for (const os of OS_NAMES) {
      for (const f of (p.os_folders && p.os_folders[os]) || []) {
        const actual = pathOs(f);
        if (actual === os) continue;
        out.projects.push(actual
          ? `${who}: "${f}" is a ${OS_LABEL[actual]} folder. Move it to Folders on ${OS_LABEL[actual]}.`
          : `${who}: "${f}" isn't a full ${OS_LABEL[os]} folder path (like ${OS_EXAMPLE[os]}).`);
      }
    }
  }
  if (m.projects.filter((p) => p.is_company).length > 1) out.projects.push('Only one project can be the company.');
  const known = new Set(m.projects.map((p) => p.slug));
  for (const person of m.people) {
    for (const ref of person.projects) {
      if (!known.has(ref)) out.people.push(`${person.name || person.slug}: the project "${ref}" doesn't exist.`);
    }
  }
  return out;
}

// A project's slug changed from `from` to `to` (the project already has the new slug): its people follow.
// If another project still owns `from`, the links belong to that project and must not move.
export function renameProjectRefs(map, from, to) {
  if (from === to || map.projects.some((p) => p.slug === from)) return;
  for (const p of map.people) p.projects = p.projects.map((r) => (r === from ? to : r));
}

// A project was removed: drop its links, unless another project still has the same slug.
export function dropProjectRefs(map, slug) {
  if (map.projects.some((p) => p.slug === slug)) return;
  for (const p of map.people) p.projects = p.projects.filter((r) => r !== slug);
}

export const hasErrors = (errs) => Object.values(errs).some((l) => l.length);

// The node objects of a map as loaded from the server, for createMapEditors' isExisting.
export function savedNodes(map) {
  return new Set([...map.areas, ...map.projects, ...map.courses, ...map.people]);
}

// -- the editors (phase 4.5 design §4: hairline rows) ---------------------------------------------
// Each editor is a column header (NAME, FOLDER NAME), then one hairline row per entry: the name and
// folder-name fields and REMOVE on the first line, a project's folders and company toggle or a
// person's project toggles on the second. The CSS is in css/views/settings.css (shared with setup).

const field = (text, input, cls) => el('label', { class: cls ? `field ${cls}` : 'field' }, el('span', { class: 'lb' }, text), input);

// An on/off toggle (outlined, white when on), like the arm's .pl buttons.
function toggle(text, on, onflip, title) {
  const b = el('button', { type: 'button', class: on ? 'b h40 tg on' : 'b h40 tg', 'aria-pressed': on ? 'true' : 'false', title }, text);
  b.addEventListener('click', () => {
    const next = !b.classList.contains('on');
    b.classList.toggle('on', next);
    b.setAttribute('aria-pressed', next ? 'true' : 'false');
    onflip(next);
  });
  return b;
}

// Generic list editor over the array at map[key]. makeRow(item) -> {main: [name, slug], extra: node|null}.
function rowsEditor({ map, key, label, addText, makeRow, blank, canRemove, lockedText, onRemove, changed }) {
  const cols = el('div', { class: 'ecols', 'aria-hidden': 'true' },
    el('span', { class: 'lb' }, 'NAME'), el('span', { class: 'lb' }, 'FOLDER NAME'), el('span'));
  const rows = el('div', { class: 'erows' });
  const errBox = el('div', { class: 'eerr', role: 'alert' });
  const addBtn = el('button', { type: 'button', class: 'b h40 add', onclick: () => {
    map[key].push(blank());
    draw();
    changed();
    const last = rows.lastElementChild;
    const first = last && last.querySelector('input[type="text"]');
    if (first) first.focus();
  } }, addText);
  const node = el('div', { class: 'editor' }, cols, rows, errBox, el('div', { class: 'eadd' }, addBtn));

  function draw() {
    clear(rows);
    cols.hidden = !map[key].length;
    if (!map[key].length) rows.appendChild(el('div', { class: 'empty' }, `No ${label}s yet.`));
    for (const item of map[key]) {
      const { main, extra } = makeRow(item);
      const end = !canRemove || canRemove(item)
        ? el('button', { type: 'button', class: 'b h40 remove', 'aria-label': `Remove ${label} ${item.name || item.slug || ''}`.trim(),
          onclick: () => {
            map[key] = map[key].filter((x) => x !== item);
            if (onRemove) onRemove(item);
            draw();
            changed();
          } }, 'Remove')
        : el('span', { class: 'lb elock' }, lockedText || '');
      rows.appendChild(el('div', { class: 'erow' }, el('div', { class: 'e1' }, main, end), extra ? el('div', { class: 'e2' }, extra) : null));
    }
  }
  draw();
  return {
    node, refresh: draw,
    setErrors(list) { clear(errBox); for (const m of list) errBox.appendChild(el('p', { class: 'err' }, m)); },
  };
}

// Name + slug inputs with the auto-slug behaviour: while the slug still equals slugify(previous name)
// it follows the name; once the owner edits it, it stays put. `slugChanged(old, new)` is optional.
// A locked slug (a default area, or a node already saved on the server) is read-only: renaming its
// folder would strand the notes and items filed under it, so only the display name changes.
function nameSlug(item, { slugLocked, slugChanged, changed, label }) {
  const slugInput = el('input', { type: 'text', class: 'm slug', value: item.slug, readonly: slugLocked || null, spellcheck: 'false',
    'aria-label': `Folder name of the ${label}`,
    title: slugLocked ? "Saved folder names can't change; edit the name instead." : null,
    oninput: () => {
      const old = item.slug;
      item.slug = slugInput.value.trim();
      if (slugChanged) slugChanged(old, item.slug);
      changed();
    } });
  const nameInput = el('input', { type: 'text', value: item.name, 'aria-label': `Name of the ${label}`,
    oninput: () => {
      const old = item.slug;
      const followed = !slugLocked && item.slug === slugify(item.name);
      item.name = nameInput.value;
      if (followed) {
        item.slug = slugify(item.name);
        slugInput.value = item.slug;
        if (slugChanged) slugChanged(old, item.slug);
      }
      changed();
    } });
  return [nameInput, slugInput];
}

// createMapEditors(map, {onChange, isExisting, os}) -> {areas, projects, courses, people, validate, validateAndShow}
// Each editor has {node, refresh, setErrors}. onChange fires after every edit (callers persist or clear errors).
// isExisting(node) is true for nodes already saved on the server (Settings): their slug is read-only.
// os is the hub's OS (state.os): its folder column comes first, labelled "(this PC)".
export function createMapEditors(map, { onChange, isExisting = () => false, os = null } = {}) {
  let live = false;  // show errors while typing only after a first failed Next/Save
  const editors = {};

  function show(errs) { for (const [key] of TYPES) editors[key].setErrors(errs[key]); }
  function changed() {
    if (onChange) onChange();
    if (live) show(validateMap(map));
  }

  editors.areas = rowsEditor({
    map, key: 'areas', label: 'area', addText: 'Add an area', changed, lockedText: 'DEFAULT',
    blank: () => ({ slug: '', name: '' }),
    canRemove: (a) => !DEFAULT_AREAS.includes(a.slug),
    makeRow: (a) => ({ main: nameSlug(a, { slugLocked: DEFAULT_AREAS.includes(a.slug) || isExisting(a), changed, label: 'area' }) }),
  });

  const renameRefs = (from, to) => renameProjectRefs(map, from, to);
  const projectsChanged = () => { editors.people.refresh(); changed(); };
  const osOrder = os && OS_NAMES.includes(os) ? [os, ...OS_NAMES.filter((x) => x !== os)] : OS_NAMES;
  editors.projects = rowsEditor({
    map, key: 'projects', label: 'project', addText: 'Add a project', changed: projectsChanged,
    blank: () => ({ slug: '', name: '', is_company: false, os_folders: { windows: [], linux: [] } }),
    onRemove: (p) => dropProjectRefs(map, p.slug),
    makeRow: (p) => {
      const company = toggle('Company', p.is_company === true, (on) => {
        if (on) for (const o of map.projects) o.is_company = false;
        p.is_company = on;
        editors.projects.refresh();
        changed();
      }, 'This is my company (one project at most)');
      // One column per OS; each OS matches sessions against its own column only.
      const folders = (col) => {
        const box = el('textarea', { rows: 2, class: 'm', spellcheck: 'false', value: ((p.os_folders && p.os_folders[col]) || []).join('\n'),
          placeholder: `Optional, one per line, like ${OS_EXAMPLE[col]}`,
          oninput: () => {
            p.os_folders = { ...p.os_folders, [col]: box.value.split('\n').map((s) => s.trim()).filter(Boolean) };
            delete p.folders;
            changed();
          } });
        return field(`Folders on ${OS_LABEL[col]}${col === os ? ' (this PC)' : ''}`, box, 'grow');
      };
      return {
        main: nameSlug(p, { slugLocked: isExisting(p), slugChanged: renameRefs, changed: projectsChanged, label: 'project' }),
        extra: [osOrder.map(folders), el('div', { class: 'field co' }, company)],
      };
    },
  });

  editors.courses = rowsEditor({
    map, key: 'courses', label: 'course', addText: 'Add a course', changed,
    blank: () => ({ slug: '', name: '' }),
    makeRow: (c) => ({ main: nameSlug(c, { slugLocked: isExisting(c), changed, label: 'course' }) }),
  });

  editors.people = rowsEditor({
    map, key: 'people', label: 'person', addText: 'Add a person', changed,
    blank: () => ({ slug: '', name: '', projects: [] }),
    makeRow: (p) => {
      const links = map.projects.length
        ? map.projects.map((proj) => toggle(proj.name || proj.slug || '(unnamed project)', p.projects.includes(proj.slug), (on) => {
          p.projects = p.projects.filter((r) => r !== proj.slug);
          if (on) p.projects.push(proj.slug);
          changed();
        }))
        : el('span', { class: 'f' }, 'Add projects first to link a person to them.');
      return {
        main: nameSlug(p, { slugLocked: isExisting(p), changed, label: 'person' }),
        extra: el('div', { class: 'links', role: 'group', 'aria-label': `Projects of ${p.name || 'this person'}` },
          el('span', { class: 'lb' }, 'Projects'), links),
      };
    },
  });

  editors.validate = () => validateMap(map);
  editors.validateAndShow = () => { live = true; const errs = validateMap(map); show(errs); return errs; };
  return editors;
}
