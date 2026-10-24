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

// The dot next to a note's filing state: green once filed, grey while filing, none otherwise (a
// failed filing shows the amber square instead, and "not filed" is plain text).
export function noteDot(n) {
  const f = n.filing;
  if (!f) return null;
  if (f === 'queued' || f === 'running') return '';
  if (f === 'failed') return 'a';
  return 'g';
}

// A note's or request's time, short and mono: "14:05" today, "10-21 09:00" this year, else the date
// and time in full. Anything unparseable comes back as typed.
export function shortWhen(iso, now) {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(String(iso || ''));
  if (!m) return String(iso || '');
  const pad = (n) => String(n).padStart(2, '0');
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const time = m[4] ? `${m[4]}:${m[5]}` : '';
  if (`${m[1]}-${m[2]}-${m[3]}` === today && time) return time;
  const day = m[1] === String(now.getFullYear()) ? `${m[2]}-${m[3]}` : `${m[1]}-${m[2]}-${m[3]}`;
  return time ? `${day} ${time}` : day;
}
