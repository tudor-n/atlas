// First-run calibration wizard: areas, projects, courses, people, quiet hours + backup, review.
// State survives a reload in localStorage (best effort) and is cleared after a successful Finish.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
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

  const bar = el('ol', { class: 'wizard-steps', 'aria-label': 'Setup steps' });
  const body = el('div', {});
  const stepErr = el('div', { role: 'alert' });
  const nav = el('div', { class: 'wizard-nav' });
  container.appendChild(el('div', {}, el('h1', {}, 'Set up BrainHub'),
    el('p', { class: 'muted' }, 'A few minutes now means your notes land in the right place. You can change everything later in Settings.'),
    bar, body, stepErr, nav));

  // Step 5 inputs
  const qFrom = el('input', { type: 'time', value: st.quiet_hours[0], oninput: () => { st.quiet_hours[0] = qFrom.value; save(); } });
  const qTo = el('input', { type: 'time', value: st.quiet_hours[1], oninput: () => { st.quiet_hours[1] = qTo.value; save(); } });
  const remote = el('input', { type: 'text', value: st.backup_remote, spellcheck: 'false', placeholder: 'https://github.com/you/brainhub-backup.git',
    oninput: () => { st.backup_remote = remote.value; save(); } });
  const prefsBox = el('div', {},
    el('p', { class: 'muted' }, 'Quiet hours are stored now and used later, when BrainHub starts sending reminders.'),
    el('div', { class: 'edit-grid' },
      el('label', { class: 'field' }, 'Quiet from', qFrom), el('label', { class: 'field' }, 'Until', qTo)),
    el('h3', {}, 'Backup (optional)'),
    el('p', { class: 'muted' }, 'To back up your brain to GitHub, create an empty private repo first, then paste its URL here. Leave it blank to skip.'),
    el('label', { class: 'field' }, 'Private GitHub repo URL', remote));

  const sections = [
    ['Areas are the top-level folders. The four defaults stay; add your own or rename any.', editors.areas.node],
    ['Projects get their own folder. Mark the one that is your company, if any. Add the code folders on this PC; the other OS\'s column can wait until you are on it.', editors.projects.node],
    ['The courses you are taking at uni.', editors.courses.node],
    ['People you often write about, and the projects they belong to.', editors.people.node],
  ];

  const errList = (list) => list.map((m) => el('div', { class: 'error' }, m));

  function showServerErrors() {
    clear(stepErr);
    const relevant = st.step === STEPS.length - 1 ? serverErrors : serverErrors.filter((m) => stepFor(m) === st.step);
    stepErr.appendChild(el('div', {}, errList(relevant)));
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
    STEPS.slice(0, 4).forEach((title, i) => {
      for (const msg of [errs.areas, errs.projects, errs.courses, errs.people][i]) {
        problems.push(el('div', { class: 'error' }, `${title}: ${msg} `,
          el('button', { type: 'button', class: 'linkish', onclick: () => go(i) }, 'Fix')));
      }
    });
    if (quietError()) problems.push(el('div', { class: 'error' }, `${STEPS[4]}: ${quietError()} `,
      el('button', { type: 'button', class: 'linkish', onclick: () => go(4) }, 'Fix')));
    return el('div', {},
      el('dl', { class: 'review-list' },
        el('dt', {}, 'Areas'), el('dd', {}, list(m.areas.map((a) => a.name))),
        el('dt', {}, 'Projects'), el('dd', {}, list(m.projects.map((p) => p.name + (p.is_company ? ' (company)' : '')))),
        el('dt', {}, 'Uni courses'), el('dd', {}, list(m.courses.map((c) => c.name))),
        el('dt', {}, 'People'), el('dd', {}, list(m.people.map((p) => p.name +
          (p.projects.length ? ` (${p.projects.join(', ')})` : '')))),
        el('dt', {}, 'Quiet hours'), el('dd', {}, `${st.quiet_hours[0]} to ${st.quiet_hours[1]}`),
        el('dt', {}, 'Backup'), el('dd', {}, st.backup_remote.trim() || 'not set')),
      problems);
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
      if (st.step === 4) stepErr.appendChild(el('div', {}, errList(errs)));
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

  function draw() {
    clear(bar);
    STEPS.forEach((t, i) => bar.appendChild(el('li', { 'aria-current': i === st.step ? 'step' : null, class: i === st.step ? 'on' : '' }, t)));
    clear(body);
    clear(nav);
    clear(stepErr);
    if (done) {
      clear(bar);
      body.appendChild(el('p', { class: 'banner' }, done));
      nav.appendChild(el('button', { type: 'button', class: 'primary', onclick: () => { location.hash = '#/today'; } }, 'Open Today'));
      return;
    }
    body.appendChild(el('h2', {}, `Step ${st.step + 1} of ${STEPS.length}: ${STEPS[st.step]}`));
    if (st.step < 4) {
      const [hint, node] = sections[st.step];
      body.appendChild(el('p', { class: 'muted' }, hint));
      if (st.step === 3) editors.people.refresh();
      body.appendChild(node);
    } else if (st.step === 4) {
      body.appendChild(prefsBox);
    } else {
      body.appendChild(reviewNode());
    }
    nav.appendChild(el('button', { type: 'button', disabled: st.step === 0 || finishing, onclick: () => go(st.step - 1) }, 'Back'));
    if (st.step < STEPS.length - 1) {
      nav.appendChild(el('button', { type: 'button', class: 'primary', onclick: next }, 'Next'));
    } else {
      nav.appendChild(el('button', { type: 'button', class: 'primary', disabled: finishing, onclick: finish },
        finishing ? 'Setting up...' : 'Finish'));
    }
    showServerErrors();
  }

  draw();
}
