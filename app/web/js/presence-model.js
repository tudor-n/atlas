// The presence ping (chatter spec §5): a key or a pointer press in the app or the widget tells the hub
// the owner is at the PC, at most every 15 seconds (well inside the 1-minute away at Very high). Fire and forget: a failed ping is never shown. No DOM
// of its own; `target` is the document, `send` the POST, `now` a millisecond clock.

export const PING_EVERY_MS = 15000;

// At most one go per `ms`. A clock that went backwards (a resumed laptop) lets the next one through.
export class Throttle {
  constructor(ms = PING_EVERY_MS) {
    this.ms = ms;
    this.last = null;
  }

  take(now) {
    if (this.last !== null && now >= this.last && now - this.last < this.ms) return false;
    this.last = now;
    return true;
  }
}

// Listens on `target` (capture phase, so a handler that stops the event can't hide it) and returns
// the function that stops listening.
export function installPresencePing(target, send, { every = PING_EVERY_MS, now = () => Date.now() } = {}) {
  const gate = new Throttle(every);
  const ping = () => {
    if (!gate.take(now())) return;
    try { Promise.resolve(send()).catch(() => {}); } catch (_) { /* the hub is away: the next press tries again */ }
  };
  const opts = { capture: true, passive: true };
  for (const type of ['keydown', 'pointerdown']) target.addEventListener(type, ping, opts);
  return () => { for (const type of ['keydown', 'pointerdown']) target.removeEventListener(type, ping, opts); };
}
