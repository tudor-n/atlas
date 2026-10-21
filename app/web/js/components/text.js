// Text rendering helpers for recall and journal. Every piece of text enters the page as a text node.
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

// One raw journal entry: time, source, filing status, and the verbatim text.
export function entryCard(e, { highlight = false } = {}) {
  const f = e.filing;
  const files = f && Array.isArray(f.files) ? f.files.map((x) => (typeof x === 'string' ? x : x.path)).filter(Boolean) : [];
  return el('article', { class: highlight ? 'entry hl' : 'entry', id: `entry-${e.id}` },
    el('div', { class: 'entry-head' },
      el('span', { class: 'meta' }, `${String(e.ts).slice(0, 10)} ${String(e.ts).slice(11, 16)}`),
      el('span', { class: 'badge' }, e.source),
      el('span', { class: f && f.status === 'failed' ? 'badge fail' : 'badge' }, f ? `filing: ${f.status}` : 'not filed')),
    el('pre', { class: 'verbatim' }, e.text),
    f && f.synopsis ? el('div', { class: 'meta' }, f.synopsis) : null,
    files.length ? el('div', { class: 'meta files-line' }, files.join(', ')) : null,
    f && f.error ? el('div', { class: 'error' }, f.error) : null);
}

// Search hits (entries and notes). onNote(hit) is called for a note hit.
export function hitList(hits, onNote) {
  if (!hits.length) return el('p', { class: 'empty' }, 'No matching entries or notes.');
  return el('ul', { class: 'hits' }, hits.map((h) => el('li', { class: 'hit' },
    el('div', { class: 'meta' }, h.kind === 'entry' ? 'Entry' : 'Note', h.date ? ` · ${h.date}` : '', ' · ',
      h.kind === 'entry'
        ? el('a', { href: `#/journal?entry=${encodeURIComponent(h.ref)}` }, h.ref)
        : el('a', { href: '#', onclick: (ev) => { ev.preventDefault(); onNote(h); } }, h.ref)),
    el('div', { class: 'snippet' }, snippetNodes(h.snippet)))));
}
