// Text rendering helpers for recall, journal and browse. Every piece of text enters the page as a
// text node. The look of entryCard / hitList / noteText lives in css/views/journal.css (linked
// globally), so Ask, Journal and Browse draw them the same way.
import { el } from '../dom.js';

const MARK = /\[([^[\]]*)\]/g;

// FTS snippets mark matches with [ ]. Balanced pairs become <b>; stray brackets stay plain text.
export function snippetNodes(snippet) {
  const out = [];
  const s = String(snippet ?? '');
  let last = 0;
  for (const m of s.matchAll(MARK)) {
    if (m.index > last) out.push(s.slice(last, m.index));
    out.push(el('b', {}, m[1]));
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

const ENTRY_ID = /^e-\d{8}-\d{6}-[0-9a-z]+$/;
// "path/to/note.md" or "path/to/note.md#Heading" (a note's preamble has no heading).
const NOTE_REF = /^[^[\]#\s][^[\]#]*\.md(#[^[\]]+)?$/;
const REF = /\[([^[\]]+)\]/g;

export const isEntryRef = (r) => ENTRY_ID.test(r);
export const isNoteRef = (r) => NOTE_REF.test(r);

// Turn citation refs in an answer into links. onNote(ref) is called when a note ref is clicked.
// Anything else in brackets stays text.
export function answerNodes(text, onNote) {
  const out = [];
  const s = String(text ?? '');
  let last = 0;
  for (const m of s.matchAll(REF)) {
    const ref = m[1];
    let node = null;
    if (isEntryRef(ref)) {
      node = el('a', { class: 'cite', href: `#/journal?entry=${encodeURIComponent(ref)}` }, `[${ref}]`);
    } else if (isNoteRef(ref)) {
      node = el('a', { class: 'cite', href: '#', onclick: (ev) => { ev.preventDefault(); onNote(ref); } }, `[${ref}]`);
    }
    if (!node) continue;
    if (m.index > last) out.push(s.slice(last, m.index));
    out.push(node);
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

// The files a filing touched, as paths (they come as strings or {path}).
export function filingFiles(filing) {
  const files = filing && Array.isArray(filing.files) ? filing.files : [];
  return files.map((x) => (typeof x === 'string' ? x : x && x.path)).filter(Boolean);
}

// "2026-10-24T14:03:00" -> {date: "2026-10-24", time: "14:03"}
export function tsParts(ts) {
  const s = String(ts ?? '');
  return { date: s.slice(0, 10), time: s.slice(11, 16) };
}

// What the entry's filing status line says: {kind, text}. kind: 'failed' (an amber chip),
// 'busy' (a grey dot), 'none' (faint), 'done' (nothing beyond the files, or "no notes").
export function filingState(filing) {
  if (!filing) return { kind: 'none', text: 'NOT FILED' };
  if (filing.status === 'failed') return { kind: 'failed', text: 'FILING FAILED' };
  if (filing.status === 'queued' || filing.status === 'running') return { kind: 'busy', text: 'FILING' };
  return { kind: 'done', text: filingFiles(filing).length ? '' : 'FILED · NO NOTES' };
}

// One raw journal entry: a mono time column, the verbatim text in .read, then the source, the
// filing status and the files in mono 12 dim. withDate (the default, for lists without day
// headers) puts the date above the time in a wider column; Journal passes withDate: false.
export function entryCard(e, { highlight = false, withDate = true } = {}) {
  const f = e.filing;
  const { date, time } = tsParts(e.ts);
  const st = filingState(f);
  const files = filingFiles(f);
  const cls = ['ecard', withDate ? 'wd' : '', highlight ? 'hl' : ''].filter(Boolean).join(' ');
  return el('article', { class: cls, id: `entry-${e.id}`, title: `${date} ${time} · ${e.id}` },
    el('div', { class: 'ecard-t' },
      withDate ? el('span', { class: 'ecard-d' }, date) : null,
      el('span', {}, time)),
    el('div', { class: 'ecard-b' },
      el('div', { class: 'read ecard-x' }, e.text),
      f && f.synopsis ? el('div', { class: 'ecard-s' }, f.synopsis) : null,
      el('div', { class: 'ecard-m' },
        el('span', { class: 'ecard-src' }, String(e.source || '').toUpperCase()),
        st.kind === 'failed' ? el('span', { class: 'chip' }, st.text) : null,
        st.kind === 'busy' ? el('span', { class: 'dl' }, el('span', { class: 'dot', 'aria-hidden': 'true' }), st.text) : null,
        (st.kind === 'none' || st.kind === 'done') && st.text ? el('span', { class: 'f' }, st.text) : null,
        files.map((p) => el('span', { class: 'ecard-f' }, p))),
      f && f.error ? el('div', { class: 'ecard-e err' }, f.error) : null));
}

// A note's text as it reads on disk (whitespace kept), in the reading measure.
export const noteText = (text) => el('div', { class: 'read note-x' }, String(text ?? ''));

// Search hits (entries and notes) as rows: the ref in mono 12 dim, then the snippet with its
// matches in white 600. An entry hit links to the journal; a note hit calls onNote(hit). Every
// row carries data-ref, so a caller can mark the open one (.cur).
export function hitList(hits, onNote) {
  if (!hits.length) return el('div', { class: 'empty' }, 'No matching entries or notes.');
  return el('ul', { class: 'hitl' }, hits.map((h) => {
    const head = el('span', { class: 'hit-r' },
      el('span', { class: 'hit-k' }, h.kind === 'entry' ? 'ENTRY' : 'NOTE'),
      h.date ? ` ${h.date} · ` : ' ', h.ref);
    const body = el('span', { class: 'hit-s' }, snippetNodes(h.snippet));
    const node = h.kind === 'entry'
      ? el('a', { class: 'hit', href: `#/journal?entry=${encodeURIComponent(h.ref)}`, 'data-press': '', 'data-ref': h.ref }, head, body)
      : el('button', { type: 'button', class: 'hit', 'data-ref': h.ref, onclick: () => onNote(h) }, head, body);
    return el('li', {}, node);
  }));
}
