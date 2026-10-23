// Tablet: everything typed on the workbench tablet, newest first (phase 3 design §7), and the
// measurement requests agents posted. Nothing here is filed until the owner presses "File this".
// Phase 4.5 layout: NOTES as blocks in .main, OPEN REQUESTS as cards in a 400px .side, and
// SHOW IGNORED as a switch row at the head of the requests (it shows ignored notes and requests).
import * as api from '../api.js';
import { el, clear, fmtWhen } from '../dom.js';
import { button, chip, dot, sectionHead, switchEl } from '../components/ui.js';
import { twice } from '../press.js';
import { noteState, noteDot, shortWhen } from './tablet-model.js';

export async function render(container) {
  let dead = false, showHidden = false, loading = false, again = false;
  const msg = el('div', { class: 'tmsg', role: 'alert' });
  const notesHead = el('div', {});
  const notesBox = el('div', { class: 'tnotes' });
  const reqHead = el('div', {});
  const reqBox = el('div', { class: 'treqs' });
  const toggle = switchEl({ label: 'Show ignored', onchange: (on) => { showHidden = on; refresh(); } });
  const hiddenN = el('span', { class: 'n' });
  toggle.insertBefore(hiddenN, toggle.lastChild);
  const swRow = el('div', { class: 'tsw', hidden: true }, toggle);

  container.appendChild(el('div', { class: 'vw split tablet' },
    el('div', { class: 'main' }, notesHead,
      el('p', { class: 'tintro' }, 'Everything typed on the workbench tablet, newest first. Nothing here is filed until you press File this.'),
      msg, notesBox),
    el('aside', { class: 'side w400', 'aria-label': 'Open requests' }, reqHead, swRow, reqBox)));

  // One primary per panel: FILE THIS is an outline, except on the note the focus is in (else the
  // first note that can be filed).
  function markPrimary(block) {
    const all = [...notesBox.querySelectorAll('.tfile')];
    const mine = block && block.querySelector('.tfile');
    const pick = mine || all[0] || null;
    for (const b of all) b.classList.toggle('pr', b === pick);
  }
  notesBox.addEventListener('focusin', (ev) => {
    const block = ev.target.closest && ev.target.closest('.tnote');
    if (block && block.querySelector('.tfile')) markPrimary(block);
  });

  function say(text, ok) {
    clear(msg);
    if (text) msg.appendChild(el('p', { class: ok ? 'ok' : 'err' }, text));
  }

  const act = (path, done) => async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    say('');
    try { await api.post(path, {}); if (!dead) { say(done, true); refresh(); } } catch (e) {
      if (!dead) { btn.disabled = false; say(e.message); }
    }
  };

  // IGNORE is a risky press: it fires on the second press within 2 s (twice()).
  function ignoreButton(path, what) {
    const btn = button('Ignore', { size: 40, title: `Ignore this ${what}` });
    const go = act(path, 'Ignored.');
    twice(btn, { onGo: (ev) => go(ev) });
    return btn;
  }

  function requestCard(r) {
    const enc = encodeURIComponent(r.id);
    return el('article', { class: r.hidden ? 'treq hid' : 'treq' },
      el('div', { class: 'treq-t' }, r.title),
      el('div', { class: 'treq-m', title: fmtWhen(r.created) }, `${r.project_name || r.project || 'no project'} · ${shortWhen(r.created, new Date())}`),
      el('ol', { class: 'treq-l' }, r.items.map((i, n) => el('li', {}, el('span', { class: 'i' }, String(n + 1)), el('span', {}, i)))),
      el('div', { class: 'treq-f' },
        r.hidden
          ? button('Restore', { size: 40, onclick: act(`/api/requests/${enc}/restore`, 'Restored.') })
          : ignoreButton(`/api/requests/${enc}/ignore`, 'request')));
  }

  function noteBlock(n) {
    const s = noteState(n);
    const d = noteDot(n);
    const enc = encodeURIComponent(n.id);
    const state = d === 'a'
      ? el('span', { class: 'dl amb' }, el('span', { class: 'sq' }), s.label)
      : el('span', { class: 'dl' }, d === null ? null : dot(d), s.label);
    return el('article', { class: n.hidden ? 'tnote hid' : 'tnote' },
      el('div', { class: 'tnote-h' },
        el('span', { class: 'tm', title: fmtWhen(n.ts) }, shortWhen(n.ts, new Date())),
        n.answer_to ? chip('Answer', { title: `Measurements for ${n.answer_to}` }) : null,
        n.answer_to ? el('span', { class: 'ans' }, n.answer_to) : null,
        el('span', { class: 'grow' }),
        state),
      el('div', { class: 'read tnote-x' }, n.text),
      el('div', { class: 'tnote-a' },
        s.canFile ? button('File this', { size: 40, cls: 'tfile', onclick: act(`/api/tablet-notes/${enc}/file`, 'Filing it now. Its card shows on Today.') }) : null,
        n.hidden
          ? button('Restore', { size: 40, onclick: act(`/api/tablet-notes/${enc}/restore`, 'Restored.') })
          : ignoreButton(`/api/tablet-notes/${enc}/ignore`, 'note')));
  }

  async function refresh() {
    if (dead) return;
    if (loading) { again = true; return; }
    loading = true;
    try {
      const v = await api.get(`/api/tablet-notes${showHidden ? '?hidden=1' : ''}`);
      if (dead) return;
      clear(notesHead).appendChild(sectionHead('Notes', v.notes.length, { tag: 'h1' }));
      clear(notesBox).append(...(v.notes.length
        ? v.notes.map(noteBlock)
        : [el('div', { class: 'empty' }, 'Nothing from the tablet yet.')]));
      markPrimary(null);
      clear(reqHead).appendChild(sectionHead('Open requests', v.requests.length));
      clear(reqBox).append(...(v.requests.length
        ? v.requests.map(requestCard)
        : [el('div', { class: 'empty' }, 'No open requests.')]));
      const hidden = v.hidden.notes + v.hidden.requests;
      swRow.hidden = !(hidden || showHidden);
      hiddenN.textContent = String(hidden);
      toggle.setOn(showHidden);
    } catch (e) {
      if (!dead) clear(notesBox).appendChild(el('p', { class: 'err terr', role: 'alert' }, `Couldn't load the tablet notes: ${e.message}`));
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
