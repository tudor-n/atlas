// Agents: Claude Code sessions seen through the hooks, with the Usage panel on the right (phase 4.5
// design §4). `#/usage` renders this view with the usage panel focused.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { dot, selectBox } from '../components/ui.js';
import { twice } from '../press.js';
import { mountUsage } from './usage.js';
import { statusText, sinceText, nextTimeText, linkText, osText, statusDot, whereText } from './agents-model.js';

const WHERE = ['terminal', 'IDE', 'desktop app', 'unknown'];
const HEAD = ['Status', 'Project', 'Part', 'Where', 'Since', 'Note', 'Next time', ''];

// opts.focus === 'usage' (the `#/usage` route): scroll to and highlight the usage panel.
export async function render(container, _params, _query, opts = {}) {
  let dead = false, loading = false, again = false, dirty = false, againForce = false;
  const head = el('div', { class: 'sect-h' }, el('h1', { class: 'lb' }, 'Agents'), el('span', { class: 'n' }));
  const msg = el('div', { class: 'amsg', role: 'alert' });
  const root = el('div', { class: 'abody' });
  const main = el('div', { class: 'main' }, head, msg, root);
  const side = el('aside', { class: 'side usage', id: 'usage', tabindex: '-1', 'aria-label': 'Usage' });
  container.appendChild(el('div', { class: 'vw split agents' }, main, side));
  const stopUsage = mountUsage(side);

  function typing() {
    const a = document.activeElement;
    return a && root.contains(a) && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT' || a.tagName === 'SELECT');
  }

  async function refresh(force) {
    force = force === true;
    if (dead) return;
    if (loading) { again = true; againForce = againForce || force; return; }
    if (!force && typing()) { dirty = true; return; }
    loading = true;
    try {
      const rows = await api.get('/api/agents');
      if (!dead) draw(rows);
    } catch (e) {
      if (!dead) clear(root).appendChild(el('p', { class: 'err aerr', role: 'alert' }, `Couldn't load agents: ${e.message}`));
    } finally {
      loading = false;
      if (again) { const f = againForce; again = againForce = false; refresh(f); }
    }
  }

  root.addEventListener('focusout', (ev) => {
    if (ev.relatedTarget && root.contains(ev.relatedTarget)) return;
    setTimeout(() => { if (dirty && !typing()) { dirty = false; refresh(); } }, 0);
  });

  const fail = (e) => { clear(msg).appendChild(el('p', { class: 'err' }, e.message)); };

  async function patch(row, body) {
    clear(msg);
    try { await api.patch(`/api/agents/${encodeURIComponent(row.session_id)}`, body); } catch (e) { fail(e); }
    dirty = false;
    refresh(true);
  }

  function partCell(row) {
    const btn = el('button', { type: 'button', class: 'part', title: 'Click to rename', onclick: () => {
      let done = false;
      const input = el('input', { type: 'text', class: 'h40', value: row.part_override || '', 'aria-label': 'Part name',
        placeholder: row.part, spellcheck: 'false' });
      const finish = (save) => {
        if (done) return;
        done = true;
        if (save) patch(row, { part: input.value.trim() || null });
        else { dirty = false; refresh(true); }
      };
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
        else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
      });
      input.addEventListener('blur', () => finish(true));
      btn.replaceWith(input);
      input.focus();
    } }, row.part);
    return btn;
  }

  // "link to <name>": this row's folder joins that project's folders on this PC.
  function linkButton(row) {
    const text = linkText(row);
    if (!text) return null;
    return el('button', { type: 'button', class: 'b h40 lk', title: `Add ${row.suggest.folder} to ${row.suggest.name} on this PC`,
      onclick: async () => {
        clear(msg);
        try {
          await api.post(`/api/agents/${encodeURIComponent(row.session_id)}/link`, { slug: row.suggest.slug });
        } catch (e) { fail(e); }
        refresh(true);
      } }, text);
  }

  // WHERE: the value in mono (an override, or `auto (<detected>)`); pressing it puts the select in
  // its place and opens it. A choice saves; Esc or leaving it puts the value back.
  function whereCell(row) {
    const btn = el('button', { type: 'button', class: 'wh', title: `${row.entrypoint || 'unknown entrypoint'} · click to change`,
      'aria-label': `Where: ${whereText(row)}. Change`, onclick: () => {
        let done = false;
        const auto = el('option', { value: '', selected: !row.where_override }, `auto (${row.where})`);
        const select = el('select', { class: 'h40', 'aria-label': 'Where', title: row.entrypoint || 'unknown entrypoint' },
          auto, WHERE.map((w) => el('option', { value: w, selected: row.where_override === w }, w)));
        const box = selectBox(select);
        const finish = (save) => {
          if (done) return;
          done = true;
          if (save && (select.value || null) !== (row.where_override || null)) patch(row, { where: select.value || null });
          else { dirty = false; refresh(true); }
        };
        select.addEventListener('change', () => finish(true));
        select.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { ev.preventDefault(); finish(false); } });
        select.addEventListener('blur', () => finish(false));
        btn.replaceWith(box);
        select.focus();
        try { if (typeof select.showPicker === 'function') select.showPicker(); } catch (_) { /* needs a user gesture; the focus is enough */ }
      } },
    row.where_override ? row.where_override : [el('span', { class: 'f' }, 'auto '), row.where || 'unknown']);
    return btn;
  }

  function removeButton(row) {
    const btn = el('button', { type: 'button', class: 'b h40 x tx', 'aria-label': `Remove ${row.part}`, title: 'Remove from the list' }, '✕');
    twice(btn, { onGo: async () => {
      clear(msg);
      try { await api.del(`/api/agents/${encodeURIComponent(row.session_id)}`); } catch (e) { fail(e); }
      refresh();
    } });
    return btn;
  }

  function draw(rows) {
    clear(root);
    const live = rows.filter((r) => !r.ended).length;
    head.lastChild.textContent = rows.length ? (live === rows.length ? String(live) : `${live} / ${rows.length}`) : '';
    head.lastChild.title = rows.length ? `${live} open, ${rows.length - live} ended` : '';
    if (!rows.length) {
      root.appendChild(el('div', { class: 'empty' },
        'No Claude Code sessions seen. Install the integration in Settings, then start a session.'));
      return;
    }
    const now = new Date();
    const thead = el('tr', {}, HEAD.map((h) => el('th', { scope: 'col', class: h ? null : 'c-x' }, h)));
    const body = rows.map((row) => {
      const nt = nextTimeText(row);
      const kind = statusDot(row);
      return el('tr', { class: row.ended ? 'ended' : null, title: osText(row) },
        el('td', { class: 'c-st' }, el('span', { class: kind === 'a' ? 'dl amb' : 'dl' }, dot(kind), statusText(row))),
        el('td', { class: 'c-pr' }, el('div', { class: 'pr-n' }, el('span', {}, row.project_name),
          row.mapped ? el('span', { class: 'mp', title: 'This folder is in the brain map' }, 'mapped') : null),
          linkButton(row)),
        el('td', { class: 'c-pt' }, partCell(row)),
        el('td', { class: 'c-wh' }, whereCell(row)),
        el('td', { class: 'c-sn m' }, sinceText(row.status_since, now)),
        el('td', { class: 'c-nt' }, row.note || ''),
        el('td', { class: 'c-nx' }, nt ? el('span', { class: 'm' }, String(Number(row.next_time))) : null,
          nt ? el('span', { class: 'nx', title: nt }, Number(row.next_time) === 1 ? ' note' : ' notes') : null),
        el('td', { class: 'c-x' }, removeButton(row)));
    });
    root.appendChild(el('div', { class: 'tw' },
      el('table', { class: 'atab' }, el('thead', {}, thead), el('tbody', {}, body))));
  }

  await refresh();
  if (opts.focus === 'usage') {
    side.classList.add('hl');
    if (typeof side.scrollIntoView === 'function') side.scrollIntoView({ block: 'start' });
    try { side.focus({ preventScroll: true }); } catch (_) { /* older engines */ }
  }
  const subs = [api.onEvent('agents.changed', refresh), api.onEvent('ws.open', refresh)];
  const timer = setInterval(refresh, 30000);
  return () => { dead = true; stopUsage(); for (const off of subs) off(); clearInterval(timer); };
}
