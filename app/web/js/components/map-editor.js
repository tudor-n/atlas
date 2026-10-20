// Brain-map editors shared by the setup wizard and Settings. Four editors (areas, projects, courses,
// people) edit one plain `map` object in place: {areas, projects, courses, people}.
// validateMap() mirrors brainmap.validate_map on the server, so mistakes show next to the right editor.
import { el, clear } from '../dom.js';

export const DEFAULT_AREAS = ['general', 'projects', 'uni', 'personal'];
// Top-level brain folders (mirrors brainmap._RESERVED). Only the four default areas may use the default slugs.
const RESERVED = ['log', '_items', 'people', ...DEFAULT_AREAS];
const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;

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
  if (Array.isArray(raw.areas)) m.areas = nodes(raw.areas).map(base);
  m.projects = nodes(raw.projects).map((n) => ({ ...base(n), is_company: n.is_company === true, folders: strs(n.folders) }));
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

const field = (text, input, cls) => el('label', { class: `field ${cls || ''}` }, text, input);

// Generic list editor over the array at map[key].
function rowsEditor({ map, key, label, addText, makeRow, blank, canRemove, onRemove, changed }) {
  const rows = el('div', { class: 'erows' });
  const errBox = el('div', { role: 'alert' });
  const node = el('div', { class: 'editor' }, rows, errBox,
    el('button', { type: 'button', class: 'add', onclick: () => {
      map[key].push(blank());
      draw();
      changed();
      const last = rows.lastElementChild;
      const first = last && last.querySelector('input[type="text"]');
      if (first) first.focus();
    } }, addText));

  function draw() {
    clear(rows);
    if (!map[key].length) rows.appendChild(el('p', { class: 'empty' }, `No ${label}s yet.`));
    for (const item of map[key]) {
      const row = el('div', { class: 'erow' }, makeRow(item));
      if (!canRemove || canRemove(item)) {
        row.appendChild(el('button', { type: 'button', class: 'remove', 'aria-label': `Remove ${label} ${item.name || item.slug || ''}`.trim(),
          onclick: () => {
            map[key] = map[key].filter((x) => x !== item);
            if (onRemove) onRemove(item);
            draw();
            changed();
          } }, 'Remove'));
      }
      rows.appendChild(row);
    }
  }
  draw();
  return {
    node, refresh: draw,
    setErrors(list) { clear(errBox); for (const m of list) errBox.appendChild(el('div', { class: 'error' }, m)); },
  };
}

// Name + slug inputs with the auto-slug behaviour: while the slug still equals slugify(previous name)
// it follows the name; once the owner edits it, it stays put. `slugChanged(old, new)` is optional.
function nameSlug(item, { slugLocked, slugChanged, changed }) {
  const slugInput = el('input', { type: 'text', value: item.slug, readonly: slugLocked || null, spellcheck: 'false',
    oninput: () => {
      const old = item.slug;
      item.slug = slugInput.value.trim();
      if (slugChanged) slugChanged(old, item.slug);
      changed();
    } });
  const nameInput = el('input', { type: 'text', value: item.name,
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
  return [field('Name', nameInput, 'grow'), field('Folder name', slugInput, 'slug')];
}

// createMapEditors(map, {onChange}) -> {areas, projects, courses, people, validate, validateAndShow}
// Each editor has {node, refresh, setErrors}. onChange fires after every edit (callers persist or clear errors).
export function createMapEditors(map, { onChange } = {}) {
  let live = false;  // show errors while typing only after a first failed Next/Save
  const editors = {};

  function show(errs) { for (const [key] of TYPES) editors[key].setErrors(errs[key]); }
  function changed() {
    if (onChange) onChange();
    if (live) show(validateMap(map));
  }

  editors.areas = rowsEditor({
    map, key: 'areas', label: 'area', addText: 'Add an area', changed,
    blank: () => ({ slug: '', name: '' }),
    canRemove: (a) => !DEFAULT_AREAS.includes(a.slug),
    makeRow: (a) => nameSlug(a, { slugLocked: DEFAULT_AREAS.includes(a.slug), changed }),
  });

  const renameRefs = (from, to) => renameProjectRefs(map, from, to);
  const projectsChanged = () => { editors.people.refresh(); changed(); };
  editors.projects = rowsEditor({
    map, key: 'projects', label: 'project', addText: 'Add a project', changed: projectsChanged,
    blank: () => ({ slug: '', name: '', is_company: false, folders: [] }),
    onRemove: (p) => dropProjectRefs(map, p.slug),
    makeRow: (p) => {
      const company = el('input', { type: 'checkbox', checked: p.is_company === true,
        onchange: () => {
          if (company.checked) for (const o of map.projects) o.is_company = false;
          p.is_company = company.checked;
          editors.projects.refresh();
          changed();
        } });
      const folders = el('textarea', { rows: 2, spellcheck: 'false', value: (p.folders || []).join('\n'),
        oninput: () => { p.folders = folders.value.split('\n').map((s) => s.trim()).filter(Boolean); changed(); } });
      return [
        nameSlug(p, { slugChanged: renameRefs, changed: projectsChanged }),
        el('label', { class: 'check' }, company, 'This is my company'),
        field('Folder paths on this PC (optional, one per line)', folders, 'wide'),
      ];
    },
  });

  editors.courses = rowsEditor({
    map, key: 'courses', label: 'course', addText: 'Add a course', changed,
    blank: () => ({ slug: '', name: '' }),
    makeRow: (c) => nameSlug(c, { changed }),
  });

  editors.people = rowsEditor({
    map, key: 'people', label: 'person', addText: 'Add a person', changed,
    blank: () => ({ slug: '', name: '', projects: [] }),
    makeRow: (p) => {
      const boxes = map.projects.length
        ? map.projects.map((proj) => {
          const cb = el('input', { type: 'checkbox', checked: p.projects.includes(proj.slug),
            onchange: () => {
              p.projects = p.projects.filter((r) => r !== proj.slug);
              if (cb.checked) p.projects.push(proj.slug);
              changed();
            } });
          return el('label', { class: 'check' }, cb, proj.name || proj.slug || '(unnamed project)');
        })
        : el('span', { class: 'muted small' }, 'Add projects first to link a person to them.');
      return [nameSlug(p, { changed }), el('div', { class: 'wide' }, el('div', { class: 'meta' }, 'Projects'), boxes)];
    },
  });

  editors.validate = () => validateMap(map);
  editors.validateAndShow = () => { live = true; const errs = validateMap(map); show(errs); return errs; };
  return editors;
}
