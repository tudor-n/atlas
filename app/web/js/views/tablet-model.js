// Pure helpers for the Tablet view and Settings → Tablet (tested with node).

// A tablet note's filing state: never filed unless the owner asks (phase 3 design §7).
export function noteState(n) {
  const f = n.filing;
  if (!f) return { label: 'not filed', canFile: true };
  if (f === 'queued' || f === 'running') return { label: 'filing…', canFile: false };
  if (f === 'failed') return { label: 'filing failed', canFile: true };
  return { label: 'filed', canFile: false };
}

// What the Tablet section says about the LAN listener.
export function lanLine(s) {
  if (!s.tablet_lan) return 'Off: BrainHub only listens on this PC.';
  if (!s.tablet_address) return 'On, but no address is set.';
  if (s.listening) return `Listening on http://${s.listening}:${s.port}/tablet`;
  if (s.error) return `Can't listen on ${s.tablet_address}: ${s.error}`;
  return `Waiting: this laptop doesn't have ${s.tablet_address} right now (another network?).`;
}

// "mm, cm, °" -> ["mm", "cm", "°"]
export function parseUnits(text) {
  return String(text).split(',').map((u) => u.trim()).filter(Boolean);
}

// Seconds left on a pairing code, as "4:59".
export function countdown(secs) {
  const s = Math.max(0, Math.floor(secs));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
