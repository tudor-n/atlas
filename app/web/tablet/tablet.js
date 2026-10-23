// BrainHub on the workbench tablet (phase 3 design §6): pairing, a home screen with ROBOT and
// JOURNAL, and JOURNAL's TODAY · NOTES · AGENTS, with an in-page keyboard for all typing.
// Plain ES5 for the SM-T110's Android 4.2 WebKit: no modules, no fetch, no WebSocket (the hub is
// long-polled), and text only ever enters the page as text nodes (rule 6).
(function () {
  'use strict';
  var L = window.BHLogic, K = window.BHKeyboard;
  var store = new L.Store(window.localStorage || null);
  var queue = new L.SendQueue(store, 'bh.queue');
  var backoff = new L.Backoff();
  var DEVICE_NAME = 'Workbench tablet';
  var S = { screen: 'boot', tab: store.get('bh.tab', 'today'), state: null, version: 0, loop: 0,
            flushing: false, lastTouch: 0, keys: {} };

  // -- DOM helpers: text nodes only, class swaps, writes skipped when unchanged -------------------
  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.appendChild(document.createTextNode(String(text)));
    return e;
  }
  function at(e, x, y, w, hgt) {
    e.style.left = x + 'px'; e.style.top = y + 'px';
    if (w !== undefined) e.style.width = w + 'px';
    if (hgt !== undefined) e.style.height = hgt + 'px';
    return e;
  }
  function txt(e, s) { s = s === undefined || s === null ? '' : String(s); if (e._t !== s) { e._t = s; e.textContent = s; } }
  function hasCls(e, c) { return (' ' + e.className + ' ').indexOf(' ' + c + ' ') >= 0; }
  function cls(e, c, on) {
    if (on && !hasCls(e, c)) e.className = (e.className + ' ' + c).replace(/^\s+/, '');
    if (!on && hasCls(e, c)) e.className = (' ' + e.className + ' ').replace(' ' + c + ' ', ' ').replace(/^\s+|\s+$/g, '');
  }
  function show(e, on) { var d = on ? '' : 'none'; if (e.style.display !== d) e.style.display = d; }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); }
  function now() { return new Date().getTime(); }

  // A tap: fires on a touch-up that didn't move (lists can still be dragged), with no 300 ms click
  // delay and no ghost click. Mouse clicks work too, for testing on a PC.
  function onTap(e, fn) {
    var sx = 0, sy = 0, moved = false;
    e.addEventListener('touchstart', function (ev) {
      var t = ev.touches[0]; sx = t.clientX; sy = t.clientY; moved = false; S.lastTouch = now(); cls(e, 'down', true);
    }, false);
    e.addEventListener('touchmove', function (ev) {
      var t = ev.touches[0];
      if (Math.abs(t.clientX - sx) > 10 || Math.abs(t.clientY - sy) > 10) { moved = true; cls(e, 'down', false); }
    }, false);
    e.addEventListener('touchend', function (ev) {
      cls(e, 'down', false); S.lastTouch = now();
      if (!moved) { ev.preventDefault(); fn(ev); }
    }, false);
    e.addEventListener('touchcancel', function () { cls(e, 'down', false); }, false);
    e.addEventListener('click', function (ev) { if (now() - S.lastTouch > 800) fn(ev); }, false);
  }

  // A pane that scrolls by dragging (touchmove with preventDefault), never the page.
  function Scroller(view) {
    var self = this;
    this.view = view; this.inner = h('div', 'scroll'); view.appendChild(this.inner); this.y = 0;
    var y0 = 0, start = 0;
    view.addEventListener('touchstart', function (ev) { y0 = ev.touches[0].clientY; start = self.y; }, false);
    view.addEventListener('touchmove', function (ev) {
      ev.preventDefault();
      self.to(start + ev.touches[0].clientY - y0);
    }, false);
    view.addEventListener('mousewheel', function (ev) { self.to(self.y + (ev.wheelDelta > 0 ? 60 : -60)); }, false);
  }
  Scroller.prototype.to = function (y) {
    var min = Math.min(0, this.view.offsetHeight - this.inner.offsetHeight - 16);
    this.y = Math.max(min, Math.min(0, y));
    var v = 'translate3d(0,' + this.y + 'px,0)';
    this.inner.style.webkitTransform = v; this.inner.style.transform = v;
  };

  // -- talking to the hub (XHR; JSON both ways) ------------------------------------------------
  function api(method, path, body, cb, timeoutMs) {
    var x = new XMLHttpRequest(), done = false;
    var timer = setTimeout(function () { if (!done) { done = true; try { x.abort(); } catch (e) {} cb(0, null); } },
                           timeoutMs || 15000);
    x.open(method, path, true);
    if (body !== null && body !== undefined) x.setRequestHeader('Content-Type', 'application/json');
    x.onreadystatechange = function () {
      if (x.readyState !== 4 || done) return;
      done = true; clearTimeout(timer);
      var data = null;
      try { data = x.responseText ? JSON.parse(x.responseText) : null; } catch (e) { data = null; }
      cb(x.status, data);
    };
    x.send(body !== null && body !== undefined ? JSON.stringify(body) : null);
    return x;
  }

  // -- the screens ------------------------------------------------------------------------------
  var root = document.getElementById('root');
  var scr = {};
  function screen(name) {
    var e = h('div', 'screen'); root.appendChild(e); scr[name] = e; return e;
  }
  function go(name) {
    S.screen = name;
    for (var k in scr) if (scr.hasOwnProperty(k)) cls(scr[k], 'on', k === name);
    if (name !== 'journal') kbClose();
    render();
  }

  // pairing
  var pair = { code: '' };
  (function () {
    var e = screen('pair');
    e.appendChild(at(h('div', 'abs label', 'Pair this tablet'), 40, 40));
    e.appendChild(at(h('div', 'abs', 'Type the code from BrainHub on the PC'), 40, 72)).style.fontSize = '26px';
    e.appendChild(at(h('div', 'abs dim', 'On the PC: Settings → Tablet → Pair a device.'), 40, 118)).style.fontSize = '17px';
    pair.boxes = [];
    for (var i = 0; i < 6; i++) { var b = at(h('div', 'codebox mono'), 40 + i * 76, 210); e.appendChild(b); pair.boxes.push(b); }
    pair.msg = at(h('div', 'abs amber'), 40, 320, 500); pair.msg.style.fontSize = '17px'; e.appendChild(pair.msg);
    var keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'bksp', '0', 'pair'];
    for (var j = 0; j < keys.length; j++) {
      var k = keys[j], label = k === 'bksp' ? '⌫' : (k === 'pair' ? 'PAIR' : k);
      var key = at(h('div', 'key' + (k === 'pair' ? ' act sp' : (k === 'bksp' ? ' glyph' : '')), label),
                   600 + (j % 3) * 128, 120 + Math.floor(j / 3) * 92, 116);
      key.setAttribute('data-k', k); e.appendChild(key);
    }
    keyPad(e, function (k) {
      if (k === 'bksp') pair.code = pair.code.slice(0, -1);
      else if (k === 'pair') doPair();
      else if (pair.code.length < 6) pair.code += k;
      txt(pair.msg, '');
      render();
    });
  })();
  function doPair() {
    if (pair.code.length !== 6) { txt(pair.msg, 'The code has 6 digits.'); return; }
    txt(pair.msg, 'Pairing…');
    api('POST', '/api/tablet/pair', { code: pair.code, name: DEVICE_NAME }, function (st, data) {
      if (st === 200) { pair.code = ''; txt(pair.msg, ''); startLoop(); return; }
      pair.code = '';
      txt(pair.msg, st === 0 ? 'Can’t reach BrainHub.' : (data && data.error) || ('Pairing failed (' + st + ')'));
      render();
    });
  }

  // connecting (the first state hasn't come yet)
  var boot = {};
  (function () {
    var e = screen('boot');
    e.appendChild(at(h('div', 'abs label', 'BrainHub'), 40, 40));
    boot.msg = at(h('div', 'abs dim'), 40, 72, 900); boot.msg.style.fontSize = '26px'; e.appendChild(boot.msg);
    txt(boot.msg, 'Connecting…');
  })();

  // waiting for the PC's setup wizard
  (function () {
    var e = screen('setup');
    e.appendChild(at(h('div', 'abs label', 'BrainHub'), 40, 40));
    var t = at(h('div', 'abs', 'Set up BrainHub on the PC first.'), 40, 72); t.style.fontSize = '26px'; e.appendChild(t);
  })();

  // home: ROBOT and JOURNAL (spec §3.3)
  var home = {};
  (function () {
    var e = screen('home');
    e.appendChild(at(h('div', 'abs label', 'BrainHub'), 40, 34));
    home.clock = at(h('div', 'abs mono'), 880, 28, 104); home.clock.style.textAlign = 'right'; home.clock.style.fontSize = '16px';
    e.appendChild(home.clock);
    function block(x, name, sub) {
      var b = at(h('div', 'mode'), x, 90); b.appendChild(h('div', 'name', name));
      var dot = h('div', 'dot'); b.appendChild(dot);
      var s = h('div', 'sub dim ellip'); b.appendChild(s); e.appendChild(b);
      return { el: b, dot: dot, sub: s };
    }
    home.robot = block(40, 'ROBOT');
    home.journal = block(532, 'JOURNAL');
    home.msg = at(h('div', 'abs amber'), 40, 492, 900); home.msg.style.fontSize = '17px'; e.appendChild(home.msg);
    home.who = at(h('div', 'abs faint'), 40, 548, 900); home.who.style.fontSize = '15px'; e.appendChild(home.who);
    onTap(home.robot.el, function () {
      var st = S.state;
      if (!st) return;
      if (!st.home.arm_ok) { say(home.msg, 'The arm server is offline: start it on the PC first.'); return; }
      window.location.href = st.robot_url;
    });
    onTap(home.journal.el, function () { go('journal'); });
  })();

  // journal: the bar, the panes, HOME
  var J = {};
  (function () {
    var e = screen('journal');
    var bar = h('div', 'bar'); e.appendChild(bar);
    bar.appendChild(h('div', 'brand', 'BRAINHUB'));
    J.tabs = {};
    var names = [['today', 'TODAY', 130], ['notes', 'NOTES', 250], ['agents', 'AGENTS', 384]];
    for (var i = 0; i < names.length; i++) (function (n) {
      var t = at(h('div', 'tab'), n[2], 0); t.appendChild(document.createTextNode(n[1]));
      if (n[0] === 'notes') { J.chip = h('span', 'chip'); t.appendChild(J.chip); }
      t.appendChild(h('div', 'under')); bar.appendChild(t); J.tabs[n[0]] = t;
      onTap(t, function () { S.tab = n[0]; store.set('bh.tab', S.tab); kbClose(); render(); });
    })(names[i]);
    J.link = at(h('div', 'dot'), 900, 24); bar.appendChild(J.link);
    J.linkText = at(h('div', 'abs mono dim'), 914, 20, 100); J.linkText.style.fontSize = '12px'; bar.appendChild(J.linkText);
    J.panes = {};
    J.scroll = {};
    for (var p = 0; p < names.length; p++) {
      var pane = h('div', 'pane'); e.appendChild(pane); J.panes[names[p][0]] = pane;
      if (names[p][0] !== 'agents') J.scroll[names[p][0]] = new Scroller(pane);
    }
    J.agents = h('div', 'agents'); J.panes.agents.appendChild(J.agents);
    J.usage = h('div', 'usage'); J.panes.agents.appendChild(J.usage);
    var foot = h('div', 'foot'); e.appendChild(foot);
    J.home = h('div', 'home', 'HOME (HOLD)'); foot.appendChild(J.home);
    J.msg = h('div', 'msg'); foot.appendChild(J.msg);
    homeControl(J.home);
  })();

  // HOME: a deliberate action (spec §3.3): hold it, or tap it twice. Far from where STOP sits.
  function homeControl(e) {
    var hod = new L.HoldOrDouble(600, 400), timer = null;
    function down(ev) {
      if (ev.type === 'mousedown' && now() - S.lastTouch < 800) return;
      if (ev.type === 'touchstart') { S.lastTouch = now(); ev.preventDefault(); }
      hod.down(now()); cls(e, 'hold', true); txt(e, 'HOLD…');
      timer = setInterval(function () { if (hod.check(now()) === 'go') { stop(); go('home'); } }, 50);
    }
    function stop() { if (timer) { clearInterval(timer); timer = null; } cls(e, 'hold', false); txt(e, 'HOME (HOLD)'); }
    function up(ev) {
      if (ev.type === 'mouseup' && now() - S.lastTouch < 800) return;
      var r = hod.up(now()); stop();
      if (r === 'go') go('home');
      else if (r === 'tap') say(J.msg, 'Hold HOME, or tap it twice, to go back.', true);
    }
    e.addEventListener('touchstart', down, false);
    e.addEventListener('touchend', up, false);
    e.addEventListener('touchcancel', function () { hod.cancel(); stop(); }, false);
    e.addEventListener('mousedown', down, false);
    e.addEventListener('mouseup', up, false);
  }

  function say(e, text, quiet) {
    txt(e, text); cls(e, 'amber', !quiet); cls(e, 'dim', !!quiet);
    if (e._timer) clearTimeout(e._timer);
    e._timer = setTimeout(function () { txt(e, ''); }, 4000);
  }

  // -- IGNORE: double tap to confirm (spec §3.3) -------------------------------------------------
  function ignoreButton(x, y, kind, id) {
    var b = at(h('div', 'btn', 'IGNORE'), x, y, 140), c = new L.ConfirmTap(2000), timer = null;
    onTap(b, function () {
      if (c.tap(now()) === 'armed') {
        cls(b, 'armed', true); txt(b, 'TAP AGAIN');
        timer = setTimeout(function () { c.reset(); cls(b, 'armed', false); txt(b, 'IGNORE'); }, 2000);
        return;
      }
      if (timer) clearTimeout(timer);
      txt(b, '…');
      api('POST', '/api/tablet/ignore', { kind: kind, id: id }, function (st, data) {
        if (st === 204) { refresh(); return; }
        cls(b, 'armed', false); txt(b, 'IGNORE');
        say(J.msg, st === 0 ? 'Can’t reach BrainHub.' : 'Not ignored: ' + ((data && data.error) || st));
      });
    });
    return b;
  }

  // -- the keyboard (design §6.3) -----------------------------------------------------------------
  var kb = new K.Keyboard([]), KB = { field: null };
  var ABC = [['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'], ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
             ['z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.']];
  var SYM = [['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'], ['-', '/', ':', ';', '(', ')', '&', '@', '"'],
             ['.', ',', '?', '!', '%', '+', '=', '*', '#']];
  var ROW_Y = [8, 98, 188, 278];
  (function () {
    var top = h('div'); top.id = 'kbtop'; root.appendChild(top);
    KB.label = h('div', 'label'); top.appendChild(KB.label);
    KB.text = h('div', 'txt'); top.appendChild(KB.text);
    onTap(top, function () { kbClose(); });
    KB.top = top;
    var sheet = h('div'); sheet.id = 'kb'; root.appendChild(sheet); KB.sheet = sheet;
    KB.sets = {};
    KB.sets.abc = letters(ABC, 'abc'); KB.sets.sym = letters(SYM, 'sym'); KB.sets.num = numeric();
    for (var s in KB.sets) if (KB.sets.hasOwnProperty(s)) sheet.appendChild(KB.sets[s]);
    keyPad(sheet, function (k) {
      var r = kb.press(k, now());
      if (r === 'done') kbDone(); else if (r === 'hide') kbClose(); else kbShow();
    });
    document.addEventListener('keydown', function (ev) {  // a physical keyboard, for testing on a PC
      if (!KB.field) return;
      var k = ev.keyCode, ch = ev.key;
      var map = { 8: 'bksp', 13: 'enter', 32: 'space', 27: 'hide' };
      var key = map[k] || (ch && ch.length === 1 ? ch : null);
      if (!key) return;
      ev.preventDefault();
      var r = kb.press(key, now());
      if (r === 'done') kbDone(); else if (r === 'hide') kbClose(); else kbShow();
    }, false);
  })();
  function key(set, k, label, x, y, w, extra, hgt) {
    var e = at(h('div', 'key' + (extra ? ' ' + extra : ''), label), x, y, w, hgt);
    e.setAttribute('data-k', k);
    if (hgt) e.style.lineHeight = (hgt - 2) + 'px';
    set.appendChild(e);
    return e;
  }
  function letters(rows, mode) {
    var set = h('div', 'keys'), i;
    for (i = 0; i < rows[0].length; i++) key(set, rows[0][i], rows[0][i], 16 + i * 92, ROW_Y[0], 84);
    key(set, 'bksp', '⌫', 936, ROW_Y[0], 72, 'glyph');
    for (i = 0; i < rows[1].length; i++) key(set, rows[1][i], rows[1][i], 62 + i * 92, ROW_Y[1], 84);
    key(set, 'enter', '↵', 890, ROW_Y[1], 118, 'glyph');
    if (mode === 'abc') KB.shift = key(set, 'shift', '⇧ SHIFT', 16, ROW_Y[2], 130, 'sp');
    else key(set, '\'', '\'', 16, ROW_Y[2], 130);
    for (i = 0; i < rows[2].length; i++) key(set, rows[2][i], rows[2][i], 154 + i * 92, ROW_Y[2], 84);
    key(set, 'hide', 'HIDE ⌄', 16, ROW_Y[3], 132, 'sp');
    key(set, mode === 'abc' ? 'mode:sym' : 'mode:abc', mode === 'abc' ? '123' : 'ABC', 156, ROW_Y[3], 100, 'sp');
    key(set, 'space', 'SPACE', 264, ROW_Y[3], 544, 'sp');
    KB['act_' + mode] = key(set, 'done', 'SAVE', 816, ROW_Y[3], 192, 'act sp');
    return set;
  }
  function numeric() {
    var set = h('div', 'keys'), pad = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '-', '0', '.'];
    for (var i = 0; i < pad.length; i++) {
      key(set, pad[i], pad[i] === '-' ? '−' : pad[i], 16 + (i % 3) * 124, ROW_Y[Math.floor(i / 3)], 116);
    }
    KB.units = h('div'); set.appendChild(KB.units);
    key(set, 'space', 'SPACE', 400, ROW_Y[3], 392, 'sp');
    key(set, 'bksp', '⌫', 816, ROW_Y[0], 192, 'glyph');
    key(set, 'mode:abc', 'ABC', 816, ROW_Y[1], 192, 'sp');
    KB.act_num = key(set, 'done', 'SET', 816, ROW_Y[2], 192, 'act sp', 170);
    return set;
  }
  function setUnits(units) {
    units = units || [];
    var k = units.join('|');
    if (KB.unitsKey === k) return;
    KB.unitsKey = k; clear(KB.units);
    kb = new K.Keyboard(units || []);
    for (var i = 0; i < units.length && i < 12; i++) {
      key(KB.units, 'unit:' + units[i], units[i], 400 + (i % 4) * 100, ROW_Y[Math.floor(i / 4)], 92, 'unit');
    }
  }
  // Keys fire on touch-down (no click delay); backspace repeats while held (380 ms, then 70 ms).
  function keyPad(container, fn) {
    var rep = null, repT = null;
    function stopRep() { if (repT) clearTimeout(repT); if (rep) clearInterval(rep); rep = repT = null; }
    function find(t) { while (t && t !== container) { if (t.getAttribute && t.getAttribute('data-k')) return t; t = t.parentNode; } return null; }
    function press(ev) {
      var k = find(ev.target);
      if (!k) return;
      ev.preventDefault();
      cls(k, 'down', true);
      var code = k.getAttribute('data-k');
      fn(code);
      if (code === 'bksp') repT = setTimeout(function () { rep = setInterval(function () { fn('bksp'); }, 70); }, 380);
    }
    function release() {
      stopRep();
      var downs = container.querySelectorAll('.down');
      for (var i = 0; i < downs.length; i++) cls(downs[i], 'down', false);
    }
    container.addEventListener('touchstart', function (ev) { S.lastTouch = now(); press(ev); }, false);
    container.addEventListener('touchend', release, false);
    container.addEventListener('touchcancel', release, false);
    container.addEventListener('mousedown', function (ev) { if (now() - S.lastTouch > 800) press(ev); }, false);
    container.addEventListener('mouseup', release, false);
    container.addEventListener('mouseleave', release, false);
  }
  // field: {key, label, kind: 'text'|'number', multiline, max, el, onDone(text)}
  function kbOpen(field) {
    if (KB.field && KB.field.el) cls(KB.field.el, 'cur', false);
    KB.field = field;
    kb.reset(store.get('bh.draft.' + field.key, ''), field.kind, field.multiline, field.max);
    txt(KB.label, field.label);
    txt(KB.act_abc, field.action || 'SAVE'); txt(KB.act_sym, field.action || 'SAVE'); txt(KB.act_num, field.action || 'SET');
    if (field.el) cls(field.el, 'cur', true);
    cls(KB.top, 'on', true); cls(KB.sheet, 'on', true);
    kbShow();
  }
  function kbShow() {
    if (!KB.field) return;
    store.set('bh.draft.' + KB.field.key, kb.text);  // a reload or a Fully Kiosk restart loses nothing
    for (var m in KB.sets) if (KB.sets.hasOwnProperty(m)) cls(KB.sets[m], 'on', m === kb.mode);
    if (KB.shift) { cls(KB.shift, 'lit', kb.shift !== 'off'); txt(KB.shift, kb.shift === 'lock' ? '⇪ CAPS' : '⇧ SHIFT'); }
    clear(KB.text);
    KB.text.appendChild(document.createTextNode(kb.text));
    KB.text.appendChild(h('span', 'caret'));
    KB.text.scrollTop = KB.text.scrollHeight;
    if (KB.field.el) fieldText(KB.field.el, kb.text, KB.field.placeholder);
  }
  function kbClose() {
    if (!KB.field) return;
    if (KB.field.el) { cls(KB.field.el, 'cur', false); fieldText(KB.field.el, kb.text, KB.field.placeholder); }
    KB.field = null;
    cls(KB.top, 'on', false); cls(KB.sheet, 'on', false);
    if (S.notesPending) { S.notesPending = false; S.keys.notes = null; render(); }  // held back while typing
  }
  function kbDone() {
    var f = KB.field, text = kb.text;
    kbClose();
    if (f && f.onDone) f.onDone(text);
  }
  function fieldText(el, text, placeholder) {
    var key = (text ? 'v:' : 'p:') + (text || placeholder || '');
    if (el._v === key) return;
    el._v = key; clear(el);
    el.appendChild(text ? document.createTextNode(text) : h('span', 'ph', placeholder || ''));
  }

  // -- sending: in order, kept on disk until the hub has it (design §6.5) ------------------------
  function flush() {
    var item = queue.peek();
    if (S.flushing || !item) { renderQueue(); return; }
    S.flushing = true;
    var path = item.kind === 'note' ? '/api/tablet/notes'
      : '/api/tablet/requests/' + encodeURIComponent(item.id) + '/answer';
    api('POST', path, item.kind === 'note' ? { text: item.text } : { answers: item.answers }, function (st, data) {
      S.flushing = false;
      if (st === 200 || st === 201) {
        queue.shift();
        say(J.msg, data && data.held ? 'Saved · held until the drive is back' : 'Saved', true);
        flush(); refresh(); return;
      }
      if (st === 401) { go('pair'); return; }
      if (st === 400 || st === 404 || st === 409) {
        queue.shift();
        if (item.kind === 'note') {  // refused: back into the note box, never dropped
          if (!store.get('bh.draft.note', '')) store.set('bh.draft.note', item.text);
          say(J.msg, 'Not saved (' + ((data && data.error) || st) + '): it is back in the note box.');
        } else {  // the request is gone or answered elsewhere: keep the values as a plain note
          queue.push({ kind: 'note', text: item.fallback });
          say(J.msg, 'That request was closed; your values were kept as a note.');
        }
        flush(); render(); return;
      }
      renderQueue();  // the hub is out of reach: the next good poll sends it
    });
  }
  function renderQueue() {
    var n = queue.size();
    if (n && J.queue) txt(J.queue, 'Waiting to send (' + n + ')');
    else if (J.queue) txt(J.queue, '');
  }

  // -- state: fetched, then long-polled (design §6.4) --------------------------------------------
  function startLoop() {
    S.loop += 1;
    if (S.waitXhr) { try { S.waitXhr.abort(); } catch (e) {} S.waitXhr = null; }  // the old browser has few connections
    fetchState(S.loop);
  }
  function later(id, ms) { setTimeout(function () { if (id === S.loop) fetchState(id); }, ms); }
  function linkUp(ok) {
    if (ok) backoff.ok();
    var failing = !ok && backoff.failing();
    cls(J.link, 'ok', !failing); cls(J.link, 'warn', failing);
    txt(J.linkText, failing ? 'retrying' : 'live');
    if (failing) { txt(home.msg, 'Can’t reach BrainHub. Retrying…'); txt(boot.msg, 'Can’t reach BrainHub. Retrying…'); }
    else if (home.msg._t === 'Can’t reach BrainHub. Retrying…') txt(home.msg, '');
  }
  function fetchState(id) {
    api('GET', '/api/tablet/state', null, function (st, data) {
      if (id !== S.loop) return;
      if (st === 200 && data) {
        S.state = data; S.version = data.version; linkUp(true);
        if (S.screen === 'boot' || S.screen === 'pair' || S.screen === 'setup') go('home'); else render();
        flush(); wait(id); return;
      }
      if (st === 401) { go('pair'); return; }
      if (st === 409) { go('setup'); later(id, 10000); return; }
      var d = backoff.fail(); linkUp(false); later(id, d);
    });
  }
  function wait(id) {
    S.waitXhr = api('GET', '/api/tablet/wait?since=' + S.version, null, function (st, data) {
      if (id !== S.loop) return;
      if (st === 200 && data) {
        linkUp(true);
        if (data.version !== S.version) fetchState(id); else { flush(); wait(id); }
        return;
      }
      if (st === 401) { go('pair'); return; }
      var d = backoff.fail(); linkUp(false); later(id, d);
    }, 35000);
  }
  function refresh() { startLoop(); }
  setInterval(function () { if (S.screen === 'home' || S.screen === 'journal') refresh(); }, 60000);  // the clock

  // -- rendering ------------------------------------------------------------------------------------
  function render() {
    if (S.screen === 'pair') {
      for (var i = 0; i < 6; i++) { txt(pair.boxes[i], pair.code.charAt(i)); cls(pair.boxes[i], 'cur', i === pair.code.length); }
      return;
    }
    var st = S.state;
    if (!st) return;
    setUnits(st.units);
    if (S.screen === 'home') {
      txt(home.clock, st.clock);
      txt(home.robot.sub, st.home.arm); cls(home.robot.dot, 'ok', st.home.arm_ok);
      txt(home.journal.sub, st.home.journal); cls(home.journal.dot, 'ok', true);
      txt(home.who, st.device ? 'paired as ' + st.device : 'on the PC (no pairing needed)');
      return;
    }
    if (S.screen !== 'journal') return;
    for (var t in J.tabs) if (J.tabs.hasOwnProperty(t)) { cls(J.tabs[t], 'sel', t === S.tab); cls(J.panes[t], 'on', t === S.tab); }
    txt(J.chip, st.requests.length ? st.requests.length : ''); show(J.chip, st.requests.length > 0);
    if (S.tab === 'today') renderToday(st);
    else if (S.tab === 'notes') renderNotes(st);
    else renderAgents(st);
  }
  function changed(name, value) {
    var k = JSON.stringify(value);
    if (S.keys[name] === k) return false;
    S.keys[name] = k; return true;
  }
  function renderToday(st) {
    if (!changed('today', st.today)) return;
    var box = J.scroll.today.inner; clear(box);
    if (!st.today.length) { var e = h('div', 'empty'); e.appendChild(h('div', 't', 'Nothing due, nothing waiting.')); box.appendChild(e); }
    for (var g = 0; g < st.today.length; g++) {
      var grp = st.today[g], gh = h('div', 'gh'); gh.appendChild(h('div', 'label', grp.title)); box.appendChild(gh);
      for (var r = 0; r < grp.rows.length; r++) {
        var row = h('div', 'row'), it = grp.rows[r];
        row.appendChild(h('div', 't ellip', it.text));
        row.appendChild(h('div', 'm mono ' + (it.attention ? 'amber' : 'dim'), it.meta));
        box.appendChild(row);
      }
      if (grp.more) { var more = h('div', 'row'); more.appendChild(h('div', 't faint', '+' + grp.more + ' more on the PC')); box.appendChild(more); }
    }
    J.scroll.today.to(0);
  }
  function renderNotes(st) {
    if (KB.field) { S.notesPending = true; return; }  // never pull the list from under the keyboard
    if (!changed('notes', [st.requests, st.notes])) { renderQueue(); return; }
    var box = J.scroll.notes.inner, keep = J.scroll.notes.y; clear(box);
    for (var i = 0; i < st.requests.length; i++) box.appendChild(requestCard(st.requests[i]));
    var gh = h('div', 'gh'); gh.appendChild(h('div', 'label', 'New note')); box.appendChild(gh);
    var nf = h('div', 'notefield'), f = h('div', 'field');
    nf.appendChild(f); fieldText(f, store.get('bh.draft.note', ''), 'Write a note…');
    var save = at(h('div', 'btn primary', 'SAVE'), 836, 0, 140); nf.appendChild(save);
    J.queue = at(h('div', 'abs amber'), 836, 60, 160); J.queue.style.fontSize = '12px'; nf.appendChild(J.queue);
    var noteField = { key: 'note', label: 'NOTE', kind: 'text', multiline: true, max: 20000, el: f,
                      placeholder: 'Write a note…', action: 'SAVE', onDone: saveNote };
    onTap(f, function () { kbOpen(noteField); });
    onTap(save, function () { saveNote(store.get('bh.draft.note', '')); });
    box.appendChild(nf);
    if (st.notes.length) { var rg = h('div', 'gh'); rg.appendChild(h('div', 'label', 'Recent')); box.appendChild(rg); }
    for (var n = 0; n < st.notes.length; n++) {
      var note = st.notes[n], row = h('div', 'note');
      row.appendChild(h('div', 'w mono dim', note.when));
      if (note.answer_to) row.appendChild(h('div', 'ans faint ellip', 'measurements'));
      row.appendChild(h('div', 't', note.text));
      row.appendChild(ignoreButton(860, 12, 'note', note.id));
      box.appendChild(row);
    }
    renderQueue();
    J.scroll.notes.to(keep);
  }
  function saveNote(text) {
    if (!text || !text.replace(/\s+/g, '')) { say(J.msg, 'The note is empty.'); return; }
    queue.push({ kind: 'note', text: text });
    store.remove('bh.draft.note');
    S.keys.notes = null;
    render(); flush();
  }
  function requestCard(req) {
    var card = h('div', 'card'), head = h('div', 'head');
    head.appendChild(h('div', 't ellip', req.title));
    head.appendChild(h('div', 'm dim ellip', (req.project ? req.project + ' · ' : '') + req.when));
    card.appendChild(head);
    var fields = [];
    for (var i = 0; i < req.items.length; i++) (function (i) {
      var row = h('div', 'item'), f = h('div', 'field mono');
      row.appendChild(h('div', 't ellip', req.items[i]));
      row.appendChild(f); card.appendChild(row);
      var spec = { key: 'req:' + req.id + ':' + i, label: req.items[i], kind: 'number', multiline: false, max: 200,
                   el: f, placeholder: 'tap to enter', action: 'SET' };
      spec.onDone = function () { nextEmpty(fields, i); };
      fieldText(f, store.get('bh.draft.' + spec.key, ''), spec.placeholder);
      fields.push(spec);
      onTap(f, function () { kbOpen(spec); });
    })(i);
    var act = h('div', 'act');
    var send = at(h('div', 'btn primary', 'SEND'), 16, 12, 140); act.appendChild(send);
    act.appendChild(ignoreButton(172, 12, 'request', req.id));
    onTap(send, function () { sendAnswers(req, fields); });
    card.appendChild(act);
    return card;
  }
  function nextEmpty(fields, from) {  // SET moves on to the next empty item of the checklist
    for (var j = from + 1; j < fields.length; j++) if (!store.get('bh.draft.' + fields[j].key, '')) { kbOpen(fields[j]); return; }
  }
  function sendAnswers(req, fields) {
    var answers = {}, any = false, lines = ['Measurements: ' + req.title + (req.project ? ' (' + req.project + ')' : '')];
    for (var i = 0; i < fields.length; i++) {
      var v = store.get('bh.draft.' + fields[i].key, '');
      if (v && v.replace(/\s+/g, '')) { answers[i] = v.replace(/^\s+|\s+$/g, ''); any = true; lines.push('- ' + req.items[i] + ': ' + answers[i]); }
    }
    if (!any) { say(J.msg, 'Fill in at least one value.'); return; }
    queue.push({ kind: 'answer', id: req.id, answers: answers, fallback: lines.join('\n') });
    for (var k = 0; k < fields.length; k++) store.remove('bh.draft.' + fields[k].key);
    S.keys.notes = null;
    render(); flush();
  }
  function renderAgents(st) {
    if (changed('agents', st.agents)) {
      clear(J.agents);
      if (!st.agents.length) { var e = h('div', 'empty'); e.appendChild(h('div', 't', 'No Claude Code sessions.')); J.agents.appendChild(e); }
      for (var i = 0; i < st.agents.length && i < 8; i++) {
        var a = st.agents[i], row = h('div', 'agent');
        row.appendChild(h('div', 'dot' + (a.status === 'running' ? ' ok' : (a.status === 'waiting' ? ' warn' : ''))));
        row.appendChild(h('div', 't ellip', a.project + (a.part && a.part !== '—' ? ' · ' + a.part : '')));
        row.appendChild(h('div', 'm mono ' + (a.status === 'waiting' ? 'amber' : 'dim'), a.label));
        J.agents.appendChild(row);
      }
    }
    if (changed('usage', st.usage)) {
      clear(J.usage);
      for (var u = 0; u < st.usage.length; u++) {
        var m = st.usage[u], box = at(h('div', 'meter'), 24, 24 + u * 200);
        box.appendChild(h('div', 'label', m.label));
        box.appendChild(h('div', 'pct mono' + (m.warn ? ' amber' : ''), m.pct === null ? '—' : m.pct + '%'));
        var track = h('div', 'track'), fill = h('div', 'fill' + (m.warn ? ' warn' : ''));
        fill.style.width = (m.pct === null ? 0 : Math.max(0, Math.min(100, m.pct)) * 3.36) + 'px';
        track.appendChild(fill); box.appendChild(track);
        box.appendChild(h('div', 'txt mono dim', m.text));
        J.usage.appendChild(box);
      }
    }
  }

  // -- start --------------------------------------------------------------------------------------
  (function fit() {  // 1024x600 on the device; scaled to fit anywhere else (a PC preview)
    var w = window.innerWidth || 1024, hgt = window.innerHeight || 600;
    var s = Math.min(w / 1024, hgt / 600);
    var v = s === 1 ? '' : 'scale(' + s + ')';
    root.style.webkitTransform = v; root.style.transform = v;
    root.style.left = Math.max(0, (w - 1024 * s) / 2) + 'px';
  })();
  document.addEventListener('touchmove', function (ev) { ev.preventDefault(); }, false);  // the page never scrolls
  startLoop();
})();
