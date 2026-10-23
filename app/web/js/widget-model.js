// Pure model for the desktop widget (no DOM; drive.js is pure too): bar text, tile summaries and
// popup rows from GET /api/widget.
import { heldText } from './drive.js';

export const TILE_LABEL = {
  today: 'Today', usage: 'Usage', agents: 'Agents', people: 'People',
  deadlines: 'Deadlines', review: 'To review', projects: 'Projects', arm: 'Robot arm',
};

// The arm tile opens the `arm` target instead of a view.
export const TILE_VIEW = {
  today: 'today', usage: 'usage', agents: 'agents', people: 'people',
  deadlines: 'deadlines', review: 'today', projects: 'browse',
};

// How a tile opens its view. In the desktop shell the widget asks the shell for the main window
// (`open_main`, design §6); in a plain browser, or when that call fails, it posts /api/open, which
// opens the browser. The arm tile always posts its own target. `invoke` is the shell's
// `__TAURI__.core.invoke` or null (tauriInvoke below).
export function openPlan(name, view, invoke) {
  if (name === 'arm') return { invoke: null, view: null, body: { target: 'arm' } };
  const v = view || TILE_VIEW[name] || null;
  return { invoke: typeof invoke === 'function' ? invoke : null, view: v, body: v ? { target: 'app', view: v } : { target: 'app' } };
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

const MAX_ROWS = 6;
const hhmm = (iso) => String(iso || '').slice(-5);

// The collapsed bar: the drive banner while the root isn't usable, else the server's synopsis. The
// bar cuts long text short, so the held count goes first there.
export function synopsisText(w) {
  if (!w) return '';
  if (!w.drive) return w.synopsis || '';
  return [heldText(w.drive.held), w.drive.message].filter(Boolean).join(' · ');
}
const hasUsed = (win) => !!win && win.used !== null && win.used !== undefined;

export function usageWindow(w) {
  const u = w.usage || {};
  if (hasUsed(u.five_hour)) return { win: u.five_hour, label: '5h' };
  if (hasUsed(u.seven_day)) return { win: u.seven_day, label: '7d' };
  return null;
}

export function tileSummary(name, w) {
  switch (name) {
    case 'today': {
      const t = w.today || {};
      const overdue = t.overdue || 0;
      return {
        value: String(overdue + (t.due_today || 0)),
        caption: overdue > 0 ? `due · ${overdue} overdue` : 'due today',
        warn: overdue > 0,
      };
    }
    case 'usage': {
      const u = usageWindow(w);
      if (!u) return { value: '—', caption: 'no data yet', warn: false };
      return {
        value: `${Math.round(u.win.used)}%`,
        caption: `${u.label} · resets ${hhmm(u.win.resets_at)}`,
        warn: !!u.win.warn,
      };
    }
    case 'agents': {
      const a = w.agents || {};
      const waiting = a.waiting || 0;
      return {
        value: String(a.running || 0),
        caption: waiting ? `running · ${waiting} waiting` : 'running',
        warn: waiting > 0,
      };
    }
    case 'people':
      return { value: String(((w.people || {}).waiting || []).length), caption: 'waiting on others', warn: false };
    case 'deadlines': {
      const d = w.deadlines || [];
      return { value: String(d.length), caption: d.length ? `next: ${d[0].due}` : 'none', warn: false };
    }
    case 'review': {
      const r = w.review || {};
      const n = (r.proposals || 0) + (r.failed || 0);
      return { value: String(n), caption: 'to confirm', warn: n > 0 };
    }
    case 'projects':
      return { value: String((w.projects || []).filter((p) => p.open > 0).length), caption: 'with open items', warn: false };
    case 'arm': {
      const on = !!(w.arm && w.arm.reachable);
      return { value: on ? 'on' : 'off', caption: on ? 'reachable' : 'offline', warn: false };
    }
    default:
      return { value: '', caption: '', warn: false };
  }
}

function usageRow(label, win) {
  if (!hasUsed(win)) return null;
  return { text: `${label} window`, meta: `${Math.round(win.used)}% · resets ${hhmm(win.resets_at)}` };
}

export function popupRows(name, w) {
  let rows = [];
  switch (name) {
    case 'today':
      rows = ((w.today || {}).top || []).map((i) => ({ text: i.text, meta: i.due || '' }));
      break;
    case 'usage': {
      const u = w.usage || {};
      rows = [usageRow('5h', u.five_hour), usageRow('7d', u.seven_day)].filter(Boolean);
      break;
    }
    case 'agents':
      rows = ((w.agents || {}).rows || []).map((r) => ({
        text: [r.project_name, r.part].filter(Boolean).join(' · ') || '(unknown)',
        meta: [r.status, r.where, r.next_time ? 'next time' : ''].filter(Boolean).join(' · '),
      }));
      break;
    case 'people':
      rows = ((w.people || {}).waiting || []).map((p) => ({
        text: `${p.name || p.person || 'Someone'}: ${p.text}`,
        meta: p.nudge ? `since ${p.since} · nudge` : `since ${p.since}`,
      }));
      break;
    case 'deadlines':
      rows = (w.deadlines || []).map((d) => ({ text: d.text, meta: d.due || '' }));
      break;
    case 'review': {
      const r = w.review || {};
      if (r.proposals) rows.push({ text: `${r.proposals} proposal${r.proposals === 1 ? '' : 's'} to confirm`, meta: '' });
      if (r.failed) rows.push({ text: `${r.failed} filing${r.failed === 1 ? '' : 's'} failed`, meta: '' });
      break;
    }
    case 'projects':
      rows = (w.projects || []).filter((p) => p.open > 0).map((p) => ({
        text: p.name || p.slug,
        meta: `${p.open} open` + (p.next_time ? ` · ${p.next_time} next time` : ''),
        view: `project/${p.slug}`,
      }));
      break;
    default:
      break;
  }
  return rows.slice(0, MAX_ROWS);
}

// -- capture draft: what the owner typed survives a widget reload (never silently drop it) -----
export const DRAFT_KEY = 'brainhub.widget.draft';

// `storage` is window.localStorage or null; every access may throw (blocked or full storage).
export function loadDraft(storage) {
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
