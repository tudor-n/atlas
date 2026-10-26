// Settings → Voice (phase 4 design §7.1, phase 4.5 §4): the download, the switches, which events
// speak, the blend (Atlas's voice by default), speed, volume, Play sample, pause, and the quote file.
// `ui` is Settings' section context: its action bar (SAVE and the status line) and the rail's dot.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { button, switchEl, meter, selectBox } from '../components/ui.js';
import { EVENT_LABELS, blendRows, readBlend, voiceName, statusLine, mb, workoutWords } from './voice-model.js';
import { voiceDot } from './settings-model.js';
import { prefRow, groupHead, intro, failLine } from './settings-sections.js';

const SEGMENTS = 20;

export async function renderVoiceSettings(box, isDead, ui) {
  const { bar, setDot = () => {} } = ui;
  let v = null;
  let s = null;
  // The status line and the download bar are kept apart from the form, so a progress tick or a
  // status change elsewhere updates them without redrawing (and losing) unsaved edits.
  const statusDot = el('span', { class: 'dot', 'aria-hidden': 'true' });
  const statusText = el('span', {});
  const status = el('p', { class: 'st-status', 'aria-live': 'polite' }, statusDot, statusText);
  const meterBox = el('div', { class: 'st-meter' });
  const form = el('div', {});
  box.append(status, meterBox, form);
  const say = (text, ok) => bar.say(text, ok);

  function drawStatus() {
    const line = statusLine(v);
    statusText.textContent = line.text;
    status.className = line.kind === 'error' ? 'st-status err' : 'st-status';
    const k = voiceDot(v);
    statusDot.className = k ? `dot ${k}` : 'dot';
    setDot('voice', k);
    clear(meterBox);
    const d = (v && v.download) || {};
    if (d.running) {
      const filled = d.total ? (d.done / d.total) * SEGMENTS : 0;
      meterBox.appendChild(meter(SEGMENTS, filled, { label: line.text }));
    }
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
      if (!isDead()) { clear(form).appendChild(failLine(`Couldn't read the voice settings: ${e.message}`)); bar.set(); }
    }
  }

  function draw() {
    clear(form);
    drawStatus();
    if (!v.available) { bar.set(); return; }

    // -- the one-time download
    const d = v.download || {};
    if (!v.installed && !d.running) {
      form.appendChild(el('div', { class: 'st-dl' }, button(`DOWNLOAD THE VOICE (${mb(v.missing_bytes)})`, { onclick: () => act(
        () => api.post('/api/voice/download', {}), null) })));
    }

    // -- switches and events
    // Preferences take the small white switch: green is kept for what is live (the status dot above).
    const sw = (on, id, title) => { const n = switchEl({ on: !!on, small: true, title }); n.id = id; return n; };
    const on = sw(s.voice_enabled, 'voice-on', 'Voice on');
    const sass = sw(s.voice_sass, 'voice-sass', 'Add a quip after the information');
    const sassRb = sw(s.voice_sass_readback, 'voice-sass-rb', 'Quips on tablet readbacks too');
    const events = EVENT_LABELS.map(([key, text]) => ({ key, text, box: sw(s.voice_events[key], `voice-ev-${key}`, text) }));
    const finishMin = el('input', { type: 'number', id: 'voice-finish', class: 'm num', min: '0', max: '60', step: 'any', value: String(+(s.voice_finish_min_s / 60).toFixed(2)) });

    // -- the blend
    const rows = blendRows(s.voice_blend);
    const rowsBox = el('div', { class: 'blend' });
    const editors = [];
    function addRow(id, pct) {
      const n = editors.length + 1;
      const select = el('select', { 'aria-label': `Voice ${n}` }, el('option', { value: '' }, '(none)'),
        v.voices.map((x) => el('option', { value: x, selected: x === id || null }, voiceName(x))));
      const share = el('input', { type: 'number', class: 'm pct', min: '1', max: '100', step: '1', value: String(pct), 'aria-label': `Voice ${n}: share in %` });
      editors.push({ select, share });
      rowsBox.appendChild(el('div', { class: 'pair' }, selectBox(select), share, el('span', { class: 'u' }, '%')));
    }
    rows.forEach((r) => addRow(r.id, r.pct));
    const addBtn = button('ADD A VOICE', { size: 40, onclick: () => { if (editors.length < 3) addRow('', 20); } });

    // Workout words: comma-separated; a reminder with one of them says "Workout time." and gets a workout quip.
    const workout = el('input', { type: 'text', id: 'voice-workout', class: 'm', autocomplete: 'off',
      value: (s.voice_workout_words || []).join(', ') });
    const speed = el('input', { type: 'range', id: 'voice-speed', min: '0.5', max: '2', step: '0.05', value: String(s.voice_speed) });
    const speedOut = el('span', { class: 'pv m out' }, `${s.voice_speed}×`);
    speed.oninput = () => { speedOut.textContent = `${speed.value}×`; };
    const volume = el('input', { type: 'range', id: 'voice-volume', min: '0', max: '100', step: '1', value: String(s.voice_volume) });
    const volOut = el('span', { class: 'pv m out' }, `${s.voice_volume} %`);
    volume.oninput = () => { volOut.textContent = `${volume.value} %`; };
    const boost = sw(s.voice_boost !== false, 'voice-boost', 'Even loudness');
    const duckVal = Number.isFinite(s.voice_duck_to) ? s.voice_duck_to : 100;
    const duck = el('input', { type: 'range', id: 'voice-duck', min: '0', max: '100', step: '5', value: String(duckVal) });
    const duckText = (v) => (Number(v) >= 100 ? 'unchanged' : `${v} %`);
    const duckOut = el('span', { class: 'pv m out' }, duckText(duckVal));
    duck.oninput = () => { duckOut.textContent = duckText(duck.value); };
    const model = el('select', { id: 'voice-model' },
      el('option', { value: 'int8', selected: s.voice_model === 'int8' || null }, `Standard (${mb(v.sizes.int8)})`),
      el('option', { value: 'fp32', selected: s.voice_model === 'fp32' || null }, `Higher quality (${mb(v.sizes.fp32)})`));

    // What the form says now, as settings; throws on a bad blend.
    function read() {
      const b = readBlend(editors.map((e) => ({ id: e.select.value, pct: e.share.value })));
      if (b.error) throw new Error(b.error);
      const mins = Number(finishMin.value);
      if (finishMin.value.trim() === '' || !Number.isFinite(mins) || mins < 0 || mins > 60) throw new Error('Long run: minutes, 0 to 60.');
      const words = workoutWords(workout.value);
      if (words.error) throw new Error(words.error);
      return {
        voice_enabled: on.isOn(), voice_sass: sass.isOn(), voice_sass_readback: sassRb.isOn(),
        voice_events: Object.fromEntries(events.map((e) => [e.key, e.box.isOn()])),
        voice_finish_min_s: Math.round(mins * 60), voice_workout_words: words.list, voice_blend: b.blend, voice_speed: Number(speed.value),
        voice_volume: Number(volume.value), voice_boost: boost.isOn(), voice_duck_to: Number(duck.value),
        voice_model: model.value,
      };
    }
    const save = () => act(() => api.put('/api/settings', read()), 'Saved.');
    const sample = () => act(async () => {
      const f = read();
      await api.post('/api/voice/sample', { voice_blend: f.voice_blend, voice_speed: f.voice_speed,
        voice_volume: f.voice_volume, voice_boost: f.voice_boost, voice_duck_to: f.voice_duck_to, voice_model: f.voice_model });
    }, 'Playing a sample…');

    const pause = v.paused_until
      ? button('RESUME', { size: 40, onclick: () => act(() => api.post('/api/voice/pause', { resume: true }), 'Resumed.') })
      : [button('PAUSE FOR AN HOUR', { size: 40, onclick: () => act(() => api.post('/api/voice/pause', { minutes: 60 }), 'Paused for an hour.') }),
        button('PAUSE UNTIL MORNING', { size: 40, onclick: () => act(() => api.post('/api/voice/pause', { until: 'morning' }), 'Paused until morning.') })];

    const swRow = (text, node, hint) => prefRow(text, node, { forId: node.id, hint });
    form.append(
      intro('ATLAS speaks through the PC speakers when an agent needs you or finishes a long run, when a reminder is due, '
        + 'to read back what you type on the tablet, and to reply when you write something down or ask on the ATLAS screen. '
        + 'Nothing is spoken during quiet hours (see ',
      el('a', { href: '#/settings/preferences' }, 'Preferences'), ').'),
      el('div', { class: 'pfs' }, swRow('VOICE ON', on)),
      el('section', { class: 'st-grp' }, groupHead('SPEAK WHEN'), el('div', { class: 'pfs' },
        events.map((e) => prefRow(e.text, e.box, { forId: e.box.id, cls: 'pf-t' })),
        prefRow('WORKOUT WORDS', workout, { forId: 'voice-workout',
          hint: 'A reminder with one of these (comma-separated, whole words) says "Workout time." and gets a workout quip.' }),
        prefRow('A LONG RUN IS AT LEAST', el('div', { class: 'pair' }, finishMin, el('span', { class: 'u' }, 'MINUTES')),
          { forId: 'voice-finish', hint: 'How long an agent must have run before "finished" is spoken.' }))),
      el('section', { class: 'st-grp' }, groupHead('SASS'), el('div', { class: 'pfs' },
        prefRow('Add a quip after the information', sass, { forId: sass.id, cls: 'pf-t' }),
        prefRow('Quips on tablet readbacks too', sassRb, { forId: sassRb.id, cls: 'pf-t' }),
        prefRow('QUOTES', button('EDIT QUOTES', { size: 40, onclick: () => act(() => api.post('/api/voice/edit-quotes', {}), 'Opened the quote file.') }),
          { hint: 'Opens the quote file the quips come from.' }),
        v.quote_problems.length
          ? el('div', { class: 'st-warns', role: 'status' },
            el('p', { class: 'st-warn' }, el('span', { class: 'sq' }), 'The quote file has problems (those lines are skipped):'),
            el('ul', {}, v.quote_problems.map((p) => el('li', {}, p))))
          : null)),
      el('section', { class: 'st-grp' }, groupHead('VOICE'), el('div', { class: 'pfs' },
        prefRow('BLEND', el('div', {}, rowsBox, addBtn), { hint: 'Up to three voices, mixed by share.', cls: 'pf-top' }),
        prefRow('SPEED', el('div', { class: 'pair' }, speed, speedOut), { forId: 'voice-speed' }),
        prefRow('VOLUME', el('div', { class: 'pair' }, volume, volOut), { forId: 'voice-volume' }),
        prefRow('Even loudness', boost, { forId: boost.id, cls: 'pf-t',
          hint: 'Brings every line up to a steady, clear loudness, so it carries over music.' }),
        prefRow('OTHER SOUNDS WHILE SPEAKING', el('div', { class: 'pair' }, duck, duckOut), { forId: 'voice-duck',
          hint: 'Music and other apps play at this share of their level while ATLAS speaks, then come back: 50 % is a little quieter (−6 dB), 100 % leaves them alone. Linux.' }),
        prefRow('MODEL', selectBox(model), { forId: 'voice-model' }))),
      el('section', { class: 'st-grp' }, groupHead('LISTEN'), el('div', { class: 'pfs' },
        prefRow('SAMPLE', el('div', { class: 'pair' },
          button('PLAY SAMPLE', { size: 40, onclick: sample, disabled: !v.installed }),
          button('STOP', { size: 40, onclick: () => act(() => api.post('/api/voice/stop', {}), null) })),
        { hint: 'Plays the voice as set above, saved or not.' }),
        prefRow('PAUSE', el('div', { class: 'pair' }, pause),
          { hint: v.paused_until ? 'Paused now.' : 'Silences every line for a while, then the voice comes back on its own.' }))));
    bar.set(button('SAVE', { primary: true, onclick: save }));
  }

  await load();
  // A download's progress, or a change made elsewhere (the widget, the tray, another tab): the
  // form is redrawn only when its shape or the saved voice settings change, so unsaved edits here
  // survive a mere status change. A failed first load tries again on the next event.
  const shape = (x, set) => JSON.stringify([x.available, x.installed, x.enabled, x.paused_until, x.quote_problems,
    (x.download || {}).running, Object.entries(set || {}).filter(([k]) => k.startsWith('voice_'))]);
  return api.onEvent('voice.changed', async ({ data }) => {
    if (isDead()) return;
    if (!v || !s) { await load(); return; }
    if (data && data.download) {
      const next = { ...v, download: data.download };
      if (shape(next, s) !== shape(v, s)) { await load(); return; }  // a download started or ended
      v = next;
      drawStatus();
      return;
    }
    let nv, ns;
    try { [nv, ns] = await Promise.all([api.get('/api/voice'), api.get('/api/settings')]); } catch (_) { return; }
    if (isDead()) return;
    if (shape(nv, ns) !== shape(v, s)) { await load(); return; }
    v = nv;
    drawStatus();
  });
}
