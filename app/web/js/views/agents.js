// Agents: Claude Code sessions seen through the hooks.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { STATUS_LABEL, sinceText, nextTimeText } from './agents-model.js';

const WHERE = ['terminal', 'IDE', 'desktop app', 'unknown'];

export async function render(container) {
  let dead = false, loading = false, again = false, dirty = false, againForce = false;
  const root = el('div', {});
  container.appendChild(root);

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
      if (!dead) clear(root).appendChild(el('p', { class: 'error', role: 'alert' }, `Couldn't load agents: ${e.message}`));
    } finally {
      loading = false;
      if (again) { const f = againForce; again = againForce = false; refresh(f); }
    }
  }

  root.addEventListener('focusout', (ev) => {
    if (ev.relatedTarget && root.contains(ev.relatedTarget)) return;
    setTimeout(() => { if (dirty && !typing()) { dirty = false; refresh(); } }, 0);
  });

  const msg = el('div', { role: 'alert' });
  const fail = (e) => { clear(msg).appendChild(el('div', { class: 'error' }, e.message)); };

  async function patch(row, body) {
    clear(msg);
    try { await api.patch(`/api/agents/${encodeURIComponent(row.session_id)}`, body); } catch (e) { fail(e); }
    dirty = false;
    refresh(true);
  }

  function partCell(row) {
    const btn = el('button', { type: 'button', class: 'link', title: 'Click to rename', onclick: () => {
      let done = false;
      const input = el('input', { type: 'text', value: row.part_override || '', 'aria-label': 'Part name',
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

  function whereCell(row) {
    const auto = el('option', { value: '', selected: !row.where_override }, `auto (${row.where})`);
    const select = el('select', { 'aria-label': 'Where', title: row.entrypoint || 'unknown entrypoint',
      onchange: () => patch(row, { where: select.value || null }) },
      auto, WHERE.map((w) => el('option', { value: w, selected: row.where_override === w }, w)));
    return select;
  }

  function draw(rows) {
    clear(root);
    root.appendChild(el('h1', {}, 'Agents'));
    root.appendChild(msg);
    if (!rows.length) {
      root.appendChild(el('p', { class: 'muted' },
        'No Claude Code sessions seen. Install the integration in Settings, then start a session.'));
      return;
    }
    const now = new Date();
    const head = el('tr', {}, ['Project', 'Part', 'Where', 'Status', 'Since', 'Note', 'Next time', ''].map((h) => el('th', {}, h)));
    const body = rows.map((row) => {
      const nt = nextTimeText(row);
      return el('tr', {},
        el('td', {}, row.project_name, row.mapped ? el('span', { class: 'badge' }, 'mapped') : null),
        el('td', {}, partCell(row)),
        el('td', {}, whereCell(row)),
        el('td', {}, el('span', { class: row.status === 'waiting' ? 'badge warn' : 'badge' }, STATUS_LABEL[row.status] || row.status)),
        el('td', {}, sinceText(row.status_since, now)),
        el('td', { class: 'meta' }, row.note || ''),
        el('td', { class: 'meta' }, nt || ''),
        el('td', {}, el('button', { type: 'button', 'aria-label': `Remove ${row.part}`, title: 'Remove from the list', onclick: async () => {
          clear(msg);
          try { await api.del(`/api/agents/${encodeURIComponent(row.session_id)}`); } catch (e) { fail(e); }
          refresh();
        } }, '✕')));
    });
    root.appendChild(el('table', { class: 'agents-table' }, el('thead', {}, head), el('tbody', {}, body)));
  }

  await refresh();
  const subs = [api.onEvent('agents.changed', refresh), api.onEvent('ws.open', refresh)];
  const timer = setInterval(refresh, 30000);
  return () => { dead = true; for (const off of subs) off(); clearInterval(timer); };
}
