// Tablet: everything typed on the workbench tablet, newest first (phase 3 design §7), and the
// measurement requests agents posted. Nothing here is filed until the owner presses "File this".
import * as api from '../api.js';
import { el, clear, fmtWhen } from '../dom.js';
import { noteState } from './tablet-model.js';

export async function render(container) {
  let dead = false, showHidden = false, loading = false, again = false;
  const root = el('div', {});
  const msg = el('div', { role: 'alert' });
  container.appendChild(root);

  function say(text, ok) {
    clear(msg);
    if (text) msg.appendChild(el('div', { class: ok ? 'status' : 'error' }, text));
  }

  const act = (path, done) => async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    say('');
    try { await api.post(path, {}); if (!dead) { say(done, true); refresh(); } } catch (e) {
      if (!dead) { btn.disabled = false; say(e.message); }
    }
  };

  function requestCard(r) {
    return el('div', { class: 'card' },
      el('div', { class: 'card-title' }, el('span', {}, r.title),
        el('span', { class: 'meta' }, `${r.project_name || r.project || 'no project'} · ${fmtWhen(r.created)}`)),
      el('ul', {}, r.items.map((i) => el('li', {}, i))),
      el('div', { class: 'card-foot' },
        r.hidden
          ? el('button', { type: 'button', onclick: act(`/api/requests/${encodeURIComponent(r.id)}/restore`, 'Restored.') }, 'Restore')
          : el('button', { type: 'button', onclick: act(`/api/requests/${encodeURIComponent(r.id)}/ignore`, 'Ignored.') }, 'Ignore')));
  }

  function noteRow(n) {
    const s = noteState(n);
    const enc = encodeURIComponent(n.id);
    return el('div', { class: 'entry' },
      el('div', { class: 'entry-head' },
        el('span', { class: 'meta' }, fmtWhen(n.ts)),
        n.answer_to ? el('span', { class: 'badge' }, `measurements · ${n.answer_to}`) : null,
        s.label ? el('span', { class: 'meta' }, s.label) : null),
      el('div', { class: 'verbatim' }, n.text),
      el('div', { class: 'row-actions' },
        s.canFile ? el('button', { type: 'button', class: 'primary', onclick: act(`/api/tablet-notes/${enc}/file`, 'Filing it now. Its card shows on Today.') }, 'File this') : null,
        n.hidden
          ? el('button', { type: 'button', onclick: act(`/api/tablet-notes/${enc}/restore`, 'Restored.') }, 'Restore')
          : el('button', { type: 'button', onclick: act(`/api/tablet-notes/${enc}/ignore`, 'Ignored.') }, 'Ignore')));
  }

  async function refresh() {
    if (dead) return;
    if (loading) { again = true; return; }
    loading = true;
    try {
      const v = await api.get(`/api/tablet-notes${showHidden ? '?hidden=1' : ''}`);
      if (dead) return;
      clear(root);
      root.appendChild(el('h1', {}, 'Tablet'));
      root.appendChild(el('p', { class: 'muted' },
        'Everything typed on the workbench tablet, newest first. Nothing here is filed until you press File this.'));
      root.appendChild(msg);
      if (v.requests.length) {
        root.appendChild(el('h2', {}, 'Open requests'));
        for (const r of v.requests) root.appendChild(requestCard(r));
      }
      root.appendChild(el('h2', {}, 'Notes'));
      if (!v.notes.length) root.appendChild(el('p', { class: 'empty' }, 'Nothing from the tablet yet.'));
      for (const n of v.notes) root.appendChild(noteRow(n));
      const hidden = v.hidden.notes + v.hidden.requests;
      if (hidden || showHidden) {
        root.appendChild(el('div', { class: 'actions' }, el('button', { type: 'button', onclick: () => { showHidden = !showHidden; refresh(); } },
          showHidden ? 'Hide ignored' : `Show ignored (${hidden})`)));
      }
    } catch (e) {
      if (!dead) clear(root).appendChild(el('p', { class: 'error', role: 'alert' }, `Couldn't load the tablet notes: ${e.message}`));
    } finally {
      loading = false;
      if (again) { again = false; refresh(); }
    }
  }

  await refresh();
  const offs = ['tablet.changed', 'requests.changed', 'filing.started', 'filing.done', 'filing.failed', 'ws.open']
    .map((type) => api.onEvent(type, refresh));
  return () => { dead = true; for (const off of offs) off(); };
}
