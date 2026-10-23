// The tablet page's timing and storage logic, without the DOM (phase 3 design §6). Plain ES5: the
// page uses window.BHLogic, node's tests module.exports.
(function (root) {
  'use strict';

  // IGNORE (spec §3.3): the first tap arms it ("TAP AGAIN"), a second within `ms` confirms;
  // the page reverts an armed button once expired() says so.
  function ConfirmTap(ms) {
    this.ms = ms || 2000;
    this.armedAt = null;
  }
  ConfirmTap.prototype.tap = function (now) {
    if (this.armedAt !== null && now - this.armedAt <= this.ms) {
      this.armedAt = null;
      return 'confirmed';
    }
    this.armedAt = now;
    return 'armed';
  };
  ConfirmTap.prototype.expired = function (now) {
    return this.armedAt !== null && now - this.armedAt > this.ms;
  };
  ConfirmTap.prototype.reset = function () { this.armedAt = null; };

  // HOME: a deliberate action (spec §3.3): held for holdMs (check() polled while down), or two
  // short taps within doubleMs. Returns 'go', 'tap' (a first short tap) or null.
  function HoldOrDouble(holdMs, doubleMs) {
    this.holdMs = holdMs || 600;
    this.doubleMs = doubleMs || 400;
    this.downAt = null;
    this.lastTap = null;
  }
  HoldOrDouble.prototype.down = function (now) { this.downAt = now; };
  HoldOrDouble.prototype.held = function (now) {
    return this.downAt !== null && now - this.downAt >= this.holdMs;
  };
  HoldOrDouble.prototype.check = function (now) {
    if (!this.held(now)) return null;
    this.downAt = null;
    this.lastTap = null;
    return 'go';
  };
  HoldOrDouble.prototype.up = function (now) {
    if (this.downAt === null) return null;  // fired while held, or never pressed
    var held = now - this.downAt >= this.holdMs;
    this.downAt = null;
    if (held) { this.lastTap = null; return 'go'; }
    if (this.lastTap !== null && now - this.lastTap <= this.doubleMs) { this.lastTap = null; return 'go'; }
    this.lastTap = now;
    return 'tap';
  };
  HoldOrDouble.prototype.cancel = function () { this.downAt = null; };

  // The poll's retry delays (design §6.4): 2, 4, 8, 16, then 30 s; failing() after 3 in a row.
  function Backoff() {
    this.steps = [2000, 4000, 8000, 16000, 30000];
    this.fails = 0;
  }
  Backoff.prototype.fail = function () {
    var d = this.steps[Math.min(this.fails, this.steps.length - 1)];
    this.fails += 1;
    return d;
  };
  Backoff.prototype.ok = function () { this.fails = 0; };
  Backoff.prototype.failing = function () { return this.fails >= 3; };

  // localStorage that may be missing or throw (private mode, a full disk): reads fall back.
  function Store(storage) { this.s = storage || null; }
  Store.prototype.get = function (key, fallback) {
    try {
      var raw = this.s ? this.s.getItem(key) : null;
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) { return fallback; }
  };
  Store.prototype.set = function (key, value) {
    try { if (this.s) this.s.setItem(key, JSON.stringify(value)); return true; } catch (e) { return false; }
  };
  Store.prototype.remove = function (key) {
    try { if (this.s) this.s.removeItem(key); } catch (e) { /* nothing to keep */ }
  };

  // What the owner sent while the hub was out of reach (design §6.5): kept in order, on disk, and
  // sent first-in first-out once it answers. Nothing typed is lost.
  function SendQueue(store, key) {
    this.store = store;
    this.key = key || 'bh.queue';
    var items = store.get(this.key, []);
    this.items = items && items.length !== undefined ? items : [];
  }
  SendQueue.prototype._save = function () { this.store.set(this.key, this.items); };
  SendQueue.prototype.push = function (item) { this.items.push(item); this._save(); };
  SendQueue.prototype.peek = function () { return this.items.length ? this.items[0] : null; };
  SendQueue.prototype.shift = function () { var x = this.items.shift(); this._save(); return x; };
  SendQueue.prototype.size = function () { return this.items.length; };
  SendQueue.prototype.has = function (pred) {
    for (var i = 0; i < this.items.length; i++) if (pred(this.items[i])) return true;
    return false;
  };

  var api = { ConfirmTap: ConfirmTap, HoldOrDouble: HoldOrDouble, Backoff: Backoff, Store: Store,
              SendQueue: SendQueue };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.BHLogic = api;
})(this);
