// DOM helpers shared by the shell, components and views (no imports, so no cycles).
// el(tag, attrs, ...children). Text only ever enters the page as text nodes (never as HTML).
// attrs: `class`, `on<event>` functions, boolean/property attrs (value, checked, disabled, ...),
// anything else becomes an attribute. Children may be strings, numbers, nodes or arrays of those.
const PROPS = new Set(['value', 'checked', 'disabled', 'hidden', 'open', 'selected', 'required']);

export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'innerHTML' || k === 'outerHTML' || k === 'srcdoc') throw new Error(`el(): ${k} is not allowed`);
    if (k.startsWith('on')) {
      if (typeof v !== 'function') throw new Error(`el(): ${k} must be a function`);
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === 'class') {
      node.className = v;
    } else if (PROPS.has(k)) {
      node[k] = v;
    } else {
      node.setAttribute(k, v === true ? '' : String(v));
    }
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(node, c);
    else if (c instanceof Node) node.appendChild(c);
    else node.appendChild(document.createTextNode(String(c)));
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

// "2026-10-25T14:00" -> "2026-10-25 14:00"
export const fmtWhen = (iso) => (iso ? String(iso).replace('T', ' ') : '');
