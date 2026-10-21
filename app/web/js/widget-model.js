// Pure model for the desktop widget (no DOM, no imports): tile summaries and popup rows from GET /api/widget.
export const TILE_LABEL = {
  today: 'Today', usage: 'Usage', agents: 'Agents', people: 'People',
  deadlines: 'Deadlines', review: 'To review', projects: 'Projects', arm: 'Robot arm',
};

// The arm tile opens the `arm` target instead of a view.
export const TILE_VIEW = {
  today: 'today', usage: 'usage', agents: 'agents', people: 'people',
  deadlines: 'deadlines', review: 'today', projects: 'settings',
};

const MAX_ROWS = 6;
const hhmm = (iso) => String(iso || '').slice(-5);
const hasUsed = (win) => !!win && win.used !== null && win.used !== undefined;

function usageWindow(w) {
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
