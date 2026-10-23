// The tablet's in-page keyboard, as state without the DOM (phase 3 design §6.3). Plain ES5 for the
// SM-T110's old WebKit: the page uses window.BHKeyboard, node's tests module.exports.
(function (root) {
  'use strict';

  var SHIFT_LOCK_MS = 400;  // a second tap on shift within this locks caps

  function Keyboard(units) {
    this.units = (units || []).slice().sort(function (a, b) { return b.length - a.length; });
    this.reset('', 'text', false, 20000);
  }

  // kind: 'text' (notes; letters first) or 'number' (a measurement: keypad and unit chips first).
  Keyboard.prototype.reset = function (text, kind, multiline, max) {
    this.text = text || '';
    this.kind = kind === 'number' ? 'number' : 'text';
    this.mode = this.kind === 'number' ? 'num' : 'abc';
    this.shift = 'off';
    this.multiline = !!multiline;
    this.max = max || 20000;
    this._shiftAt = -1;
  };

  Keyboard.prototype.caps = function () {
    return this.mode === 'abc' && this.shift !== 'off';
  };

  Keyboard.prototype._add = function (s) {
    if (this.text.length + s.length <= this.max) this.text += s;
  };

  // One key, pressed at time `now` (ms). Returns 'done' (the field's action: SAVE or SET),
  // 'hide' (close, keeping the text) or null.
  Keyboard.prototype.press = function (key, now) {
    if (key === 'bksp') {
      this.text = this.text.slice(0, -1);
    } else if (key === 'space') {
      if (this.text && this.text.charAt(this.text.length - 1) !== ' ' &&
          this.text.charAt(this.text.length - 1) !== '\n') this._add(' ');
    } else if (key === 'enter') {
      if (!this.multiline) return 'done';
      this._add('\n');
    } else if (key === 'shift') {
      if (this.shift === 'lock') {
        this.shift = 'off';
      } else if (this.shift === 'once' && now - this._shiftAt <= SHIFT_LOCK_MS) {
        this.shift = 'lock';
      } else {
        this.shift = this.shift === 'once' ? 'off' : 'once';
      }
      this._shiftAt = now;
    } else if (key === 'mode:abc' || key === 'mode:sym' || key === 'mode:num') {
      this.mode = key.slice(5);
      if (this.mode === 'num' && this.kind !== 'number') this.mode = 'sym';
    } else if (key === 'hide') {
      return 'hide';
    } else if (key === 'done') {
      return 'done';
    } else if (key.indexOf('unit:') === 0) {
      this.text = withUnit(this.text, key.slice(5), this.units);
    } else if (key.length === 1) {
      var ch = this.caps() ? key.toUpperCase() : key;
      this._add(ch);
      if (this.shift === 'once') this.shift = 'off';
    }
    return null;
  };

  // "412" + mm -> "412 mm"; "412 mm" + cm -> "412 cm"; "412mm" + cm -> "412 cm". A unit only
  // follows a value: on an empty field the chip does nothing.
  function withUnit(text, unit, units) {
    var t = text.replace(/\s+$/, '');
    if (!t) return text;
    for (var i = 0; i < units.length; i++) {
      var u = units[i];
      if (t.length > u.length && t.slice(-u.length) === u) {
        var before = t.charAt(t.length - u.length - 1);
        if (before === ' ' || /[0-9.]/.test(before)) {
          t = t.slice(0, t.length - u.length).replace(/\s+$/, '');
          break;
        }
      }
    }
    return t ? t + ' ' + unit : text;
  }

  var api = { Keyboard: Keyboard, withUnit: withUnit };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.BHKeyboard = api;
})(this);
