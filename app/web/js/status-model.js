// Pure model for the top bar's status cluster (phase 4.5 design §3): the amber chips, REVIEW n, 5H n%,
// the agents dot and the voice switch, from GET /api/widget (brainhub/widget.py build_widget, plus
// `drive`) and GET /api/voice. No DOM. The reduced app's /api/widget has only synopsis, attention,
// tiles, drive and warnings, so every other field is optional here.
import { driveText, readonlyReason } from './drive.js';
import { tileSummary, usageWindow } from './widget-model.js';
import { widgetVoice } from './views/voice-model.js';

// summary: the /api/widget body or null (not loaded yet, or the hub is unreachable).
// voice: the /api/voice body or null.
// opts: {offline} while the hub can't be reached; {drive} overrides summary.drive (the shell's own
// /api/state is fresher); {readonly} forces the read-only chip.
export function statusFrom(summary, voice, opts = {}) {
  const w = summary || {};
  const drive = opts.drive !== undefined ? opts.drive : (w.drive || null);
  const readonly = !!opts.readonly || drive?.state === 'readonly';

  const chips = [];
  if (opts.offline) chips.push({ text: 'OFFLINE', kind: 'offline', title: "Can't reach the hub. Retrying." });
  if (readonly) {
    chips.push({ text: 'READ-ONLY', kind: 'readonly', title: readonlyReason(drive || { state: 'readonly' }) });
  } else if (drive) {
    chips.push({ text: 'DRIVE', kind: 'drive', title: driveText(drive) });
  }
  const failed = Number(w.review?.failed) || 0;
  if (failed > 0) chips.push({ text: `${failed} FAILED`, kind: 'failed', title: `${failed} filing${failed === 1 ? '' : 's'} failed` });

  const r = tileSummary('review', w);
  const count = Number(r.value) || 0;

  const u = w.usage ? usageWindow(w) : null;
  const usage = u ? { label: u.label.toUpperCase(), pct: Math.round(u.win.used), warn: !!u.win.warn } : null;

  const running = Number(w.agents?.running) || 0;
  const waiting = Number(w.agents?.waiting) || 0;
  const agents = {
    running, waiting,
    dot: waiting > 0 ? 'a' : running > 0 ? 'g' : '',
    text: [`${running} RUNNING`, waiting > 0 ? `${waiting} WAITING` : ''].filter(Boolean).join(' · '),
  };

  const v = widgetVoice(voice);
  return {
    chips,
    review: { count, warn: count > 0 },
    usage,
    agents,
    voice: { ...v, on: !!v.show && v.action === 'off' },  // on: the switch is on (a click turns it off)
  };
}
