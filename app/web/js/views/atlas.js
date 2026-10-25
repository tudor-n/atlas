// ATLAS (voice-input decisions VI1–VI9; it replaces Ask): one input to write something down or to ask.
// The intent is guessed as you type (intent-model.js) and shown as NOTE | ASK; a press on it overrides
// the guess until the field is emptied. A note is saved and filed, and its confirm card fills the
// screen (look the tasks over there). A question gets the MATCHES rail, the cited ANSWER and DIG DEEPER,
// as Ask did. Above both: the ATLAS dot (atlas-dot.js), which moves while ATLAS speaks, and a one-line
// caption of what ATLAS last said (atlas.reply), shown whether or not the voice spoke. What was done is
// always named with the other way one press away (SAVE AS NOTE INSTEAD, ASK INSTEAD), so a wrong guess
// never loses what was written. Leaving the screen hands the last note to the tray, and anything typed
// but not sent to the capture bar.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { answerNodes, hitList, noteText } from '../components/text.js';
import { button, emptyRow, dot, seg } from '../components/ui.js';
import { confirmCard } from '../components/confirm-card.js';
import { atlasDot } from '../components/atlas-dot.js';
import { readonlyReason } from '../drive.js';
import { shownIntent } from '../intent-model.js';

const MAX_LINES = 6;
const AT_REST = 'Write something down, or ask me something.';
const INTENTS = [{ value: 'note', label: 'NOTE' }, { value: 'ask', label: 'ASK' }];

export function render(container, params, query) {
  let closeStream = null;
  let lastQ = '';
  let noteSeq = 0;
  let pinned = null;     // the owner's NOTE/ASK press, until the field is emptied
  let saving = null;     // the text being saved, while a save is in flight
  let note = null;       // the note on screen: {entryId, text, card}
  let savedText = null;  // the text last saved as a note here (ASK INSTEAD keeps it in the log)
  let readonly = '';
  let dead = false;

  // -- presence: the dot and the caption --------------------------------------------------------
  const dotNode = atlasDot();
  const say = el('p', { class: 'read atl-say f', 'aria-live': 'polite' }, AT_REST);
  const caption = (text) => {
    if (!text) return;
    say.textContent = text;
    say.classList.remove('f');
  };

  // -- the input --------------------------------------------------------------------------------
  const input = el('textarea', {
    id: 'atl-q', rows: 1, autocomplete: 'off', placeholder: 'Buy M3 screws tomorrow. What did I say about the wrist servo?',
    oninput: () => { if (!input.value.trim()) pinned = null; sync(); fit(); },
    onkeydown: (ev) => {
      if (ev.key !== 'Enter' || ev.shiftKey || ev.altKey) return;
      if (ev.ctrlKey || ev.metaKey || intent() === 'ask') { ev.preventDefault(); submit(); }
    },
  });
  const pick = seg(INTENTS, 'note', (v) => { pinned = v; sync(); input.focus(); }, { label: 'Note or question' });
  // SAVE or ASK: an outline at rest, the white primary only while there is text (one primary on screen)
  const go = button('SAVE', { type: 'submit', cls: 'atl-go' });
  const form = el('form', { class: 'atl-bar', onsubmit: (ev) => { ev.preventDefault(); submit(); } },
    el('label', { class: 'sr', for: 'atl-q' }, 'Write something down or ask'), input, pick, go);
  const formErr = el('p', { class: 'atl-err err', role: 'alert' });

  const intent = () => shownIntent(input.value, pinned);
  function sync() {
    const now = intent();
    INTENTS.forEach((o, i) => pick.children[i].setAttribute('aria-pressed', o.value === now ? 'true' : 'false'));
    go.textContent = now === 'ask' ? 'ASK' : 'SAVE';
    go.classList.toggle('pr', input.value.trim() !== '');
  }
  function fit() {
    const top = input.scrollTop;
    input.rows = 1;  // measure the content from the smallest box
    if (input.value.includes('\n') || input.scrollHeight > input.clientHeight + 2) {
      const cs = getComputedStyle(input);
      const lh = parseFloat(cs.lineHeight) || 22;
      const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
      input.rows = Math.max(1, Math.min(MAX_LINES, Math.ceil((input.scrollHeight - pad) / lh - 0.05)));
    }
    input.scrollTop = top;
  }

  // -- what was done, and the other way ---------------------------------------------------------
  const was = el('div', { class: 'atl-was', hidden: true });
  function showWas(text, action, hint) {
    clear(was).append(...[el('span', { class: 'lb' }, text), action, hint && el('span', { class: 'f atl-hint' }, hint)].filter(Boolean));
    was.hidden = false;
  }

  // -- a question: MATCHES rail and ANSWER (as Ask had them) -------------------------------------
  const hitsCount = el('span', { class: 'n' });
  const hitsBody = el('div', {}, emptyRow('Matches show here as soon as you ask.'));
  const rail = el('section', { class: 'rail ask-rail', 'aria-label': 'Matches' },
    el('div', { class: 'sect-h' }, el('h2', { class: 'lb' }, 'Matches'), hitsCount), hitsBody);
  const status = el('span', { class: 'dl ask-st', role: 'status' });
  const answer = el('div', { class: 'read ask-a', 'aria-live': 'polite' });
  const errBox = el('div', { class: 'ask-err err', role: 'alert' });
  const actions = el('div', { class: 'ask-act' });
  const deeperStatus = el('span', { class: 'dl ask-st', role: 'status' });
  const deeper = el('div', { class: 'read ask-a', 'aria-live': 'polite' });
  const deeperErr = el('div', { class: 'ask-err err', role: 'alert' });
  const deeperAct = el('div', { class: 'ask-act' });
  const deeperBox = el('section', { class: 'ask-deeper', hidden: true },
    el('div', { class: 'sect-h' }, el('h2', { class: 'lb' }, 'Deeper search'), deeperStatus),
    el('div', { class: 'pane' }, deeper, deeperErr, deeperAct));
  const quickUi = { st: status, err: errBox, act: actions };
  const deeperUi = { st: deeperStatus, err: deeperErr, act: deeperAct };
  const answerBox = el('section', { class: 'ask-answer' },
    el('div', { class: 'sect-h' }, el('h2', { class: 'lb' }, 'Answer'), status),
    el('div', { class: 'pane' }, answer, errBox, actions),
    deeperBox);
  const noteRef = el('span', { class: 'ask-ref' });
  const noteBody = el('div', { class: 'pane' });
  const noteBox = el('section', { class: 'ask-note', hidden: true, 'aria-label': 'Note' },
    el('div', { class: 'ask-note-h' }, button('‹ Back', { size: 40, onclick: closeNote }), noteRef),
    noteBody);
  const main = el('div', { class: 'main' }, answerBox, noteBox);
  const askPane = el('div', { class: 'vw split ask-split', hidden: true }, rail, main);

  // -- a note: its confirm card -------------------------------------------------------------------
  const notePane = el('div', { class: 'atl-note', hidden: true });
  const idle = el('div', { class: 'atl-idle' },
    emptyRow('Notes are saved, then filed, and the possible tasks come back here to confirm. Questions get your own words back, with citations.'));

  const body = el('div', { class: 'atl-body' }, idle, askPane, notePane);
  const root = el('div', { class: 'vw atl' },
    el('section', { class: 'atl-top', 'aria-label': 'ATLAS' },
      el('div', { class: 'atl-me' }, dotNode, say), form, formErr),
    was, body);
  root.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !noteBox.hidden) { ev.preventDefault(); closeNote(); }
  });
  container.appendChild(root);
  sync();
  input.focus();

  const subs = [api.onEvent('atlas.reply', ({ data }) => caption(data && data.text))];
  api.get('/api/state').then((st) => { readonly = readonlyReason(st && st.drive); }).catch(() => {});

  function show(which) {
    idle.hidden = which !== 'idle';
    askPane.hidden = which !== 'ask';
    notePane.hidden = which !== 'note';
  }

  // -- submit --------------------------------------------------------------------------------------
  function submit() {
    const text = input.value;
    if (!text.trim()) return;
    if (intent() === 'ask') {
      clearField();
      ask(text.trim());
    } else {
      saveNote(text, { clearOnSave: true });
    }
  }

  function clearField() {
    input.value = '';
    pinned = null;
    sync();
    fit();
  }

  // -- notes -----------------------------------------------------------------------------------------
  async function saveNote(text, { clearOnSave = false } = {}) {
    if (saving !== null) return;
    saving = text;
    go.disabled = true;
    formErr.textContent = '';
    try {
      const res = await api.post('/api/entries', { text, source: 'pc', reply: true });
      if (dead) {  // the owner left while it saved: its card goes to the tray, as on leaving
        if (!(res && res.held)) tray(res.entry_id, text);
        return;
      }
      if (clearOnSave && input.value === text) clearField();  // saved; keep anything typed meanwhile
      stop();
      savedText = text;
      lastQ = text.trim();
      const held = !!(res && res.held);
      release();
      if (held) {
        clear(notePane).appendChild(emptyRow('Saved, and held on this OS until the drive is back. It is filed then.'));
      } else {
        const card = confirmCard(res.entry_id, { entryText: text, blocked: () => readonly, compact: false });
        note = { entryId: res.entry_id, text, card };
        clear(notePane).appendChild(card);
      }
      showWas('Saved as a note', button('ASK INSTEAD', { size: 40, onclick: () => ask(lastQ) }),
        'It stays in your log either way.');
      show('note');
    } catch (e) {
      if (!dead) formErr.textContent = `Not saved: ${e.message}. Your text is still here.`;
      else toBar(text, `Not saved: ${e.message}. Your text is back here.`);  // the field is gone
    } finally {
      saving = null;
      go.disabled = false;
    }
  }

  // The note on screen goes to the tray (a new note replaces it, or the owner leaves): its filing and
  // proposals stay one press away, as a capture's do.
  function release() {
    if (!note) return;
    const { entryId, text, card } = note;
    note = null;
    card.destroy();
    card.remove();
    tray(entryId, text);
  }
  const tray = (entryId, text) => document.dispatchEvent(new CustomEvent('atlas:tray', { detail: { entryId, text } }));
  // Text that would be lost with this screen goes into the capture bar (app.js), with why when given.
  const toBar = (text, why) => document.dispatchEvent(new CustomEvent('atlas:draft', { detail: { text, why } }));

  // -- questions -----------------------------------------------------------------------------------
  function stop() { if (closeStream) { closeStream(); closeStream = null; } }

  function markHit(ref) {
    for (const n of hitsBody.querySelectorAll('.hit')) n.classList.toggle('cur', !!ref && n.dataset.ref === ref);
  }

  function showNote(ref, text) {
    noteRef.textContent = ref;
    clear(noteBody).appendChild(noteText(text));
    answerBox.hidden = true;
    noteBox.hidden = false;
    main.scrollTop = 0;
    markHit(ref);
    const back = noteBox.querySelector('.b');
    if (back) back.focus();
  }

  function closeNote() {
    noteSeq++;
    noteBox.hidden = true;
    answerBox.hidden = false;
    markHit(null);
  }

  // Always the file as it is now, never the indexed copy (which may predate an edit).
  async function openNote(ref) {
    const mine = ++noteSeq;
    let text;
    try {
      const notes = await api.get(`/api/notes?prefix=${encodeURIComponent(ref.split('#')[0])}`);
      text = notes.length ? notes[0].text : 'That note was not found.';
    } catch (e) {
      text = `Couldn't open that note: ${e.message}`;
    }
    if (mine === noteSeq) showNote(ref, text);
  }

  const setStatus = (node, kind, text) => {
    clear(node);
    if (text) node.append(dot(kind), text);
  };

  // Streams `path` into `target`. The text is re-linked on every chunk, since a ref may span chunks.
  function run(path, target, ui, { onHits, onDone } = {}) {
    const { st, err, act } = ui;
    stop();
    err.textContent = '';
    clear(act);
    let text = '';
    let failed = false;
    const paint = () => { clear(target).append(...answerNodes(text, openNote)); };
    setStatus(st, '', 'SEARCHING');
    closeStream = api.sse(path, {
      hits: (data) => { if (onHits) onHits(data); setStatus(st, 'g', 'THINKING'); },
      delta: (chunk) => {
        text += typeof chunk === 'string' ? chunk : '';
        paint();
        setStatus(st, 'g', 'STREAMING');
      },
      reply: (line) => { if (typeof line === 'string') caption(line); },
      error: (msg) => {
        failed = true;
        stop();
        setStatus(st, '', '');
        err.textContent = `Couldn't get an answer: ${msg}`;
        act.appendChild(button('Retry', { onclick: () => run(path, target, ui, { onHits, onDone }) }));
      },
      done: () => {
        closeStream = null;
        if (failed) return;
        setStatus(st, '', 'DONE');
        if (onDone) onDone();
      },
    });
  }

  function ask(q) {
    if (!q) return;
    lastQ = q;
    release();  // a note on screen goes to the tray: hidden here, it would be out of reach
    closeNote();
    hitsCount.textContent = '';
    clear(hitsBody).appendChild(emptyRow('Searching…'));
    clear(answer);
    deeperBox.hidden = true;
    clear(deeper);
    setStatus(deeperStatus, '', '');
    deeperErr.textContent = '';
    clear(deeperAct);
    const asNote = savedText !== null && savedText.trim() === q
      ? null  // already in the log: nothing to offer
      : button('SAVE AS NOTE INSTEAD', { size: 40, onclick: () => saveNote(q) });
    showWas('Asked as a question', asNote, asNote ? null : 'It is in your log as a note too.');
    show('ask');
    run(`/api/ask?q=${encodeURIComponent(q)}&reply=1`, answer, quickUi, {
      onHits: (data) => {
        const hits = Array.isArray(data) ? data : [];
        hitsCount.textContent = String(hits.length);
        clear(hitsBody).appendChild(hitList(hits, (h) => openNote(h.ref)));
      },
      onDone: () => {
        actions.appendChild(button('Dig deeper', { amber: true, onclick: dig }));
        actions.appendChild(el('span', { class: 'ask-cost f' }, 'Slower, and uses more of your Claude allowance.'));
      },
    });
  }

  function dig() {
    deeperBox.hidden = false;
    clear(deeper);
    clear(actions);  // one dig at a time; RETRY sits under the deeper answer if it fails
    run(`/api/ask/deeper?q=${encodeURIComponent(lastQ)}`, deeper, deeperUi);
    if (typeof deeperBox.scrollIntoView === 'function') deeperBox.scrollIntoView({ block: 'nearest' });
  }

  // #/atlas?q=… (the capture bar or the widget asked): ask it at once, and drop it from the address
  // so a reload doesn't ask again (no hashchange, so the view stays).
  const asked = query && query.get('q');
  if (asked && asked.trim()) {
    const rest = new URLSearchParams(query);
    rest.delete('q');
    history.replaceState(history.state, '', `#/atlas${rest.toString() ? `?${rest}` : ''}`);
    ask(asked.trim());
  } else {
    takeHandoff();
  }
  // The widget's question waits in the hub (it can open a view only by name): take it on arrival, and
  // when the window comes forward while this screen is already open.
  const onShown = () => { if (document.visibilityState === 'visible') takeHandoff(); };
  document.addEventListener('visibilitychange', onShown);
  window.addEventListener('focus', onShown);
  async function takeHandoff() {
    try {
      const { text } = await api.post('/api/atlas/handoff/take', {});
      if (!dead && text) ask(text);
    } catch (_) { /* nothing waiting, or the hub is busy: the widget said where it went */ }
  }

  return () => {
    dead = true;
    // What was typed and not sent goes to the capture bar (it shows again off this screen); not the
    // text a save in flight is taking care of.
    const left = input.value;
    if (left.trim() && left !== saving) toBar(left);
    document.removeEventListener('visibilitychange', onShown);
    window.removeEventListener('focus', onShown);
    stop();
    release();
    dotNode.destroy();
    for (const off of subs.splice(0)) off();
  };
}
