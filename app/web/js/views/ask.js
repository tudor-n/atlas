// Ask (spec 5.4; phase 4.5 §4): instant hits, then a streamed answer with citation links, then
// "Dig deeper". Layout: a 56px query bar; below it a MATCHES rail (360px) and the ANSWER pane.
// The note viewer replaces the answer pane until ‹ BACK (or Esc).
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { answerNodes, hitList, noteText } from '../components/text.js';
import { button, sectionHead, emptyRow, dot } from '../components/ui.js';

export function render(container) {
  let closeStream = null;
  let lastQ = '';
  let noteSeq = 0;

  const input = el('input', { id: 'ask-q', type: 'text', autocomplete: 'off', required: true,
    placeholder: 'What did I say about the wrist servo?' });
  const form = el('form', { class: 'ask-bar', onsubmit: (ev) => { ev.preventDefault(); ask(); } },
    el('label', { class: 'sr', for: 'ask-q' }, 'Ask your brain'), input,
    button('Ask', { primary: true, type: 'submit', cls: 'ask-go' }));

  // MATCHES rail
  const hitsCount = el('span', { class: 'n' });
  const hitsBody = el('div', {}, emptyRow('Matches show here as soon as you ask.'));
  const rail = el('section', { class: 'rail ask-rail', 'aria-label': 'Matches' },
    el('div', { class: 'sect-h' }, el('h2', { class: 'lb' }, 'Matches'), hitsCount), hitsBody);

  // ANSWER pane: status in the section header, the answer, actions, then DEEPER SEARCH
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
    el('div', { class: 'pane' },
      el('p', { class: 'ask-hint f' }, 'Answers quote your own words, with a citation for each claim.'),
      answer, errBox, actions),
    deeperBox);

  // note viewer (replaces the answer pane)
  const noteRef = el('span', { class: 'ask-ref' });
  const noteBody = el('div', { class: 'pane' });
  const noteBox = el('section', { class: 'ask-note', hidden: true, 'aria-label': 'Note' },
    el('div', { class: 'ask-note-h' }, button('‹ Back', { size: 40, onclick: closeNote }), noteRef),
    noteBody);

  const main = el('div', { class: 'main' }, answerBox, noteBox);
  const root = el('div', { class: 'vw ask' }, form, el('div', { class: 'vw split ask-split' }, rail, main));
  root.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && !noteBox.hidden) { ev.preventDefault(); closeNote(); }
  });
  container.appendChild(root);
  input.focus();

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

  function ask() {
    const q = input.value.trim();
    if (!q) return;
    lastQ = q;
    closeNote();
    hitsCount.textContent = '';
    clear(hitsBody).appendChild(emptyRow('Searching…'));
    clear(answer);
    root.classList.add('asked');
    deeperBox.hidden = true;
    clear(deeper);
    setStatus(deeperStatus, '', '');
    deeperErr.textContent = '';
    clear(deeperAct);
    run(`/api/ask?q=${encodeURIComponent(q)}`, answer, quickUi, {
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

  return stop;
}
