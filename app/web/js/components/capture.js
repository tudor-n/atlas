// The always-visible capture box. Text is cleared only after the server answers 201.
import * as api from '../api.js';
import { el } from '../app.js';

export function mountCapture(root, { onSaved }) {
  const input = el('textarea', {
    id: 'capture-text', rows: 3, placeholder: 'What is on your mind? Ctrl+Enter to save.',
    onkeydown: (ev) => {
      if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); save(); }
    },
  });
  const status = el('span', { class: 'status', role: 'status' });
  const error = el('div', { class: 'error', role: 'alert' });
  const button = el('button', { class: 'primary', type: 'button', onclick: save }, 'Save');
  let saving = false;

  root.appendChild(el('label', { for: 'capture-text' }, 'Capture'));
  root.appendChild(input);
  root.appendChild(el('div', { class: 'capture-actions' }, status, button));
  root.appendChild(error);

  async function save() {
    const text = input.value;
    if (saving || !text.trim()) return;
    saving = true;
    button.disabled = true;
    error.textContent = '';
    status.textContent = 'Saving…';
    try {
      const res = await api.post('/api/entries', { text, source: 'pc' });
      input.value = '';  // 201: the log has it, so it is safe to clear
      status.textContent = 'Saved · filing…';
      onSaved(res.entry_id, text);
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
