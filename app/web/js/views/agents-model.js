// Pure helpers for the Agents and Usage pages (no DOM).
export const STATUS_LABEL = { idle: 'ready', running: 'running', waiting: 'waiting for you', finished: 'finished' };

// The Status cell: a closed session still lingering in the list shows "ended", whatever its last status.
export function statusText(row) {
  return row.ended ? 'ended' : (STATUS_LABEL[row.status] || row.status);
}

const pad = (n) => String(n).padStart(2, '0');

// Whole minutes -> '5 min', '2 h 5 min', '3 d'.
function span(min) {
  if (min < 60) return `${min} min`;
  if (min < 1440) {
    const h = Math.floor(min / 60), m = min % 60;
    return m ? `${h} h ${m} min` : `${h} h`;
  }
  return `${Math.floor(min / 1440)} d`;
}

export function sinceText(iso, now) {
  const secs = (now.getTime() - new Date(iso).getTime()) / 1000;
  if (!(secs >= 60)) return 'just now';
  return span(Math.floor(secs / 60));
}

export function nextTimeText(row) {
  const n = Number(row.next_time) || 0;
  if (n <= 0) return null;
  return `${n} next-time ${n === 1 ? 'note' : 'notes'} for ${row.project_name}`;
}

// An unmapped row whose folder has the same name as a project's folder on the other OS offers a
// one-click link (dual-boot design §6.4). Nothing is mapped until the owner clicks it.
export function linkText(row) {
  return !row.mapped && row.suggest ? `link to ${row.suggest.name}` : null;
}

// The row's tooltip names the OS the session ran on (dual-boot design §6.6).
const OS_LABEL = { windows: 'Windows', linux: 'Linux' };
export function osText(row) {
  return Object.hasOwn(OS_LABEL, row.os) ? `on ${OS_LABEL[row.os]}` : null;
}

export function resetText(w, now) {
  const at = new Date(w.resets_at);
  let clock = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  if (at.toDateString() !== now.toDateString()) clock = `${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${clock}`;
  if (w.reset_passed) return `reset at ${clock} · no data since`;
  const min = Math.max(0, Math.floor((at.getTime() - now.getTime()) / 60000));
  return `resets ${clock} (in ${span(min)})`;
}

// The status dot (phase 4.5 design §4): green running, amber waiting, grey ready or finished, and a
// hollow ring once the session has ended, whatever its last status.
export function statusDot(row) {
  if (row.ended) return 'ring';
  if (row.status === 'running') return 'g';
  if (row.status === 'waiting') return 'a';
  return '';
}

// A usage window's value: "84%", or "–" when there is no reading.
export function usedText(w) {
  const used = w && w.used != null && Number.isFinite(Number(w.used)) ? Math.round(Number(w.used)) : null;
  return used == null ? '–' : `${used}%`;
}

// How many of a meter's segments a used percentage lights: any use lights at least one, 100% all.
export function meterCells(used, segments = 20) {
  const u = Number(used);
  if (used == null || !Number.isFinite(u) || u <= 0) return 0;
  return Math.min(segments, Math.ceil((u / 100) * segments));
}

// The WHERE cell's value: the owner's override, else `auto (<what the hooks saw>)`.
export function whereText(row) {
  return row.where_override ? String(row.where_override) : `auto (${row.where || 'unknown'})`;
}
