// Pure model for the top bar's status cluster (phase 4.5 design §3): the amber chips, REVIEW n, 5H n%,
// the agents dot and the voice switch, from GET /api/widget (atlas/widget.py build_widget, plus
// `drive`) and GET /api/voice. No DOM. The reduced app's /api/widget has only synopsis, attention,
// tiles, drive and warnings, so every other field is optional here.
import { driveText, readonlyReason } from './drive.js';
import { tileSummary, usageWindow } from './widget-model.js';
import { widgetVoice } from './views/voice-model.js';

// summary: the /api/widget body or null (not loaded yet, or the hub is unreachable).
// voice: the /api/voice body or null.
// opts: {offline} while the hub can't be reached; {drive} overrides summary.drive (the shell's own
// /api/state is fresher); {readonly} forces the read-only chip; {review} is reviewCounts() from the
// top bar's own fetch of the review lists.
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

  // REVIEW n counts what Today's TO REVIEW counts: entries (each with its proposals, or a failed
  // filing), not proposals. Its source: the top bar's own counts, else the summary's `review.entries`
  // (when the hub sends it), else the old proposals + failed sum.
  const rc = opts.review || null;
  const wr = w.review || {};
  const proposals = rc ? rc.proposals : Number(wr.proposals) || 0;
  const entries = rc ? rc.entries
    : Number.isFinite(wr.entries) ? wr.entries : Number(tileSummary('review', w).value) || 0;
  const review = {
    count: entries, warn: entries > 0, proposals,
    title: reviewTitle(entries, proposals, rc ? rc.failed : failed, !!rc || Number.isFinite(wr.entries)),
  };

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
    review,
    usage,
    agents,
    voice: { ...v, on: !!v.show && v.action === 'off' },  // on: the switch is on (a click turns it off)
  };
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// The REVIEW readout's title: the entries to review, and how many proposals they hold.
// exact: `entries` really counts entries (else it is the proposals + failed fallback).
export function reviewTitle(entries, proposals, failed, exact = true) {
  if (!entries) return 'Nothing to review · Today';
  if (!exact) return `${entries} to review · Today`;
  const parts = [plural(entries, 'entry', 'entries') + ' to review'];
  if (proposals) parts.push(plural(proposals, 'proposal', 'proposals'));
  if (failed) parts.push(plural(failed, 'failed filing', 'failed filings'));
  return `${parts.join(' · ')} · Today`;
}

// The review counts from GET /api/proposals (groups: [{entry_id, proposals}]) and
// GET /api/filings?status=failed ([{entry_id}]), as Today counts them: one per entry (an entry
// listed in both counts once), plus the proposals they hold and the failed filings.
export function reviewCounts(groups, failedRows) {
  const ids = new Set();
  let proposals = 0;
  for (const g of groups || []) {
    if (!g || !g.entry_id) continue;
    ids.add(g.entry_id);
    proposals += (g.proposals || []).length;
  }
  let failed = 0;
  for (const f of failedRows || []) {
    if (!f || !f.entry_id) continue;
    failed += 1;
    ids.add(f.entry_id);
  }
  return { entries: ids.size, proposals, failed };
}
