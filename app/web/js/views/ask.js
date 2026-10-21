// Ask (spec 5.4): instant hits, then a streamed answer with citation links, then "Dig deeper".
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { answerNodes, hitList } from '../components/text.js';

export function render(container) {
  let closeStream = null;
  let lastQ = '';

  const input = el('input', { id: 'ask-q', type: 'text', autocomplete: 'off', required: true,
    placeholder: 'What did I say about the wrist servo?' });
  const form = el('form', { class: 'ask-form', onsubmit: (ev) => { ev.preventDefault(); ask(); } },
    el('div', { class: 'grow' }, el('label', { for: 'ask-q' }, 'Ask your brain'), input),
    el('button', { class: 'primary', type: 'submit' }, 'Ask'));
  const hitsBody = el('div', {});
  const hitsBox = el('section', { hidden: true }, el('h2', {}, 'Matches'), hitsBody);
  const answer = el('div', { class: 'answer', 'aria-live': 'polite' });
  const answerBox = el('section', { hidden: true }, el('h2', {}, 'Answer'), answer);
  const deeper = el('div', { class: 'answer', 'aria-live': 'polite' });
  const deeperBox = el('section', { hidden: true }, el('h2', {}, 'Deeper search'), deeper);
  const noteBox = el('section', { hidden: true });
  const status = el('p', { class: 'status', role: 'status' });
  const errBox = el('div', { class: 'error', role: 'alert' });
  const actions = el('div', { class: 'ask-actions' });

  container.appendChild(el('div', {}, el('h1', {}, 'Ask'), form, status, errBox,
    hitsBox, answerBox, actions, deeperBox, noteBox));
  input.focus();

  function stop() { if (closeStream) { closeStream(); closeStream = null; } }

  function showNote(ref, text) {
    clear(noteBox).hidden = false;
    noteBox.appendChild(el('h2', {}, ref));
    noteBox.appendChild(el('pre', { class: 'verbatim' }, text));
    if (typeof noteBox.scrollIntoView === 'function') noteBox.scrollIntoView({ block: 'nearest' });
  }

  // Always the file as it is now, never the indexed copy (which may predate an edit).
  async function openNote(ref) {
    try {
      const notes = await api.get(`/api/notes?prefix=${encodeURIComponent(ref.split('#')[0])}`);
      showNote(ref, notes.length ? notes[0].text : 'That note was not found.');
    } catch (e) {
      showNote(ref, `Couldn't open that note: ${e.message}`);
    }
  }

  // Streams `path` into `target`. The text is re-linked on every chunk, since a ref may span chunks.
  function run(path, target, { onHits, onDone } = {}) {
    stop();
    errBox.textContent = '';
    clear(actions);
    let text = '';
    let failed = false;
    const paint = () => { clear(target).appendChild(el('div', { class: 'answer-text' }, answerNodes(text, openNote))); };
    status.textContent = 'Searching…';
    closeStream = api.sse(path, {
      hits: (data) => { if (onHits) onHits(data); status.textContent = 'Thinking…'; },
      delta: (chunk) => { text += typeof chunk === 'string' ? chunk : ''; paint(); },
      error: (msg) => {
        failed = true;
        stop();
        status.textContent = '';
        errBox.textContent = `Couldn't get an answer: ${msg}`;
        actions.appendChild(el('button', { type: 'button', onclick: () => run(path, target, { onHits, onDone }) }, 'Retry'));
      },
      done: () => {
        closeStream = null;
        if (failed) return;
        status.textContent = '';
        if (onDone) onDone();
      },
    });
  }

  function ask() {
    const q = input.value.trim();
    if (!q) return;
    lastQ = q;
    hitsBox.hidden = false;
    clear(hitsBody);
    clear(answer);
    answerBox.hidden = false;
    deeperBox.hidden = true;
    noteBox.hidden = true;
    clear(deeper);
    run(`/api/ask?q=${encodeURIComponent(q)}`, answer, {
      onHits: (data) => {
        clear(hitsBody).appendChild(hitList(Array.isArray(data) ? data : [], (h) => openNote(h.ref)));
      },
      onDone: () => {
        actions.appendChild(el('button', { type: 'button', onclick: dig }, 'Dig deeper'));
        actions.appendChild(el('span', { class: 'meta' }, ' Slower, and uses more of your Claude allowance.'));
      },
    });
  }

  function dig() {
    deeperBox.hidden = false;
    clear(deeper);
    run(`/api/ask/deeper?q=${encodeURIComponent(lastQ)}`, deeper);
  }

  return stop;
}
