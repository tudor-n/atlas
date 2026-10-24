// Settings → "Claude Code sync": the last claude-sync run on this OS (quiet notes) and the settings
// keys that stay per OS. `ui` is Settings' section context (action bar, rail dot). Text via el() only.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { button } from '../components/ui.js';
import { parseKeys, syncNotes } from './claude-sync-model.js';
import { syncDot } from './settings-model.js';
import { prefRow, groupHead, intro, failLine, value } from './settings-sections.js';

export async function renderClaudeSync(box, isDead, ui) {
  const { bar, setDot = () => {} } = ui;
  const say = (text, ok) => bar.say(text, ok);

  function draw(view) {
    clear(box);
    const notes = syncNotes(view);
    setDot('sync', syncDot(view, notes));
    const keys = view.per_os_keys || { default: [], custom: null };
    const editable = Array.isArray(keys.custom);
    const input = el('input', { type: 'text', id: 'sync-keys', class: 'm wide', value: editable ? keys.custom.join(', ') : '', spellcheck: 'false',
      disabled: !editable, placeholder: 'e.g. theme, outputStyle', 'aria-label': 'Extra per-OS keys' });
    const save = button('SAVE', { primary: true, disabled: !editable, onclick: async () => {
      save.disabled = true;
      say('');
      try {
        const next = await api.put('/api/claude-sync/per-os-keys', { keys: parseKeys(input.value) });
        if (!isDead()) { draw(next); say('Saved. The next sync applies it.', true); }
      } catch (e) {
        if (!isDead()) { save.disabled = false; say(e.message); }
      }
    } });
    box.append(
      intro('claude-sync keeps Claude Code the same on both OSes: memory, skills and settings, through the shared store.'),
      el('section', { class: 'st-grp' }, groupHead('LAST RUN'),
        el('div', { class: 'st-notes' }, notes.map((n) => el('p', { class: n.problem ? 'st-note amb' : 'st-note' },
          n.problem ? el('span', { class: 'sq' }) : null, n.text)))),
      el('section', { class: 'st-grp' }, groupHead('SETTINGS KEPT PER OS'), el('div', { class: 'pfs' },
        prefRow('ALWAYS PER OS', value(keys.default.join(', '), 'm'),
          { hint: "Every other key in Claude Code's settings.json is the same on both OSes. Any key whose value holds a folder path stays per OS too." }),
        prefRow('EXTRA PER-OS KEYS', input, { forId: 'sync-keys',
          hint: editable ? 'Separated by commas.' : "The shared store isn't available, so this list can't be changed now." }))));
    bar.set(save);
  }

  try {
    const view = await api.get('/api/claude-sync');
    if (!isDead()) draw(view);
  } catch (e) {
    if (!isDead()) clear(box).appendChild(failLine(`Couldn't read the Claude Code sync status: ${e.message}`));
  }
}
