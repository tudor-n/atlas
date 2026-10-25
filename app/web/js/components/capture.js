// The always-visible capture bar (the arm's 72px bottom action bar): CAPTURE, a textarea, a mono status
// line and SAVE. Text is cleared only after the server has it: 201 (in the raw log) or, while the drive
// isn't usable, 200 with `held` (on disk in this OS's holding inbox).
// The textarea is one line at rest; focused or holding text it is at least MIN_ACTIVE lines and grows
// with its content up to MAX_LINES, then scrolls. Its height is the `rows` attribute (the CSP allows no
// inline styles), and the bar carries `.on` while it is active. SAVE is an outline at rest and becomes
// the white primary only while the box holds text (one white primary on screen is enough).
// It is one of the one input's doors (voice-input decisions VI1): NOTE | ASK follows the intent as you
// type (a press overrides it until the box is emptied) and the button says SAVE or ASK. A note saves as
// before, and ATLAS replies once it is filed; a question goes to the ATLAS screen (`onAsk`).
import * as api from '../api.js';
import { el } from '../dom.js';
import { shownIntent } from '../intent-model.js';
import { button as uiButton, seg } from './ui.js';

const INTENTS = [{ value: 'note', label: 'NOTE' }, { value: 'ask', label: 'ASK' }];

const MAX_LINES = 6;
const MIN_ACTIVE = 3;

export function mountCapture(root, { onSaved, onAsk }) {
  let pinned = null;  // the owner's NOTE/ASK press, until the box is emptied
  const input = el('textarea', {
    id: 'capture-text', rows: 1, placeholder: 'Write something down, or ask. Ctrl+Enter to send.',
    onkeydown: (ev) => {
      if (ev.key !== 'Enter' || ev.shiftKey || ev.altKey) return;
      if (ev.ctrlKey || ev.metaKey || intent() === 'ask') { ev.preventDefault(); save(); }
    },
    oninput: () => { if (!input.value.trim()) pinned = null; fit(); },
    onfocus: fit, onblur: () => { if (pressing) collapseLater = true; else fit(); },
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
  const pick = seg(INTENTS, 'note', (v) => { pinned = v; fit(); input.focus(); }, { label: 'Note or question' });
  let saving = false;

  root.append(
    el('label', { class: 'lb', for: 'capture-text' }, 'CAPTURE'),
    input,
    el('div', { class: 'msg' }, status, error),
    pick,
    button,
  );

  const intent = () => shownIntent(input.value, pinned);

  function fit() {
    const now = intent();
    INTENTS.forEach((o, i) => pick.children[i].setAttribute('aria-pressed', o.value === now ? 'true' : 'false'));
    button.textContent = now === 'ask' ? 'ASK' : 'SAVE';
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
    if (intent() === 'ask' && onAsk) {
      input.value = '';
      pinned = null;
      fit();
      error.textContent = '';
      status.textContent = '';
      onAsk(text.trim());
      return;
    }
    saving = true;
    button.disabled = true;
    error.textContent = '';
    status.textContent = 'Saving…';
    try {
      const res = await api.post('/api/entries', { text, source: 'pc', reply: true });
      if (input.value === text) { input.value = ''; pinned = null; }  // saved; keep anything typed meanwhile
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
