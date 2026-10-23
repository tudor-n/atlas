// The always-visible capture bar (the arm's 72px bottom action bar): CAPTURE, a textarea, a mono status
// line and SAVE. Text is cleared only after the server has it: 201 (in the raw log) or, while the drive
// isn't usable, 200 with `held` (on disk in this OS's holding inbox).
// The textarea is one line at rest; focused or holding text it is at least MIN_ACTIVE lines and grows
// with its content up to MAX_LINES, then scrolls. Its height is the `rows` attribute (the CSP allows no
// inline styles), and the bar carries `.on` while it is active. SAVE is an outline at rest and becomes
// the white primary only while the box holds text (one white primary on screen is enough).
import * as api from '../api.js';
import { el } from '../dom.js';
import { button as uiButton } from './ui.js';

const MAX_LINES = 6;
const MIN_ACTIVE = 3;

export function mountCapture(root, { onSaved }) {
  const input = el('textarea', {
    id: 'capture-text', rows: 1, placeholder: 'What is on your mind? Ctrl+Enter to save.',
    onkeydown: (ev) => {
      if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); save(); }
    },
    oninput: fit, onfocus: fit, onblur: () => { if (pressing) collapseLater = true; else fit(); },
  });
  // A press elsewhere blurs the field at pointer-down. Shrinking it then would move the tray (right above
  // the bar) before the click, and the press would land on something else, so it shrinks after the click
  // (a touch's click can come a little after pointer-up; 400 ms covers a press that makes none).
  let pressing = false;
  let collapseLater = false;
  const released = () => {
    pressing = false;
    if (!collapseLater) return;
    collapseLater = false;
    let done = false;
    const go = () => { if (!done) { done = true; fit(); } };
    document.addEventListener('click', () => setTimeout(go, 0), { capture: true, once: true });
    setTimeout(go, 400);
  };
  document.addEventListener('pointerdown', () => { pressing = true; }, true);
  document.addEventListener('pointerup', released, true);
  document.addEventListener('pointercancel', released, true);
  const status = el('span', { class: 'st', role: 'status' });
  const error = el('span', { class: 'err', role: 'alert' });
  const button = uiButton('SAVE', { onclick: save });
  let saving = false;

  root.append(
    el('label', { class: 'lb', for: 'capture-text' }, 'CAPTURE'),
    input,
    el('div', { class: 'msg' }, status, error),
    button,
  );

  function fit() {
    button.classList.toggle('pr', input.value.trim() !== '');
    const active = document.activeElement === input || input.value !== '';
    root.classList.toggle('on', active);
    if (!active) { input.rows = 1; return; }
    const top = input.scrollTop;
    input.rows = 1;  // measure the content from the smallest box
    const cs = getComputedStyle(input);
    const lh = parseFloat(cs.lineHeight) || 22;
    const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
    const lines = Math.ceil((input.scrollHeight - pad) / lh - 0.05);
    input.rows = Math.max(MIN_ACTIVE, Math.min(MAX_LINES, lines));
    input.scrollTop = top;
  }

  async function save() {
    const text = input.value;
    if (saving || !text.trim()) return;
    saving = true;
    button.disabled = true;
    error.textContent = '';
    status.textContent = 'Saving…';
    try {
      const res = await api.post('/api/entries', { text, source: 'pc' });
      if (input.value === text) input.value = '';  // saved; keep anything typed meanwhile
      fit();
      const held = !!(res && res.held);
      status.textContent = held ? 'Saved · held on this OS until the drive is back' : 'Saved · filing…';
      onSaved(res.entry_id, text, held);
    } catch (e) {
      status.textContent = '';
      error.textContent = `Not saved: ${e.message}. Your text is still here.`;
    } finally {
      saving = false;
      button.disabled = false;
    }
  }

  return { setStatus: (s) => { status.textContent = s; }, focus: () => input.focus() };
}
