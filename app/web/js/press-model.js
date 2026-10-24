// Pure model for the arm's tap-again confirmation (phase 4.5 design §2): a risky action fires only on
// a second press within `ms`. The first press arms it; once `ms` has passed it is disarmed again, so
// a later press arms it afresh. No DOM; `now` is a millisecond timestamp (Date.now(), performance.now()).

export class Twice {
  constructor(ms = 2000) {
    this.ms = ms;
    this.at = null;  // when the first press armed it, or null while disarmed
  }

  // 'armed' (first press, or the window ran out) or 'go' (second press within the window: fire).
  tap(now) {
    if (this.armed(now)) {
      this.at = null;
      return 'go';
    }
    this.at = now;
    return 'armed';
  }

  reset() {
    this.at = null;
  }

  armed(now) {
    return this.at !== null && now - this.at >= 0 && now - this.at < this.ms;
  }
}
