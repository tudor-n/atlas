// Confirm card (spec 5.3; phase 4.5 design §4): what was filed, one row per proposal, confirm / edit /
// dismiss, "Confirm all", "Tell it what's wrong", and Retry for failed filings.
// Layout: a header row (date, synopsis, status readout; HIDE and × in the tray), FILED TO, proposal
// rows (a 104px kind column, the text and a mono meta line, amber warnings, ✓ EDIT ✕), and an action
// bar (CONFIRM ALL, TELL IT WHAT'S WRONG, with the correction folded behind it). In the tray the card
// rests as one compact 52px row; REVIEW opens the full card. Styles: css/views/today.css (.cc).
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { twice } from '../press.js';
import { button, chip, dot, label } from './ui.js';
import { filingStatus, filingReadout } from './filing-status.js';
import { entryStamp, firstWords, shortWhen } from '../views/today-model.js';

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
          const fits = n.payload.type === field || (field === 'project' && n.payload.type === 'course');
          if (!placed.has(n.id) && fits && slugify(n.payload.name || '') === slug) {
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

const badUpdate = (p) => p.ptype === 'update' && ((p.warnings || []).some((w) => /unknown item/i.test(w)) || !p.payload.item_id);

// Per-entry UI drafts (typed correction, open edit and its field values, last message, whether the
// correction box and the tray card are open). They live outside the card so a re-render, reload or
// recreated card never loses what the owner typed.
const drafts = new Map();
function draftFor(entryId) {
  if (!drafts.has(entryId)) {
    drafts.set(entryId, { correction: '', editing: null, edits: {}, msg: '', fixOpen: false, expanded: false });
  }
  return drafts.get(entryId);
}

// `blocked()` returns why writing can't work right now (a read-only root), or ''.
// `onClose` (the tray's ×) also makes the card compact unless `compact: false` is passed.
export function confirmCard(entryId, { entryText, onChange, onClose, blocked = () => '', compact = !!onClose } = {}) {
  const root = el('section', { class: compact ? 'cc tray' : 'cc', 'aria-label': 'Filing review' });
  const s = {
    data: null, map: null, items: {}, loadError: '', notice: '', busy: false,
    progress: null,  // while filing: {started, step}, from the card's load and filing.progress
    draft: draftFor(entryId),
    focus: null,     // data-k of the control to focus after the next draw
  };
  // A draft from before (a reload, a recreated card) opens the correction box again.
  if (s.draft.correction) s.draft.fixOpen = true;
  const unsubs = [];
  let destroyed = false;

  const mine = (ev) => ev.data && ev.data.entry_id === entryId;
  unsubs.push(api.onEvent('filing.done', (ev) => { if (mine(ev)) { s.notice = ''; reload(); } }));
  unsubs.push(api.onEvent('filing.failed', (ev) => { if (mine(ev)) { s.notice = ''; reload(); } }));
  unsubs.push(api.onEvent('filing.started', (ev) => { if (mine(ev)) reload(); }));
  unsubs.push(api.onEvent('filing.progress', (ev) => {
    if (!mine(ev)) return;
    s.progress = { ...(s.progress || {}), step: ev.data.step };
    tick();
  }));
  unsubs.push(api.onEvent('ws.open', () => reload()));

  // Events can trigger reloads milliseconds apart (filing.started, then a fast filing.failed): only
  // the latest request may draw, or a stale "running" answer would leave the card counting forever.
  let loads = 0;
  async function reload() {
    if (destroyed) return;
    const mineLoad = ++loads;
    try {
      const [data, map] = await Promise.all([api.get(`/api/filings/${encodeURIComponent(entryId)}`), loadMap().catch(() => null)]);
      if (mineLoad !== loads) return;
      s.data = data;
      s.map = map;
      s.progress = data.progress || null;
      s.loadError = '';
      if (data.proposals.some((p) => p.status === 'proposed' && p.ptype === 'update')) {
        try {
          const items = await api.get('/api/items');
          s.items = Object.fromEntries(items.map((i) => [i.id, i]));
        } catch (_) { /* the row falls back to the item id */ }
      }
    } catch (e) {
      if (mineLoad !== loads) return;
      s.loadError = e.message;
    }
    if (!destroyed && mineLoad === loads) render();
  }

  const pending = () => (s.data ? s.data.proposals.filter((p) => p.status === 'proposed') : []);
  const filingState = () => (s.data && s.data.filing ? s.data.filing.status : '');
  const refiling = () => ['queued', 'running'].includes(filingState());

  // One action at a time: the row and card buttons stay disabled until the server answers.
  async function act(fn, errPrefix) {
    if (s.busy) return;
    s.draft.msg = '';
    s.busy = true;
    render();
    try {
      await fn();
    } catch (e) {
      s.draft.msg = `${errPrefix}: ${e.message}`;
    } finally {
      s.busy = false;
    }
    await reload();
    if (onChange) onChange();
  }

  const confirmIds = (ids) => act(() => api.post('/api/proposals/confirm', { ids }), "Couldn't confirm");
  const dismissIds = (ids) => act(() => api.post('/api/proposals/dismiss', { ids }), "Couldn't dismiss");

  // A button with a key, so the same control gets the focus back after a redraw.
  const keyed = (node, key, aria) => {
    node.dataset.k = key;
    if (aria) node.setAttribute('aria-label', aria);
    return node;
  };

  // -- header and the compact tray row -------------------------------------------------------
  function synopsisText() {
    const f = s.data && s.data.filing;
    return (f && f.synopsis) || firstWords(entryText) || `Entry ${entryStamp(entryId) || entryId}`;
  }

  const live = { head: null, line: null, mini: null };  // the parts tick() updates while filing

  function readout() {
    const f = s.data && s.data.filing;
    return filingReadout(f ? f.status : '', s.progress, Date.now(), pending().length);
  }

  function statusNode() {
    const r = readout();
    if (r.state === 'failed') return chip('Failed', { title: (s.data.filing.error || '').slice(0, 300) || null });
    if (r.state === 'filed') return el('span', { class: 'dl cc-st' }, dot('g'), 'FILED');
    if (r.state === 'queued') return el('span', { class: 'dl cc-st' }, dot('ring'), r.text);
    if (r.state === 'filing') {
      live.head = el('span', {}, r.text);
      return el('span', { class: 'dl cc-st', 'aria-hidden': 'true' }, dot(''), live.head);
    }
    return null;
  }

  function closeButton() {
    return keyed(button('×', { size: 40, cls: 'x', title: 'Close', onclick: onClose }), 'close', 'Close card');
  }

  function header() {
    const stamp = entryStamp(entryId);
    return el('div', { class: 'cc-h' },
      stamp ? el('span', { class: 'm dt' }, stamp) : null,
      el('span', { class: 'syn' }, synopsisText()),
      statusNode(),
      compact ? keyed(button('Hide', { size: 40, onclick: () => { s.draft.expanded = false; s.focus = 'review'; render(); } }), 'hide') : null,
      compact && onClose ? closeButton() : null);
  }

  function compactRow() {
    const r = readout();
    const mark = r.state === 'failed' ? el('span', { class: 'sq', title: 'Filing failed' })
      : r.state === 'filed' ? dot(pending().length ? 'a' : 'g')
        : r.state === 'queued' ? dot('ring') : dot('');
    let progress = r.text;
    if (r.state === 'filing') {
      const st = filingStatus('running', s.progress, Date.now());
      progress = st.elapsed ? `${st.text} · ${st.elapsed}` : st.text;
    }
    if (!s.data && !s.loadError) progress = 'Loading…';
    if (s.loadError) progress = "Couldn't load";
    live.mini = el('span', { class: r.state === 'failed' || s.loadError ? 'm pg amb' : 'm pg' }, progress);
    return el('div', { class: 'cc-min' },
      mark,
      el('span', { class: 'syn' }, synopsisText()),
      live.mini,
      keyed(button('Review', { size: 40, onclick: () => { s.draft.expanded = true; s.focus = 'hide'; render(); } }), 'review'),
      onClose ? closeButton() : null);
  }

  // -- rows ---------------------------------------------------------------------------------
  function dateLine(pl, kind) {
    const bits = [];
    if (pl.due) bits.push(`due ${shortWhen(pl.due)}`);
    if (pl.remind_at) bits.push(`remind ${shortWhen(pl.remind_at)}`);
    if (kind === 'next_time') bits.push('next session');
    if (kind === 'waiting') bits.push(`waiting since ${entryDate(entryId) || 'now'}`);
    return bits.join(' · ');
  }

  function warnings(p) {
    return (p.warnings || []).map((w) => el('div', { class: 'warn', role: 'note' }, el('span', { class: 'sq' }), w));
  }

  function actions(p, canConfirm) {
    if (refiling()) canConfirm = false;
    const reason = blocked();
    const ok = keyed(button('✓', { size: 40, title: reason || 'Confirm', disabled: !canConfirm || !!reason || s.busy,
      onclick: () => { s.focus = 'first'; confirmIds([p.id]); } }), `ok:${p.id}`, 'Confirm');
    const edit = keyed(button('Edit', { size: 40, title: reason || null, disabled: !!reason || s.busy,
      onclick: () => { s.draft.editing = p.id; s.focus = `edit:${p.id}`; render(); } }), `ed:${p.id}`);
    const no = keyed(button('✕', { size: 40, cls: 'no', title: reason || 'Dismiss (press twice)', disabled: !!reason || s.busy }),
      `no:${p.id}`, 'Dismiss');
    twice(no, { onGo: () => { s.focus = 'first'; dismissIds([p.id]); } });
    return el('div', { class: 'acts' }, ok, edit, no);
  }

  function propRow(kind, main, meta, p, canConfirm) {
    return el('li', { class: 'pp' },
      el('span', { class: 'k lb' }, kind),
      el('div', { class: 'grow' },
        el('div', { class: 't' }, main),
        meta ? el('div', { class: 'mt' }, meta) : null,
        warnings(p)),
      actions(p, canConfirm));
  }

  // A slug's name from the map (a new node proposed on this card keeps its slug).
  function nameOf(slug) {
    if (!slug) return '';
    const m = s.map || {};
    for (const k of ['projects', 'courses', 'areas', 'people']) {
      const hit = (m[k] || []).find((x) => x.slug === slug);
      if (hit) return hit.name || slug;
    }
    return slug;
  }

  function itemRow(p) {
    const pl = p.payload;
    const where = [pl.project ? nameOf(pl.project) : nameOf(pl.area), nameOf(pl.person)].filter(Boolean).join(' / ');
    return propRow(KIND_LABEL[pl.kind] || pl.kind || 'item', pl.text,
      [where, dateLine(pl, pl.kind)].filter(Boolean).join(' · '), p, true);
  }

  function nodeRow(p) {
    const pl = p.payload;
    return propRow(`new ${pl.type || 'node'}`, pl.name, pl.parent ? `under ${pl.parent}` : '', p, true);
  }

  function updateRow(p) {
    const pl = p.payload;
    const item = s.items[pl.item_id];
    const what = pl.status === 'done' ? 'Mark done' : pl.status === 'to_read' ? 'Move to "to read"' : `Set ${pl.status}`;
    return propRow('update', `${what}: ${item ? item.text : (pl.item_id || '(no item)')}`,
      pl.note || '', p, !badUpdate(p));
  }

  // -- inline edit --------------------------------------------------------------------------
  function field(text, control, wide) {
    const id = `f-${Math.random().toString(36).slice(2, 8)}`;
    control.id = id;
    control.classList.add('h40');
    return el('div', { class: wide ? 'fld wide' : 'fld' }, el('label', { class: 'lb', for: id }, text), control);
  }

  function select(options, current) {
    const sel = el('select', {});
    for (const [value, text] of options) sel.appendChild(el('option', { value, selected: value === current || null }, text));
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
    const cancel = () => { close(); s.focus = `ed:${p.id}`; render(); };
    const keys = (ev) => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); cancel(); } };
    let controls;
    let collect;
    let kindText = 'update';
    if (p.ptype === 'item') {
      kindText = KIND_LABEL[pl.kind] || pl.kind || 'item';
      const text = track('text', el('input', { type: 'text', value: pl.text || '' }));
      const kind = track('kind', select(KINDS.map((k) => [k, KIND_LABEL[k]]), pl.kind));
      const map = s.map || {};
      // A value only proposed on this card (a new node) stays selectable, marked "(new)".
      const withCurrent = (opts, current) => (current && !opts.some(([v]) => v === current)
        ? [...opts, [current, `${current} (new)`]] : opts);
      const projectOpts = withCurrent([['', '(none)'],
        ...(map.projects || []).map((x) => [x.slug, x.name || x.slug]),
        ...(map.courses || []).map((x) => [x.slug, `${x.name || x.slug} (course)`])], pl.project);
      const project = track('project', select(projectOpts, pl.project || ''));
      const areaOpts = withCurrent((map.areas || []).map((x) => [x.slug, x.name || x.slug]), pl.area);
      const area = track('area', select(areaOpts, pl.area || ''));
      const personOpts = withCurrent([['', '(none)'],
        ...(map.people || []).map((x) => [x.slug, x.name || x.slug])], pl.person);
      const person = track('person', select(personOpts, pl.person || ''));
      // A reminder (or an item with only a remind time) edits remind_at; everything else edits due.
      const key = (pl.kind === 'reminder' || (pl.remind_at && !pl.due)) ? 'remind_at' : 'due';
      const current = pl[key] || '';
      const date = track(key, el('input', { type: current.length > 10 ? 'datetime-local' : 'date', value: current }));
      controls = [field('What', text, true), field('Kind', kind), field('Area', area), field('Project', project),
        field('Person', person), field(key === 'remind_at' ? 'Remind at' : 'Date', date)];
      collect = () => {
        const patch = {};
        if (text.value !== (pl.text || '')) patch.text = text.value;
        if (kind.value !== pl.kind) patch.kind = kind.value;
        if (area.value && area.value !== pl.area) patch.area = area.value;
        if ((project.value || null) !== (pl.project || null)) patch.project = project.value || null;
        if ((person.value || null) !== (pl.person || null)) patch.person = person.value || null;
        if (date.value !== current) {
          patch[key] = date.value || null;
          if (key === 'due') patch.remind_at = null;
        }
        return patch;
      };
    } else if (p.ptype === 'node') {
      kindText = `new ${pl.type || 'node'}`;
      const name = track('name', el('input', { type: 'text', value: pl.name || '' }));
      controls = [field('Name', name, true)];
      collect = () => (name.value !== (pl.name || '') ? { name: name.value } : {});
    } else {
      const status = track('status', select(UPDATE_STATUSES.map((v) => [v, v === 'done' ? 'done' : 'to read']), pl.status));
      controls = [field('New status', status)];
      collect = () => (status.value !== pl.status ? { status: status.value } : {});
    }
    controls[0].querySelector('input, select').dataset.k = `edit:${p.id}`;
    const errBox = el('div', { class: 'err cc-err', role: 'alert' });
    const reason = blocked();
    const saveBtn = button('Save', { primary: true, size: 40, title: reason || null, disabled: !!reason, onclick: async () => {
      const patch = collect();
      if (Object.keys(patch).length === 0) { cancel(); return; }
      saveBtn.disabled = true;
      try {
        await api.patch(`/api/proposals/${encodeURIComponent(p.id)}`, patch);
        close();
        s.draft.msg = '';
        s.focus = `ed:${p.id}`;
        await reload();
      } catch (e) {
        errBox.textContent = `Not saved: ${e.message}`;
        saveBtn.disabled = false;
      }
    } });
    // Enter in a one-line field saves, as a form would.
    const enter = (ev) => {
      if (ev.key === 'Enter' && ev.target.tagName === 'INPUT' && !ev.isComposing) { ev.preventDefault(); saveBtn.click(); }
    };
    return el('li', { class: 'pp ed', onkeydown: (ev) => { keys(ev); enter(ev); } },
      el('span', { class: 'k lb' }, kindText),
      el('div', { class: 'grow' },
        el('div', { class: 'eg' }, controls),
        el('div', { class: 'eb' }, saveBtn, button('Cancel', { size: 40, onclick: cancel })),
        errBox));
  }

  // -- footer: confirm all, and the correction folded behind TELL IT WHAT'S WRONG ------------
  function footer(list) {
    const reason = blocked();
    const bar = el('div', { class: 'actbar cc-bar' });
    if (list.length > 0) {
      const ok = list.filter((p) => !badUpdate(p));
      bar.appendChild(keyed(button(s.busy ? 'Working…' : 'Confirm all', { primary: true, title: reason || null,
        disabled: ok.length === 0 || !!reason || s.busy || refiling(),
        onclick: () => { s.focus = 'first'; confirmIds(ok.map((p) => p.id)); } }), 'all'));
    }
    const open = s.draft.fixOpen;
    // Stays an outline while open (a white fill beside CONFIRM ALL would read as a second primary).
    const tell = keyed(button("Tell it what's wrong", { onclick: () => {
      s.draft.fixOpen = !s.draft.fixOpen;
      s.focus = s.draft.fixOpen ? 'fix' : 'tell';
      render();
    } }), 'tell');
    tell.setAttribute('aria-expanded', open ? 'true' : 'false');
    tell.setAttribute('aria-controls', `fixbox-${entryId}`);
    bar.append(tell, el('span', { class: 'grow' }));
    return [bar, open ? correctionBox(reason) : null];
  }

  function correctionBox(reason) {
    const ta = el('textarea', { id: `fix-${entryId}`, rows: 3, placeholder: 'e.g. the present is for the 12th',
      oninput: () => { s.draft.correction = ta.value; } });
    ta.dataset.k = 'fix';
    ta.value = s.draft.correction;
    const err = el('div', { class: 'err cc-err', role: 'alert' });
    const btn = button('Re-file', { title: reason || 'Re-file with this correction', disabled: !!reason, onclick: async () => {
      const text = ta.value.trim();
      if (!text) { ta.focus(); return; }
      btn.disabled = true;
      err.textContent = '';
      try {
        await api.post(`/api/filings/${encodeURIComponent(entryId)}/correct`, { text });
        s.draft.correction = '';
        s.draft.fixOpen = false;
        ta.value = '';
        s.notice = 'Re-filing with your correction…';
        await reload();  // now queued: the old rows (and their ✓) are gone until the new card
      } catch (e) {
        err.textContent = e.status === 409 ? 'Still filing, try again in a moment' : `Couldn't send the correction: ${e.message}`;
      } finally {
        btn.disabled = !!reason;
      }
    } });
    btn.setAttribute('aria-label', 'Re-file with this correction');
    const keys = (ev) => {
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); s.draft.fixOpen = false; s.focus = 'tell'; render(); }
      if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); btn.click(); }
    };
    return el('div', { class: 'cc-fix', id: `fixbox-${entryId}`, onkeydown: keys },
      el('label', { class: 'lb', for: ta.id }, "What's wrong"),
      el('div', { class: 'fx' }, ta, btn),
      err);
  }

  function failBox(filing) {
    const reason = blocked();
    const err = el('div', { class: 'err cc-err', role: 'alert' });
    const btn = keyed(button('Retry', { primary: true, title: reason || null, disabled: !!reason, onclick: async () => {
      btn.disabled = true;
      try {
        await api.post(`/api/filings/${encodeURIComponent(entryId)}/retry`, {});
        s.notice = 'Retrying…';
        await reload();
      } catch (e) {
        err.textContent = e.status === 409 ? 'Still filing, try again in a moment' : `Couldn't retry: ${e.message}`;
        btn.disabled = false;
      }
    } }), 'retry');
    return [
      el('div', { class: 'cc-fail' },
        el('p', { class: 'err', role: 'alert' }, `Filing failed: ${filing.error || 'unknown error'}`),
        el('p', { class: 'safe' }, 'Your text is safe in the log.'),
        entryText ? el('p', { class: 'read q' }, entryText) : null),
      el('div', { class: 'actbar cc-bar' }, btn, el('span', { class: 'grow' }), err),
    ];
  }

  // -- filing status (ticks once a second while filing) --------------------------------------
  let ticker = null;
  function tick() {
    if (!s.data || !s.data.filing) return;
    const status = s.data.filing.status;
    if (live.line) {
      const { text } = filingStatus(status, s.progress, Date.now());
      if (live.line.textContent !== text) live.line.textContent = text;
    }
    if (live.head) live.head.textContent = filingReadout(status, s.progress, Date.now()).text;
    if (live.mini && status === 'running') {
      const st = filingStatus(status, s.progress, Date.now());
      live.mini.textContent = st.elapsed ? `${st.text} · ${st.elapsed}` : st.text;
    }
  }
  function syncTicker() {
    const on = refiling() && !destroyed;
    if (on && !ticker) ticker = setInterval(tick, 1000);
    if (!on && ticker) { clearInterval(ticker); ticker = null; }
  }

  // -- render -------------------------------------------------------------------------------
  function render() {
    // Keep the keyboard where it was: the same control (by data-k) after the redraw.
    const a = document.activeElement;
    const had = a && root.contains(a) ? (a.dataset && a.dataset.k) || 'first' : null;
    const want = s.focus || had;
    const scroll = !!s.focus;  // the owner's own move may scroll; a background redraw never does
    s.focus = null;
    live.head = null; live.line = null; live.mini = null;
    draw();
    syncTicker();
    if (!want) return;
    // Never take the keyboard from elsewhere (the capture bar, another card): only when it was here
    // or has nowhere to be. While busy every row button is disabled: try again at the next draw.
    const b = document.activeElement;
    const free = !b || b === document.body || root.contains(b);
    if (!free) return;
    if (s.busy || !refocus(want, scroll)) s.focus = want;
  }

  function refocus(key, scroll) {
    let n = key === 'first' ? null : root.querySelector(`[data-k="${CSS.escape(key)}"]`);
    if (!n || n.disabled) n = root.querySelector('.pp .b:not(:disabled), [data-k="all"]:not(:disabled), [data-k="tell"], [data-k="retry"], [data-k="review"]');
    if (!n) return false;
    n.focus({ preventScroll: !scroll });
    if (n.tagName === 'TEXTAREA') n.setSelectionRange(n.value.length, n.value.length);
    return document.activeElement === n;
  }

  function draw() {
    clear(root);
    root.classList.toggle('min', compact && !s.draft.expanded);
    if (compact && !s.draft.expanded) { root.appendChild(compactRow()); return; }
    root.appendChild(header());
    if (s.loadError) { root.appendChild(el('div', { class: 'err cc-line', role: 'alert' }, `Couldn't load this card: ${s.loadError}`)); return; }
    if (!s.data) { root.appendChild(el('div', { class: 'cc-line f' }, 'Loading…')); return; }
    const filing = s.data.filing;
    if (!filing) { root.appendChild(el('div', { class: 'cc-line f' }, 'Not filed yet.')); return; }
    // "Retrying…" or "Re-filing with your correction…" until the agent runs; then its steps say it.
    if (s.notice && filing.status !== 'running') root.appendChild(el('div', { class: 'cc-line m d', role: 'status' }, s.notice));
    if (filing.status === 'failed') { root.append(...failBox(filing)); return; }
    if (filing.status === 'queued' || filing.status === 'running') {
      // Only the step is announced; the seconds tick in the header, outside the live region.
      live.line = el('span', { role: 'status' });
      root.appendChild(el('div', { class: 'cc-line m d' }, live.line));
      tick();
      return;
    }
    const files = filing.files || [];
    if (files.length) {
      root.appendChild(el('div', { class: 'cc-files' }, label('Filed to'),
        el('ul', {}, files.map((f) => el('li', {}, f)))));
    }
    const list = pending();
    if (list.length === 0) {
      root.appendChild(el('div', { class: 'cc-line cc-done' }, dot('g'), 'Nothing left to review.'));
    } else {
      root.appendChild(el('ul', { class: 'pps', 'aria-label': 'Proposals' }, orderProposals(list).map((p) => {
        if (s.draft.editing === p.id) return editRow(p);
        return p.ptype === 'node' ? nodeRow(p) : p.ptype === 'update' ? updateRow(p) : itemRow(p);
      })));
    }
    if (s.draft.msg) root.appendChild(el('div', { class: 'err cc-line', role: 'alert' }, s.draft.msg));
    root.append(...footer(list).filter(Boolean));
  }

  render();
  reload();

  root.reload = reload;
  root.refiling = refiling;  // true while this entry is queued or being filed
  root.destroy = () => {
    destroyed = true;
    for (const u of unsubs) u();
    live.head = null; live.line = null; live.mini = null;
    syncTicker();
  };
  return root;
}
