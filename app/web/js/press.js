// Press feedback and the tap-again confirmation (phase 4.5 design §2, §5).
// - installPress(): one set of delegated listeners puts the arm's `prs` class on the pressed control
//   at pointer-down (and on Space/Enter for a focused button) and takes it off on release, cancel or
//   slide-off. CSS styles .prs, never :active.
// - twice(button, {label, ms, onGo}): a risky action fires on a second press within `ms`; the first
//   shows `label` ("TAP AGAIN"), turns the button amber (.arm) and says so in an aria-live region.
// Text only ever goes in via textContent or as nodes (rule 6).
import { Twice } from './press-model.js';

const PRESSABLE = 'button, .b, .row[data-press], [data-press]';
const KEYS = new Set([' ', 'Enter']);

const disabled = (n) => !!(n.disabled || n.classList.contains('dis') || n.getAttribute('aria-disabled') === 'true');

const installed = new WeakMap();  // root -> uninstall

export function installPress(root = document) {
  if (installed.has(root)) return installed.get(root);
  let cur = null;

  const find = (target) => {
    const n = target && target.closest ? target.closest(PRESSABLE) : null;
    if (!n || disabled(n)) return null;
    if (root !== document && !root.contains(n)) return null;
    return n;
  };
  const release = () => {
    if (cur) cur.classList.remove('prs');
    cur = null;
  };
  const down = (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    release();
    cur = find(e.target);
    if (cur) cur.classList.add('prs');
  };
  // pointerleave doesn't bubble, so slide-off is pointerout to somewhere outside the pressed control.
  const out = (e) => { if (cur && !(e.relatedTarget && cur.contains(e.relatedTarget))) release(); };
  const keydown = (e) => {
    if (!KEYS.has(e.key) || e.repeat) return;
    const n = e.target && e.target.closest ? e.target.closest('button') : null;
    if (!n || disabled(n) || (root !== document && !root.contains(n))) return;
    release();
    cur = n;
    n.classList.add('prs');
  };
  const keyup = (e) => { if (KEYS.has(e.key)) release(); };
  const hidden = () => { if (document.hidden) release(); };

  const on = [
    [root, 'pointerdown', down], [root, 'pointerout', out], [root, 'keydown', keydown],
    [document, 'pointerup', release], [document, 'pointercancel', release], [document, 'keyup', keyup],
    [window, 'blur', release], [document, 'visibilitychange', hidden],
  ];
  for (const [t, ev, fn] of on) t.addEventListener(ev, fn, true);
  const uninstall = () => {
    for (const [t, ev, fn] of on) t.removeEventListener(ev, fn, true);
    release();
    installed.delete(root);
  };
  installed.set(root, uninstall);
  return uninstall;
}

// -- twice() -----------------------------------------------------------------------------------
let live = null;

function announce(text) {
  if (!live || !live.isConnected) {
    live = document.createElement('div');
    live.className = 'sr';
    live.setAttribute('aria-live', 'assertive');
    live.setAttribute('role', 'status');
    document.body.appendChild(live);
  }
  live.textContent = '';
  setTimeout(() => { live.textContent = text; }, 30);  // a change the screen reader notices
}

// Returns {reset, destroy}. onGo(event) runs on the confirming press.
export function twice(button, { label = 'TAP AGAIN', ms = 2000, onGo } = {}) {
  const gate = new Twice(ms);
  let saved = null;  // the button's own nodes while it shows `label`
  let timer = null;

  const restore = () => {
    clearTimeout(timer);
    timer = null;
    gate.reset();
    button.classList.remove('arm');
    if (saved) { button.replaceChildren(...saved); saved = null; }
  };
  const click = (e) => {
    if (disabled(button)) return;
    if (gate.tap(Date.now()) === 'go') {
      restore();
      if (onGo) onGo(e);
      return;
    }
    const what = (button.getAttribute('aria-label') || button.textContent || '').trim();
    saved = Array.from(button.childNodes);
    button.textContent = label;
    button.classList.add('arm');
    announce(what ? `${label}: ${what}` : label);
    clearTimeout(timer);
    timer = setTimeout(restore, ms);
  };
  button.addEventListener('click', click);
  return {
    reset: restore,
    destroy: () => { restore(); button.removeEventListener('click', click); },
  };
}
