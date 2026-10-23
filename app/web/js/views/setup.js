// First-run calibration wizard: areas, projects, courses, people, quiet hours + backup, review.
// State survives a reload in localStorage (best effort) and is cleared after a successful Finish.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { button, notice } from '../components/ui.js';
import { prefRow } from './settings-sections.js';
import { stepState } from './settings-model.js';
import { createMapEditors, defaultMap, normalizeMap, hasErrors, validateMap } from '../components/map-editor.js';

const KEY = 'brainhub.setup.v1';
const STEPS = ['Areas', 'Projects', 'Uni courses', 'People', 'Quiet hours and backup', 'Review'];
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

function loadSaved() {
  try {
    const d = JSON.parse(localStorage.getItem(KEY));
    if (!d || typeof d !== 'object') return null;
    const step = Number.isInteger(d.step) && d.step >= 0 && d.step < STEPS.length ? d.step : 0;
    const qh = Array.isArray(d.quiet_hours) && d.quiet_hours.length === 2 && d.quiet_hours.every((x) => typeof x === 'string')
      ? d.quiet_hours : null;
    return { step, map: normalizeMap(d.map), backup_remote: typeof d.backup_remote === 'string' ? d.backup_remote : '', quiet_hours: qh };
  } catch (_) {
    return null;
  }
}
function persist(st) {
  try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (_) { /* private mode or full: the wizard still works */ }
}
function forget() {
  try { localStorage.removeItem(KEY); } catch (_) { /* nothing to clear */ }
}

// Which step a server validation message belongs to (messages come from brainmap.validate_map).
function stepFor(msg) {
  if (/^at most one project/.test(msg)) return 1;
  const word = /^(?:duplicate )?(area|project|course|person)/.exec(msg);
  return word ? { area: 0, project: 1, course: 2, person: 3 }[word[1]] : null;
}

export async function render(container) {
  let server = {};
  try { server = await api.get('/api/settings'); } catch (_) { /* defaults below */ }
  let os = null;  // labels this PC's folder column
  try { os = (await api.get('/api/state')).os; } catch (_) { /* both columns stay unlabelled */ }
  const serverQuiet = Array.isArray(server.quiet_hours) ? server.quiet_hours : ['23:00', '08:00'];
  const saved = loadSaved();
  const st = saved
    ? { ...saved, quiet_hours: saved.quiet_hours || [...serverQuiet] }  // copies: the inputs mutate st.quiet_hours
    : { step: 0, map: defaultMap(), backup_remote: '', quiet_hours: [...serverQuiet] };
  let serverErrors = [];  // from a rejected Finish; cleared on the next edit
  let finishing = false;
  let done = null;  // message after a partial success

  const save = () => persist(st);
  const editors = createMapEditors(st.map, {
    onChange: () => { save(); if (serverErrors.length) { serverErrors = []; showServerErrors(); } },
    os,
  });

  const bar = el('ol', { class: 'sp-steps', 'aria-label': 'Setup steps' });
  const kicker = el('span', { class: 'lb' });
  const title = el('h1', {});
  const hint = el('p', { class: 'sp-hint' });
  const body = el('div', { class: 'sp-body' });
  const stepErr = el('div', { class: 'sp-err', role: 'alert' });
  const back = button('BACK', { onclick: () => go(st.step - 1) });
  const fwd = button('NEXT', { primary: true });
  container.appendChild(el('div', { class: 'vw setup' },
    el('div', { class: 'sp-scroll' }, el('div', { class: 'sp-col' },
      el('div', { class: 'sp-intro' }, el('span', { class: 'lb' }, 'SET UP BRAINHUB'),
        el('p', { class: 'sp-lead' }, 'A few minutes now means your notes land in the right place. You can change everything later in Settings.')),
      bar,
      el('header', { class: 'sp-head' }, kicker, title, hint),
      body, stepErr)),
    el('div', { class: 'actbar sp-bar' }, el('div', { class: 'sp-col sp-nav' }, back, el('span', { class: 'grow' }), fwd))));
  fwd.addEventListener('click', () => {
    if (done) { location.hash = '#/today'; return; }
    if (st.step < STEPS.length - 1) next(); else finish();
  });

  // Step 5 inputs
  const qFrom = el('input', { type: 'time', id: 'sp-quiet', class: 'm', value: st.quiet_hours[0], 'aria-label': 'Quiet from', oninput: () => { st.quiet_hours[0] = qFrom.value; save(); } });
  const qTo = el('input', { type: 'time', class: 'm', value: st.quiet_hours[1], 'aria-label': 'Until', oninput: () => { st.quiet_hours[1] = qTo.value; save(); } });
  const remote = el('input', { type: 'text', id: 'sp-remote', class: 'm wide', value: st.backup_remote, spellcheck: 'false', placeholder: 'https://github.com/you/brainhub-backup.git',
    oninput: () => { st.backup_remote = remote.value; save(); } });
  const prefsBox = el('div', { class: 'pfs' },
    prefRow('QUIET HOURS', el('div', { class: 'pair' }, qFrom, el('span', { class: 'lb' }, 'TO'), qTo),
      { forId: 'sp-quiet', hint: 'Quiet hours are stored now and used later, when BrainHub starts sending reminders.' }),
    prefRow('BACKUP (OPTIONAL)', remote, { forId: 'sp-remote',
      hint: 'To back up your brain to GitHub, create an empty private repo first, then paste its URL here. Leave it blank to skip.' }));

  const sections = [
    ['Areas are the top-level folders. The four defaults stay; add your own or rename any.', editors.areas.node],
    ['Projects get their own folder. Mark the one that is your company, if any. Add the code folders on this PC; the other OS\'s column can wait until you are on it.', editors.projects.node],
    ['The courses you are taking at uni.', editors.courses.node],
    ['People you often write about, and the projects they belong to.', editors.people.node],
  ];

  const errList = (list) => list.map((m) => el('p', { class: 'err' }, m));

  function showServerErrors() {
    clear(stepErr);
    const relevant = st.step === STEPS.length - 1 ? serverErrors : serverErrors.filter((m) => stepFor(m) === st.step);
    stepErr.append(...errList(relevant));
  }

  function quietError() {
    if (!HHMM.test(st.quiet_hours[0]) || !HHMM.test(st.quiet_hours[1])) return 'Pick both quiet-hours times.';
    return '';
  }

  function stepErrors(i) {
    const errs = validateMap(st.map);
    if (i <= 3) return [errs.areas, errs.projects, errs.courses, errs.people][i];
    if (i === 4) return quietError() ? [quietError()] : [];
    return [];
  }

  function reviewNode() {
    const m = st.map;
    const list = (items) => (items.length ? items.join(', ') : 'none');
    const errs = validateMap(m);
    const problems = [];
    const fix = (i) => button('FIX', { size: 40, onclick: () => go(i) });
    STEPS.slice(0, 4).forEach((title, i) => {
      for (const msg of [errs.areas, errs.projects, errs.courses, errs.people][i]) {
        problems.push(el('div', { class: 'sp-prob' }, el('p', { class: 'err' }, `${title}: ${msg}`), fix(i)));
      }
    });
    if (quietError()) problems.push(el('div', { class: 'sp-prob' }, el('p', { class: 'err' }, `${STEPS[4]}: ${quietError()}`), fix(4)));
    const val = (text, mono) => el('span', { class: mono ? 'pv m' : 'pv' }, text);
    const edit = (i) => button('EDIT', { size: 40, onclick: () => go(i) });
    const line = (lbl, v, i) => el('div', { class: 'pf sp-rv' }, el('div', { class: 'pfl' }, el('span', { class: 'lb' }, lbl)),
      el('div', { class: 'pfc' }, v), edit(i));
    return el('div', {},
      problems.length ? el('div', { class: 'sp-probs' }, problems) : null,
      el('div', { class: 'pfs' },
        line('AREAS', val(list(m.areas.map((a) => a.name))), 0),
        line('PROJECTS', val(list(m.projects.map((p) => p.name + (p.is_company ? ' (company)' : '')))), 1),
        line('UNI COURSES', val(list(m.courses.map((c) => c.name))), 2),
        line('PEOPLE', val(list(m.people.map((p) => p.name + (p.projects.length ? ` (${p.projects.join(', ')})` : '')))), 3),
        line('QUIET HOURS', val(`${st.quiet_hours[0]} to ${st.quiet_hours[1]}`, true), 4),
        line('BACKUP', val(st.backup_remote.trim() || 'not set', !!st.backup_remote.trim()), 4)));
  }

  function go(i) {
    st.step = i;
    save();
    draw();
  }

  async function next() {
    const errs = stepErrors(st.step);
    if (st.step <= 3) editors.validateAndShow();
    if (errs.length) {
      clear(stepErr);
      if (st.step === 4) stepErr.append(...errList(errs));
      return;
    }
    go(st.step + 1);
  }

  async function finish() {
    const errs = validateMap(st.map);
    if (hasErrors(errs) || quietError()) return;
    finishing = true;
    serverErrors = [];
    draw();
    const remoteUrl = st.backup_remote.trim();
    try {
      await api.post('/api/setup', { map: st.map, backup_remote: remoteUrl || null });
    } catch (e) {
      if (e.status === 409) {  // already set up (another tab, or an earlier try that got through)
        forget();
        location.hash = '#/today?notice=setup-done';
        return;
      }
      finishing = false;
      serverErrors = String(e.message).split('; ').filter(Boolean);
      draw();
      return;
    }
    // Setup is done and committed. Quiet hours are a separate settings call.
    forget();
    let note = null;
    if (JSON.stringify(st.quiet_hours) !== JSON.stringify(serverQuiet)) {
      try { await api.put('/api/settings', { quiet_hours: st.quiet_hours }); } catch (e) {
        note = `Your brain is set up, but the quiet hours weren't saved: ${e.message}. You can set them in Settings.`;
      }
    }
    finishing = false;
    if (note) { done = note; draw(); return; }
    location.hash = '#/today';
  }

  // The progress bar: one 4px segment and a label per step, done / current / to do.
  function drawSteps() {
    clear(bar);
    STEPS.forEach((t, i) => {
      const k = stepState(i, st.step);
      bar.appendChild(el('li', { class: k, 'aria-current': k === 'cur' ? 'step' : null },
        el('i', { 'aria-hidden': 'true' }), el('span', { class: 'sl' }, el('span', { class: 'm' }, String(i + 1)), ' ', t)));
    });
  }

  function draw() {
    clear(body);
    clear(stepErr);
    if (done) {
      clear(bar);
      kicker.textContent = 'ALMOST DONE';
      title.textContent = 'Your brain is set up';
      hint.textContent = '';
      body.appendChild(notice(done, { title: 'QUIET HOURS' }));
      back.hidden = true;
      fwd.textContent = 'OPEN TODAY';
      fwd.disabled = false;
      return;
    }
    drawSteps();
    kicker.textContent = `STEP ${st.step + 1} OF ${STEPS.length}`;
    title.textContent = STEPS[st.step];
    if (st.step < 4) {
      const [text, node] = sections[st.step];
      hint.textContent = text;
      if (st.step === 3) editors.people.refresh();
      body.appendChild(node);
    } else if (st.step === 4) {
      hint.textContent = 'When the voice stays silent, and where the brain is backed up.';
      body.appendChild(prefsBox);
    } else {
      hint.textContent = 'Check everything once. Finish creates the brain and its folders.';
      body.appendChild(reviewNode());
    }
    back.disabled = st.step === 0 || finishing;
    const last = st.step === STEPS.length - 1;
    fwd.textContent = last ? (finishing ? 'SETTING UP…' : 'FINISH') : 'NEXT';
    fwd.disabled = last && finishing;
    showServerErrors();
  }

  draw();
}
