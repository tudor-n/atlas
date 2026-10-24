// Pure model for the desktop widget (no DOM; drive.js is pure too), from GET /api/widget: the bar's
// summary, the NEEDS YOU list and the status strip (the widget redesign, 2026-10-24: one list of
// what needs you instead of eight tiles and their popups).
import { heldText } from './drive.js';
import { adoptLegacy } from './legacy-keys.js';

// How a row (or OPEN ATLAS, view null) opens the app. In the desktop shell the widget asks the shell
// for the main window (`open_main`, design §6); in a plain browser, or when that call fails, it
// posts /api/open, which opens the browser. `invoke` is the shell's `__TAURI__.core.invoke` or null
// (tauriInvoke below).
export function openPlan(view, invoke) {
  const v = view || null;
  return { invoke: typeof invoke === 'function' ? invoke : null, view: v, body: v ? { target: 'app', view: v } : { target: 'app' } };
}

// How long the widget waits for the shell's `open_main` before it falls back to POST /api/open.
export const OPEN_MAIN_TIMEOUT_MS = 3000;

// Race the shell's `open_main` call against a timeout: 'shell' when it resolved (the main window
// is showing), 'fallback' when it rejected (an older shell, or one that could not show the
// window) or did not answer within `ms` (a stuck main thread). Never rejects. `wait` is
// injectable for tests (default: setTimeout).
export async function openOutcome(call, ms = OPEN_MAIN_TIMEOUT_MS, wait = (t, f) => setTimeout(f, t)) {
  let timer;
  const timeout = new Promise((resolve) => { timer = wait(ms, () => resolve('fallback')); });
  let attempt;
  try {
    attempt = Promise.resolve(call()).then(() => 'shell', () => 'fallback');
  } catch (_) {
    attempt = Promise.resolve('fallback');
  }
  const outcome = await Promise.race([attempt, timeout]);
  if (timer !== undefined && typeof clearTimeout === 'function') clearTimeout(timer);
  return outcome;
}

// The shell's command bridge on a window-like object, or null (a browser, or a shell without it).
export function tauriInvoke(win) {
  try {
    const core = win && win.__TAURI__ && win.__TAURI__.core;
    return core && typeof core.invoke === 'function' ? core.invoke.bind(core) : null;
  } catch (_) {
    return null;
  }
}

const hhmm = (iso) => String(iso || '').slice(-5);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const hasUsed = (win) => !!win && win.used !== null && win.used !== undefined;

// The 5-hour window when it has a reading, else the 7-day one (the app's top bar uses it too).
export function usageWindow(w) {
  const u = w.usage || {};
  if (hasUsed(u.five_hour)) return { win: u.five_hour, label: '5h' };
  if (hasUsed(u.seven_day)) return { win: u.seven_day, label: '7d' };
  return null;
}

// Entries to review (the app's top bar uses it too).
export function reviewCount(w) {
  const r = w.review || {};
  // Entries, as Today's TO REVIEW counts them (older hubs: proposals + failed filings).
  return Number.isFinite(r.entries) ? r.entries : (r.proposals || 0) + (r.failed || 0);
}

// NEEDS YOU: everything that wants the owner, most urgent first. Each row: {key, text, meta, view,
// warn}. `view` is the app page a click opens (nav-model views). Calm rows (due today, a nudge)
// have warn false; the amber square marks the rest.
export function needsYou(w) {
  if (!w) return [];
  const rows = [];
  ((w.agents || {}).rows || []).forEach((r, i) => {
    if (r.status !== 'waiting') return;
    rows.push({ key: `agent:${i}`, text: [r.project_name || 'An agent', r.part].filter(Boolean).join(' · '),
      meta: 'waiting for you', view: 'agents', warn: true });
  });
  const n = reviewCount(w);
  const failed = (w.review || {}).failed || 0;
  if (n > 0) {
    rows.push({ key: 'review', text: `${plural(n, 'entry', 'entries')} to review`,
      meta: failed ? `${failed} failed` : 'confirm', view: 'today', warn: true });
  }
  const req = w.requests || 0;
  if (req > 0) {
    rows.push({ key: 'requests', text: plural(req, 'tablet request', 'tablet requests'), meta: 'answer on the tablet',
      view: 'tablet', warn: true });
  }
  const t = w.today || {};
  (t.top || []).forEach((d, i) => {
    const overdue = i < (t.overdue || 0);  // the server lists the overdue ones first
    rows.push({ key: `due:${i}`, text: d.text, meta: overdue ? `overdue · ${d.due || ''}` : 'due today', view: 'today', warn: overdue });
  });
  const u = w.usage || {};
  for (const [win, label] of [[u.five_hour, '5-hour'], [u.seven_day, 'weekly']]) {
    if (hasUsed(win) && win.warn) {
      rows.push({ key: `usage:${label}`, text: `${Math.round(win.used)}% of the ${label} limit used`,
        meta: `resets ${hhmm(win.resets_at)}`, view: 'usage', warn: true });
    }
  }
  ((w.people || {}).waiting || []).forEach((p, i) => {
    if (!p.nudge) return;
    rows.push({ key: `nudge:${i}`, text: `Nudge ${p.name || p.person || 'them'}: ${p.text}`, meta: `since ${p.since}`,
      view: 'people', warn: false });
  });
  return rows;
}

// The collapsed bar: the drive banner while the root isn't usable (held count first: the bar cuts
// long text short); else what needs you in a few words, then the 5-hour usage; "All clear" when
// nothing does.
export function barText(w) {
  if (!w) return '';
  if (w.drive) return [heldText(w.drive.held), w.drive.message].filter(Boolean).join(' · ');
  const rows = needsYou(w);
  const count = (prefix) => rows.filter((r) => r.key.startsWith(prefix)).length;
  const parts = [];
  const agents = count('agent:');
  if (agents) parts.push(agents === 1 ? '1 agent needs you' : `${agents} agents need you`);
  const n = reviewCount(w);
  if (n) parts.push(`${n} to review`);
  if (w.requests) parts.push(plural(w.requests, 'request', 'requests'));
  const due = count('due:');
  if (due) parts.push(`${due} due`);
  const nudges = count('nudge:');
  if (nudges) parts.push(plural(nudges, 'nudge', 'nudges'));
  const five = (w.usage || {}).five_hour;
  return [parts.length ? parts.join(' · ') : 'All clear', hasUsed(five) ? `5h ${Math.round(five.used)}%` : '']
    .filter(Boolean).join(' · ');
}

// Does anything need you (the bar's amber square)?
export function attention(w) {
  return !!w && (!!w.drive || needsYou(w).some((r) => r.warn));
}

// The status strip under the list: usage, agents running, the arm. [{text, warn}].
export function statusStrip(w) {
  if (!w) return [];
  const out = [];
  const u = w.usage || {};
  if (hasUsed(u.five_hour)) out.push({ text: `5H ${Math.round(u.five_hour.used)}%`, warn: !!u.five_hour.warn });
  if (hasUsed(u.seven_day)) out.push({ text: `7D ${Math.round(u.seven_day.used)}%`, warn: !!u.seven_day.warn });
  const running = (w.agents || {}).running || 0;
  out.push({ text: `${running} RUNNING`, warn: false });
  if (w.arm) out.push({ text: w.arm.reachable ? 'ARM ON' : 'ARM OFF', warn: false });
  return out;
}

// -- capture draft: what the owner typed survives a widget reload (never silently drop it) -----
export const DRAFT_KEY = 'atlas.widget.draft';

// `storage` is window.localStorage or null; every access may throw (blocked or full storage).
export function loadDraft(storage) {
  adoptLegacy(storage, DRAFT_KEY); // a draft typed before the rename
  try {
    const v = storage ? storage.getItem(DRAFT_KEY) : null;
    return typeof v === 'string' ? v : '';
  } catch (_) {
    return '';
  }
}

export function saveDraft(storage, text) {
  try {
    if (!storage) return;
    if (text) storage.setItem(DRAFT_KEY, text);
    else storage.removeItem(DRAFT_KEY);
  } catch (_) { /* best effort: the field itself still holds the text */ }
}
