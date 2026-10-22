// Settings → "Claude Code sync": the last claude-sync run on this OS (a quiet note) and the
// settings keys that stay per OS. Text goes in via el() only.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { parseKeys, syncNotes } from './claude-sync-model.js';

export async function renderClaudeSync(box, isDead) {
  const msg = el('div', { role: 'alert' });

  function say(text, ok) {
    clear(msg);
    if (text) msg.appendChild(el('div', { class: ok ? 'status' : 'error' }, text));
  }

  function draw(view) {
    clear(box);
    const keys = view.per_os_keys || { default: [], custom: null };
    const editable = Array.isArray(keys.custom);
    const input = el('input', { type: 'text', value: editable ? keys.custom.join(', ') : '', spellcheck: 'false',
      disabled: !editable, placeholder: 'e.g. theme, outputStyle', 'aria-label': 'Extra per-OS keys' });
    const save = el('button', { type: 'button', class: 'primary', disabled: !editable, onclick: async () => {
      save.disabled = true;
      say('');
      try {
        const next = await api.put('/api/claude-sync/per-os-keys', { keys: parseKeys(input.value) });
        if (!isDead()) { draw(next); say('Saved. The next sync applies it.', true); }
      } catch (e) {
        if (!isDead()) { save.disabled = false; say(e.message); }
      }
    } }, 'Save per-OS keys');
    box.appendChild(el('div', {},
      syncNotes(view).map((n) => el('p', { class: n.problem ? 'error' : 'quiet-note' }, n.text)),
      el('h3', {}, 'Settings kept per OS'),
      el('p', { class: 'muted' }, 'Every other key in Claude Code\'s settings.json is the same on both OSes. Always per OS: ',
        keys.default.join(', '), ', and any key whose value holds a folder path.'),
      el('label', { class: 'field' }, 'Extra per-OS keys', input),
      editable ? null : el('div', { class: 'meta' }, "The shared store isn't available, so this list can't be changed now."),
      el('div', { class: 'actions' }, save),
      msg));
  }

  try {
    const view = await api.get('/api/claude-sync');
    if (!isDead()) draw(view);
  } catch (e) {
    if (!isDead()) clear(box).appendChild(el('div', { class: 'error', role: 'alert' }, `Couldn't read the Claude Code sync status: ${e.message}`));
  }
}
