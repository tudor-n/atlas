// Confirm card (spec 5.3): what was filed, one row per proposal, confirm / edit / dismiss,
// "Confirm all", "Tell it what's wrong", and Retry for failed filings.
import * as api from '../api.js';
import { el, clear, fmtWhen } from '../dom.js';

// -- map cache (project options) ------------------------------------------------------------
let mapCache = null;
api.onEvent('map.changed', () => { mapCache = null; });

async function loadMap() {
  if (!mapCache) mapCache = await api.get('/api/map');
  return mapCache;
}

function slugify(name) {
  return String(name).normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

// "e-20261020-140322-ab12" -> "2026-10-20"
function entryDate(entryId) {
  const m = /^e-(\d{4})(\d{2})(\d{2})-/.exec(entryId);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : '';
}

const KINDS = ['task', 'deadline', 'reminder', 'waiting', 'next_time'];
const KIND_LABEL = { task: 'task', deadline: 'deadline', reminder: 'reminder', waiting: 'waiting', next_time: 'next time' };
const UPDATE_STATUSES = ['to_read', 'done'];

// Node rows go first when an item names a project/person/area that is only proposed as a node
// on the same card; confirming the item confirms that node (server side).
function orderProposals(proposals) {
  const nodes = proposals.filter((p) => p.ptype === 'node');
  const placed = new Set();
  const out = [];
  for (const p of proposals) {
    if (p.ptype === 'node') {
      if (!placed.has(p.id)) { placed.add(p.id); out.push(p); }
      continue;
    }
    if (p.ptype === 'item') {
      for (const field of ['area', 'project', 'person']) {
        const slug = p.payload[field];
        if (!slug) continue;
        for (const n of nodes) {
          if (!placed.has(n.id) && n.payload.type === field && slugify(n.payload.name || '') === slug) {
            placed.add(n.id);
            out.push(n);
          }
        }
      }
    }
    out.push(p);
  }
  return out;
}

// Per-entry UI drafts (typed correction, open edit and its field values, last message). They live
// outside the card so a re-render, reload or recreated card never loses what the owner typed.
const drafts = new Map();
function draftFor(entryId) {
  if (!drafts.has(entryId)) drafts.set(entryId, { correction: '', editing: null, edits: {}, msg: '' });
  return drafts.get(entryId);
}

export function confirmCard(entryId, { entryText, onChange, onClose } = {}) {
  const root = el('section', { class: 'card', 'aria-label': 'Filing review' });
  const s = {
    data: null, map: null, items: {}, loadError: '', notice: '',
    draft: draftFor(entryId),
  };
  const unsubs = [];
  let destroyed = false;

  const mine = (ev) => ev.data && ev.data.entry_id === entryId;
  unsubs.push(api.onEvent('filing.done', (ev) => { if (mine(ev)) { s.notice = ''; reload(); } }));
  unsubs.push(api.onEvent('filing.failed', (ev) => { if (mine(ev)) { s.notice = ''; reload(); } }));
  unsubs.push(api.onEvent('filing.started', (ev) => { if (mine(ev)) reload(); }));
  unsubs.push(api.onEvent('ws.open', () => reload()));

  async function reload() {
    if (destroyed) return;
    try {
      const [data, map] = await Promise.all([api.get(`/api/filings/${encodeURIComponent(entryId)}`), loadMap().catch(() => null)]);
      s.data = data;
      s.map = map;
      s.loadError = '';
      if (data.proposals.some((p) => p.status === 'proposed' && p.ptype === 'update')) {
        try {
          const items = await api.get('/api/items');
          s.items = Object.fromEntries(items.map((i) => [i.id, i]));
        } catch (_) { /* the row falls back to the item id */ }
      }
    } catch (e) {
      s.loadError = e.message;
    }
    if (!destroyed) render();
  }

  const pending = () => (s.data ? s.data.proposals.filter((p) => p.status === 'proposed') : []);

  async function act(fn, errPrefix) {
    s.draft.msg = '';
    try {
      await fn();
    } catch (e) {
      s.draft.msg = `${errPrefix}: ${e.message}`;
    }
    await reload();
    if (onChange) onChange();
  }

  const confirmIds = (ids) => act(() => api.post('/api/proposals/confirm', { ids }), "Couldn't confirm");
  const dismissIds = (ids) => act(() => api.post('/api/proposals/dismiss', { ids }), "Couldn't dismiss");

  // -- rows ---------------------------------------------------------------------------------
  function dateLine(pl, kind) {
    const bits = [];
    if (pl.due) bits.push(`due ${fmtWhen(pl.due)}`);
    if (pl.remind_at) bits.push(`remind ${fmtWhen(pl.remind_at)}`);
    if (kind === 'next_time') bits.push('next session');
    if (kind === 'waiting') bits.push(`waiting since ${entryDate(entryId) || 'now'}`);
    return bits.join(' · ');
  }

  function warnings(p) {
    return (p.warnings || []).map((w) => el('div', { class: 'warn', role: 'note' }, `⚠ ${w}`));
  }

  function actions(p, canConfirm) {
    return el('div', { class: 'row-actions' },
      el('button', { class: 'icon', type: 'button', 'aria-label': 'Confirm', title: 'Confirm', disabled: !canConfirm || null,
        onclick: () => confirmIds([p.id]) }, '✓'),
      el('button', { class: 'icon', type: 'button', 'aria-label': 'Edit', title: 'Edit',
        onclick: () => { s.draft.editing = p.id; render(); } }, '✎'),
      el('button', { class: 'icon', type: 'button', 'aria-label': 'Dismiss', title: 'Dismiss',
        onclick: () => dismissIds([p.id]) }, '✕'));
  }

  function itemRow(p) {
    const pl = p.payload;
    const where = [pl.area, pl.project, pl.person].filter(Boolean).join(' / ');
    return el('li', { class: 'row' },
      el('div', { class: 'row-main' },
        el('div', {}, el('span', { class: 'badge' }, KIND_LABEL[pl.kind] || pl.kind || 'item'), pl.text),
        el('div', { class: 'meta' }, [where, dateLine(pl, pl.kind)].filter(Boolean).join(' · ')),
        warnings(p)),
      actions(p, true));
  }

  function nodeRow(p) {
    const pl = p.payload;
    return el('li', { class: 'row' },
      el('div', { class: 'row-main' },
        el('div', {}, el('span', { class: 'badge new' }, `new ${pl.type || 'node'}`), pl.name),
        pl.parent ? el('div', { class: 'meta' }, `under ${pl.parent}`) : null,
        warnings(p)),
      actions(p, true));
  }

  function updateRow(p) {
    const pl = p.payload;
    const item = s.items[pl.item_id];
    const label = pl.status === 'done' ? 'Mark done' : pl.status === 'to_read' ? 'Move to "to read"' : `Set ${pl.status}`;
    const bad = (p.warnings || []).some((w) => /unknown item/i.test(w)) || !pl.item_id;
    return el('li', { class: 'row' },
      el('div', { class: 'row-main' },
        el('div', {}, el('span', { class: 'badge' }, 'update'), `${label}: `, item ? item.text : (pl.item_id || '(no item)')),
        warnings(p)),
      actions(p, !bad));
  }

  // -- inline edit --------------------------------------------------------------------------
  function field(label, control, wide) {
    const id = `f-${Math.random().toString(36).slice(2, 8)}`;
    control.id = id;
    return el('div', { class: wide ? 'wide' : null }, el('label', { for: id }, label), control);
  }

  function select(options, current) {
    const sel = el('select', {});
    for (const [value, label] of options) sel.appendChild(el('option', { value, selected: value === current || null }, label));
    return sel;
  }

  function editRow(p) {
    const pl = p.payload;
    const saved = s.draft.edits[p.id] || (s.draft.edits[p.id] = {});
    // Field values are mirrored into the draft as they change, so a redraw restores them.
    const track = (key, ctrl) => {
      if (saved[key] !== undefined) ctrl.value = saved[key];
      const store = () => { saved[key] = ctrl.value; };
      ctrl.addEventListener('input', store);
      ctrl.addEventListener('change', store);
      return ctrl;
    };
    const close = () => { s.draft.editing = null; delete s.draft.edits[p.id]; };
    const cancel = () => { close(); render(); };
    const keys = (ev) => { if (ev.key === 'Escape') { ev.preventDefault(); cancel(); } };
    let controls;
    let collect;
    if (p.ptype === 'item') {
      const text = track('text', el('input', { type: 'text', value: pl.text || '' }));
      const kind = track('kind', select(KINDS.map((k) => [k, KIND_LABEL[k]]), pl.kind));
      const projects = (s.map && s.map.projects) || [];
      const opts = [['', '(none)'], ...projects.map((x) => [x.slug, x.name || x.slug])];
      if (pl.project && !opts.some(([v]) => v === pl.project)) opts.push([pl.project, `${pl.project} (new)`]);
      const project = track('project', select(opts, pl.project || ''));
      // A reminder (or an item with only a remind time) edits remind_at; everything else edits due.
      const key = (pl.kind === 'reminder' || (pl.remind_at && !pl.due)) ? 'remind_at' : 'due';
      const current = pl[key] || '';
      const date = track(key, el('input', { type: current.length > 10 ? 'datetime-local' : 'date', value: current }));
      controls = [field('What', text, true), field('Kind', kind), field('Project', project),
        field(key === 'remind_at' ? 'Remind at' : 'Date', date)];
      collect = () => {
        const patch = {};
        if (text.value !== (pl.text || '')) patch.text = text.value;
        if (kind.value !== pl.kind) patch.kind = kind.value;
        if ((project.value || null) !== (pl.project || null)) patch.project = project.value || null;
        if (date.value !== current) {
          patch[key] = date.value || null;
          if (key === 'due') patch.remind_at = null;
        }
        return patch;
      };
    } else if (p.ptype === 'node') {
      const name = track('name', el('input', { type: 'text', value: pl.name || '' }));
      controls = [field('Name', name, true)];
      collect = () => (name.value !== (pl.name || '') ? { name: name.value } : {});
    } else {
      const status = track('status', select(UPDATE_STATUSES.map((v) => [v, v === 'done' ? 'done' : 'to read']), pl.status));
      controls = [field('New status', status)];
      collect = () => (status.value !== pl.status ? { status: status.value } : {});
    }
    const errBox = el('div', { class: 'error', role: 'alert' });
    const saveBtn = el('button', { class: 'primary', type: 'button', onclick: async () => {
      const patch = collect();
      if (Object.keys(patch).length === 0) { cancel(); return; }
      saveBtn.disabled = true;
      try {
        await api.patch(`/api/proposals/${encodeURIComponent(p.id)}`, patch);
        close();
        s.draft.msg = '';
        await reload();
      } catch (e) {
        errBox.textContent = `Not saved: ${e.message}`;
        saveBtn.disabled = false;
      }
    } }, 'Save');
    return el('li', { class: 'row', onkeydown: keys },
      el('div', { class: 'row-main' },
        el('div', { class: 'edit-grid' }, controls),
        el('div', { class: 'row-actions' }, saveBtn, el('button', { type: 'button', onclick: cancel }, 'Cancel')),
        errBox));
  }

  // -- correction and retry -----------------------------------------------------------------
  function correctionBox() {
    const ta = el('textarea', { id: `fix-${entryId}`, rows: 2, placeholder: 'e.g. the present is for the 12th',
      oninput: () => { s.draft.correction = ta.value; } });
    ta.value = s.draft.correction;
    const err = el('div', { class: 'error', role: 'alert' });
    const btn = el('button', { type: 'button', onclick: async () => {
      const text = ta.value.trim();
      if (!text) return;
      btn.disabled = true;
      err.textContent = '';
      try {
        await api.post(`/api/filings/${encodeURIComponent(entryId)}/correct`, { text });
        s.draft.correction = '';
        ta.value = '';
        s.notice = 'Re-filing with your correction…';
        render();
      } catch (e) {
        err.textContent = e.status === 409 ? 'Still filing, try again in a moment' : `Couldn't send the correction: ${e.message}`;
      } finally {
        btn.disabled = false;
      }
    } }, 'Re-file with this correction');
    return el('div', {}, el('label', { for: ta.id }, "Tell it what's wrong"), ta,
      el('div', { class: 'actions' }, btn), err);
  }

  function retryBox(filing) {
    const err = el('div', { class: 'error', role: 'alert' });
    const btn = el('button', { class: 'primary', type: 'button', onclick: async () => {
      btn.disabled = true;
      try {
        await api.post(`/api/filings/${encodeURIComponent(entryId)}/retry`, {});
        s.notice = 'Retrying…';
        await reload();
      } catch (e) {
        err.textContent = e.status === 409 ? 'Still filing, try again in a moment' : `Couldn't retry: ${e.message}`;
        btn.disabled = false;
      }
    } }, 'Retry');
    return el('div', {},
      el('div', { class: 'error', role: 'alert' }, `Filing failed: ${filing.error || 'unknown error'}`),
      entryText ? el('p', { class: 'meta' }, `Your text is safe in the log: ${entryText}`) : el('p', { class: 'meta' }, 'Your text is safe in the log.'),
      btn, err);
  }

  // -- render -------------------------------------------------------------------------------
  function render() {
    clear(root);
    const title = el('div', { class: 'card-title' }, el('span', {}, 'Filing review'),
      onClose ? el('button', { class: 'icon', type: 'button', 'aria-label': 'Close card', title: 'Close', onclick: onClose }, '×') : null);
    root.appendChild(el('header', {}, title));
    if (s.loadError) { root.appendChild(el('div', { class: 'error', role: 'alert' }, `Couldn't load this card: ${s.loadError}`)); return; }
    if (!s.data) { root.appendChild(el('p', { class: 'status' }, 'Loading…')); return; }
    const filing = s.data.filing;
    if (!filing) { root.appendChild(el('p', { class: 'meta' }, 'Not filed yet.')); return; }
    if (s.notice) root.appendChild(el('p', { class: 'status', role: 'status' }, s.notice));
    if (filing.status === 'failed') { root.appendChild(retryBox(filing)); return; }
    if (filing.status === 'queued' || filing.status === 'running') {
      root.appendChild(el('p', { class: 'status', role: 'status' }, 'Filing…'));
      return;
    }
    root.appendChild(el('div', {},
      el('div', { class: 'meta' }, 'Filed to'),
      el('ul', { class: 'files' }, (filing.files || []).map((f) => el('li', {}, f))),
      filing.synopsis ? el('p', {}, filing.synopsis) : null));
    const list = pending();
    if (list.length === 0) {
      root.appendChild(el('p', { class: 'empty' }, 'Nothing left to review.'));
    } else {
      root.appendChild(el('ul', { class: 'rows' }, orderProposals(list).map((p) => {
        if (s.draft.editing === p.id) return editRow(p);
        return p.ptype === 'node' ? nodeRow(p) : p.ptype === 'update' ? updateRow(p) : itemRow(p);
      })));
    }
    if (s.draft.msg) root.appendChild(el('div', { class: 'error', role: 'alert' }, s.draft.msg));
    const foot = el('div', { class: 'card-foot' });
    if (list.length > 0) {
      const ok = list.filter((p) => !(p.ptype === 'update' && ((p.warnings || []).some((w) => /unknown item/i.test(w)) || !p.payload.item_id)));
      foot.appendChild(el('div', { class: 'actions' },
        el('button', { class: 'primary', type: 'button', disabled: ok.length === 0 || null,
          onclick: () => confirmIds(ok.map((p) => p.id)) }, 'Confirm all')));
    }
    foot.appendChild(correctionBox());
    root.appendChild(foot);
  }

  render();
  reload();

  root.reload = reload;
  root.destroy = () => { destroyed = true; for (const u of unsubs) u(); };
  return root;
}
