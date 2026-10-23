// The filing card's status line while an entry is filed. A static "Filing…" made the 10-20 s wait
// feel long (owner feedback, 2026-10-22), so it names the agent's latest step and counts seconds.
// Pure, so it is tested without a DOM. The step is agent text: callers set it with textContent.

// status: the filing's status; progress: {started: local ISO, step: str|null} or null.
// Returns the line ({text}, announced to screen readers) and the ticking part ({elapsed}, not).
export function filingStatus(status, progress, nowMs) {
  if (status === 'queued') return { text: 'Waiting to be filed…', elapsed: '' };
  const step = progress && typeof progress.step === 'string' && progress.step ? progress.step : '';
  const started = progress && typeof progress.started === 'string' ? Date.parse(progress.started) : NaN;
  const secs = Number.isNaN(started) ? null : Math.max(0, Math.floor((nowMs - started) / 1000));
  return { text: step ? `Filing… ${step}` : 'Filing…', elapsed: secs === null ? '' : `${secs} s` };
}
