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

export function resetText(w, now) {
  const at = new Date(w.resets_at);
  let clock = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  if (at.toDateString() !== now.toDateString()) clock = `${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${clock}`;
  if (w.reset_passed) return `reset at ${clock} · no data since`;
  const min = Math.max(0, Math.floor((at.getTime() - now.getTime()) / 60000));
  return `resets ${clock} (in ${span(min)})`;
}
