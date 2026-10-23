// Settings → Voice (phase 4 design §7.1): the download, the switches, which events speak, the
// blend (Atlas's voice by default), speed, volume, Play sample, pause, and the quote file.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { EVENT_LABELS, blendRows, readBlend, voiceName, statusLine, mb } from './voice-model.js';

export async function renderVoiceSettings(box, isDead) {
  const msg = el('div', { role: 'alert' });
  let v = null;
  let s = null;

  function say(text, ok) {
    clear(msg);
    if (text) msg.appendChild(el('div', { class: ok ? 'status' : 'error' }, text));
  }

  async function act(fn, done) {
    say('');
    try {
      await fn();
      if (!isDead()) { if (done) say(done, true); await load(); }
    } catch (e) { if (!isDead()) say(e.message); }
  }

  async function load() {
    try {
      [v, s] = await Promise.all([api.get('/api/voice'), api.get('/api/settings')]);
      if (!isDead()) draw();
    } catch (e) {
      if (!isDead()) clear(box).appendChild(el('div', { class: 'error', role: 'alert' }, `Couldn't read the voice settings: ${e.message}`));
    }
  }

  function draw() {
    clear(box);
    const line = statusLine(v);
    box.appendChild(el('p', { class: line.kind }, line.text));
    if (!v.available) return;

    // -- the one-time download
    const d = v.download || {};
    if (!v.installed && !d.running) {
      box.appendChild(el('div', { class: 'actions' }, el('button', { type: 'button', class: 'primary', onclick: () => act(
        () => api.post('/api/voice/download', {}), null) }, `Download the voice (${mb(v.missing_bytes)})`)));
    }

    // -- switches and events
    const on = el('input', { type: 'checkbox', id: 'voice-on', checked: s.voice_enabled || null });
    const sass = el('input', { type: 'checkbox', id: 'voice-sass', checked: s.voice_sass || null });
    const sassRb = el('input', { type: 'checkbox', id: 'voice-sass-rb', checked: s.voice_sass_readback || null });
    const events = EVENT_LABELS.map(([key, text]) => ({ key, box: el('input', { type: 'checkbox', id: `voice-ev-${key}`, checked: s.voice_events[key] || null }), text }));
    const finishMin = el('input', { type: 'number', id: 'voice-finish', min: '0', max: '60', step: '1', value: String(Math.round(s.voice_finish_min_s / 60)) });

    // -- the blend
    const rows = blendRows(s.voice_blend);
    const rowsBox = el('div', {});
    const editors = [];
    function addRow(id, pct) {
      const select = el('select', { 'aria-label': 'Voice' }, el('option', { value: '' }, '(none)'),
        v.voices.map((x) => el('option', { value: x, selected: x === id || null }, voiceName(x))));
      const share = el('input', { type: 'number', min: '1', max: '100', step: '1', value: String(pct), 'aria-label': 'Share in %' });
      const row = el('div', { class: 'voice-line' }, select, share, el('span', { class: 'meta' }, '%'));
      editors.push({ select, share });
      rowsBox.appendChild(row);
    }
    rows.forEach((r) => addRow(r.id, r.pct));
    const addBtn = el('button', { type: 'button', onclick: () => { if (editors.length < 3) addRow('', 20); } }, 'Add a voice');

    const speed = el('input', { type: 'range', id: 'voice-speed', min: '0.5', max: '2', step: '0.05', value: String(s.voice_speed) });
    const speedOut = el('span', { class: 'meta' }, `${s.voice_speed}×`);
    speed.oninput = () => { speedOut.textContent = `${speed.value}×`; };
    const volume = el('input', { type: 'range', id: 'voice-volume', min: '0', max: '100', step: '5', value: String(s.voice_volume) });
    const volOut = el('span', { class: 'meta' }, `${s.voice_volume} %`);
    volume.oninput = () => { volOut.textContent = `${volume.value} %`; };
    const model = el('select', { id: 'voice-model' },
      el('option', { value: 'int8', selected: s.voice_model === 'int8' || null }, `Standard (${mb(v.sizes.int8)})`),
      el('option', { value: 'fp32', selected: s.voice_model === 'fp32' || null }, `Higher quality (${mb(v.sizes.fp32)})`));

    // What the form says now, as settings; throws on a bad blend.
    function read() {
      const b = readBlend(editors.map((e) => ({ id: e.select.value, pct: e.share.value })));
      if (b.error) throw new Error(b.error);
      const mins = Number(finishMin.value);
      if (!Number.isInteger(mins) || mins < 0 || mins > 60) throw new Error('Long run: a whole number of minutes, 0 to 60.');
      return {
        voice_enabled: on.checked, voice_sass: sass.checked, voice_sass_readback: sassRb.checked,
        voice_events: Object.fromEntries(events.map((e) => [e.key, e.box.checked])),
        voice_finish_min_s: mins * 60, voice_blend: b.blend, voice_speed: Number(speed.value),
        voice_volume: Number(volume.value), voice_model: model.value,
      };
    }
    const save = () => act(() => api.put('/api/settings', read()), 'Saved.');
    const sample = () => act(async () => {
      const f = read();
      await api.post('/api/voice/sample', { voice_blend: f.voice_blend, voice_speed: f.voice_speed,
        voice_volume: f.voice_volume, voice_model: f.voice_model });
    }, 'Playing a sample…');

    const pause = v.paused_until
      ? el('button', { type: 'button', onclick: () => act(() => api.post('/api/voice/pause', { resume: true }), 'Resumed.') }, 'Resume')
      : [el('button', { type: 'button', onclick: () => act(() => api.post('/api/voice/pause', { minutes: 60 }), 'Paused for an hour.') }, 'Pause for an hour'),
        el('button', { type: 'button', onclick: () => act(() => api.post('/api/voice/pause', { until: 'morning' }), 'Paused until morning.') }, 'Pause until morning')];

    box.appendChild(el('div', {},
      el('p', { class: 'muted' }, 'BrainHub speaks through the PC speakers when an agent needs you or finishes a long run, when a reminder is due, '
        + 'and to read back what you type on the tablet. Nothing is spoken during quiet hours (see Preferences).'),
      el('div', { class: 'pref' }, el('label', { class: 'check', for: 'voice-on' }, on, 'Voice on')),
      el('fieldset', {}, el('legend', {}, 'Speak when'),
        events.map((e) => el('label', { class: 'check', for: e.box.id }, e.box, e.text)),
        el('label', { class: 'voice-line', for: 'voice-finish' }, el('span', {}, 'A long run is at least'), finishMin, 'minutes')),
      el('fieldset', {}, el('legend', {}, 'Sass'),
        el('label', { class: 'check', for: 'voice-sass' }, sass, 'Add a quip after the information'),
        el('label', { class: 'check', for: 'voice-sass-rb' }, sassRb, 'Quips on tablet readbacks too'),
        el('div', { class: 'actions' }, el('button', { type: 'button', onclick: () => act(() => api.post('/api/voice/edit-quotes', {}), 'Opened the quote file.') }, 'Edit quotes'))),
      el('fieldset', {}, el('legend', {}, 'Voice'), rowsBox, el('div', { class: 'actions' }, addBtn),
        el('label', { class: 'voice-line', for: 'voice-speed' }, el('span', {}, 'Speed'), speed, speedOut),
        el('label', { class: 'voice-line', for: 'voice-volume' }, el('span', {}, 'Volume'), volume, volOut),
        el('label', { class: 'voice-line', for: 'voice-model' }, el('span', {}, 'Model'), model)),
      el('div', { class: 'actions' },
        el('button', { type: 'button', class: 'primary', onclick: save }, 'Save'),
        el('button', { type: 'button', onclick: sample, disabled: !v.installed || null }, 'Play sample'),
        el('button', { type: 'button', onclick: () => act(() => api.post('/api/voice/stop', {}), null) }, 'Stop'),
        pause),
      v.quote_problems.length
        ? el('div', { class: 'quiet-note' }, 'The quote file has problems (those lines are skipped):', el('ul', {}, v.quote_problems.map((p) => el('li', {}, p))))
        : null,
      msg));
  }

  await load();
  // A download's progress, or a change made elsewhere (the widget, the tray, another Settings
  // save): redraw the form only when its shape changes, so unsaved edits here survive.
  return api.onEvent('voice.changed', async ({ data }) => {
    if (isDead() || !v) return;
    if (data && data.download) {
      v.download = data.download;
      if (data.download.running) { box.firstChild.textContent = statusLine(v).text; return; }
    }
    let next;
    try { next = await api.get('/api/voice'); } catch (_) { return; }
    if (isDead()) return;
    const shape = (x) => JSON.stringify([x.available, x.installed, x.enabled, x.paused_until, x.quote_problems, (x.download || {}).running]);
    if (shape(next) !== shape(v)) { await load(); return; }
    v = next;
    const line = statusLine(v);
    box.firstChild.textContent = line.text;
    box.firstChild.className = line.kind;
  });
}
