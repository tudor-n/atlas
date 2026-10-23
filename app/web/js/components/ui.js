// Thin builders for the arm components (web/css/components.css) on top of el(). Every piece of text
// enters as a text node through el() (rule 6). Press feedback comes from js/press.js (installPress);
// risky buttons get twice() from there too.
import { el } from '../dom.js';

// size: 40 | 48 | 52 (48 is the default .b).
export function button(text, { primary, amber, size, onclick, disabled, title, type = 'button', cls } = {}) {
  const c = ['b', primary && 'pr', amber && 'am', size === 40 && 'h40', size === 52 && 'h52', cls]
    .filter(Boolean).join(' ');
  return el('button', { type, class: c, onclick, disabled: !!disabled, title }, text);
}

export const chip = (text, { title } = {}) => el('span', { class: 'chip', title }, text);

// kind: '' (grey), 'g' (green), 'a' (amber), 'ring' (hollow).
export const dot = (kind = '', { title } = {}) =>
  el('span', { class: kind ? `dot ${kind}` : 'dot', title, 'aria-hidden': title ? null : 'true' });

export const label = (text, { tag = 'span', cls } = {}) => el(tag, { class: cls ? `lb ${cls}` : 'lb' }, text);

// A dim label and a white mono value; warn turns the value amber.
export function readout(lbl, value, { warn, title } = {}) {
  return el('span', { class: warn ? 'ro warn' : 'ro', title }, el('span', { class: 'l' }, lbl), el('span', { class: 'v' }, value));
}

// A real switch: <button role="switch" aria-checked>. The visible label (if any) is inside the button,
// so the whole block is the hit area, as on the arm's LIVE. onchange(on) runs after the flip; it may
// return false (or a promise of false) to put the switch back.
export function switchEl({ on = false, small = false, label: text, onchange, disabled, title } = {}) {
  const node = el('button', {
    type: 'button', class: 'swb', role: 'switch', 'aria-checked': on ? 'true' : 'false',
    'aria-label': text ? null : title || null, title, disabled: !!disabled,
  },
  text ? el('span', { class: 'lb' }, text) : null,
  el('span', { class: small ? 'sw s2' : 'sw' }, el('i')));
  const set = (v) => node.setAttribute('aria-checked', v ? 'true' : 'false');
  node.addEventListener('click', async () => {
    const next = node.getAttribute('aria-checked') !== 'true';
    set(next);
    if (!onchange) return;
    try {
      if ((await onchange(next)) === false) set(!next);
    } catch (e) {
      set(!next);
      console.error(e);
    }
  });
  node.setOn = set;
  node.isOn = () => node.getAttribute('aria-checked') === 'true';
  return node;
}

// options: [{value, label}] or plain strings. onselect(value) after the selection moves.
export function seg(options, selected, onselect, { size, label: groupLabel } = {}) {
  const opts = options.map((o) => (typeof o === 'object' ? o : { value: o, label: String(o) }));
  const box = el('div', { class: size === 40 ? 'seg h40' : 'seg', role: 'group', 'aria-label': groupLabel });
  const cells = opts.map((o) => el('button', {
    type: 'button', 'aria-pressed': o.value === selected ? 'true' : 'false',
    onclick: (e) => {
      for (const c of cells) c.setAttribute('aria-pressed', c === e.currentTarget ? 'true' : 'false');
      if (onselect) onselect(o.value);
    },
  }, o.label));
  box.append(...cells);
  return box;
}

// A 40px section header: label left, mono count right (count omitted when null/undefined).
export function sectionHead(text, count, { tag = 'h2' } = {}) {
  return el('div', { class: 'sect-h' }, el(tag, { class: 'lb' }, text),
    count === null || count === undefined ? null : el('span', { class: 'n' }, String(count)));
}

// A 52px list row. With onclick it is a <button> (so put no other buttons inside it); otherwise a div.
export function row(children, { current, onclick, title, cls } = {}) {
  const c = ['row', current && 'cur', cls].filter(Boolean).join(' ');
  if (onclick) {
    return el('button', { type: 'button', class: c, onclick, title, 'aria-current': current ? 'true' : null }, children);
  }
  return el('div', { class: c, title, 'aria-current': current ? 'true' : null }, children);
}

export const emptyRow = (text) => el('div', { class: 'empty' }, text);

// An amber outline panel. With a title: the title (amber label with its square), then the text.
export function notice(text, { title, role = 'status' } = {}) {
  return el('div', { class: 'notice', role },
    title ? el('div', { class: 'lb' }, title) : null,
    text ? el('p', {}, text) : null);
}

// A segmented 4px bar: `filled` of `segments` cells lit, amber when warn.
export function meter(segments, filled, { warn, label: aria } = {}) {
  const n = Math.max(0, Math.min(segments, Math.round(filled)));
  const cells = Array.from({ length: segments }, (_, i) => el('i', { class: i < n ? 'on' : null }));
  return el('div', { class: warn ? 'meter4 warn' : 'meter4', role: 'img', 'aria-label': aria }, cells);
}
