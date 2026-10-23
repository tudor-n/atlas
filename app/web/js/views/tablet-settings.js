// Settings → Tablet (phase 3 design §4): the "Tablet over Wi-Fi" switch and address, pairing,
// paired devices, and the measurement units.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { countdown, lanLine, parseUnits } from './tablet-model.js';

export async function renderTabletSettings(box, isDead) {
  const msg = el('div', { role: 'alert' });
  const pairBox = el('div', {});
  const devBox = el('div', {});
  let codeTimer = null;

  function say(text, ok) {
    clear(msg);
    if (text) msg.appendChild(el('div', { class: ok ? 'status' : 'error' }, text));
  }

  async function save(patch, done) {
    say('');
    try {
      await api.put('/api/settings', patch);
      if (!isDead()) { say(done, true); await load(); }
    } catch (e) { if (!isDead()) say(e.message); }
  }

  function draw(s) {
    clear(box);
    const on = el('input', { type: 'checkbox', id: 'tablet-lan', checked: s.tablet_lan || null });
    const addr = el('input', { type: 'text', id: 'tablet-address', value: s.tablet_address, placeholder: '192.168.0.200', spellcheck: 'false' });
    const units = el('input', { type: 'text', id: 'tablet-units', value: s.tablet_units.join(', '), spellcheck: 'false' });
    box.appendChild(el('div', {},
      el('p', { class: 'muted' }, 'The workbench tablet reaches BrainHub over your home Wi-Fi at one fixed address. '
        + 'Reserve it for this laptop in your router, and let the port through the firewall.'),
      el('div', { class: 'pref' }, el('label', { class: 'check', for: 'tablet-lan' }, on, 'Tablet over Wi-Fi')),
      el('div', { class: 'pref' }, el('label', { for: 'tablet-address' }, "This laptop's home address"), addr,
        s.this_address && s.this_address !== s.tablet_address
          ? el('button', { type: 'button', onclick: () => { addr.value = s.this_address; } }, `Use ${s.this_address} (now)`)
          : null),
      el('p', { class: s.listening ? 'status' : 'meta' }, lanLine(s)),
      el('div', { class: 'pref' }, el('label', { for: 'tablet-units' }, 'Units on the measurement keypad'), units),
      el('div', { class: 'actions' },
        el('button', { type: 'button', class: 'primary', onclick: () => save({
          tablet_lan: on.checked, tablet_address: addr.value.trim(), tablet_units: parseUnits(units.value),
        }, 'Saved.') }, 'Save')),
      msg,
      el('h3', {}, 'Pairing'), pairBox,
      el('h3', {}, 'Paired devices'), devBox));
    drawPair(null);
  }

  function drawPair(code) {
    if (codeTimer) { clearInterval(codeTimer); codeTimer = null; }
    clear(pairBox);
    if (!code) {
      pairBox.appendChild(el('div', { class: 'actions' }, el('button', { type: 'button', onclick: async () => {
        try { const c = await api.post('/api/devices/pairing-code', {}); if (!isDead()) drawPair(c); } catch (e) { say(e.message); }
      } }, 'Pair a device')));
      return;
    }
    const until = Date.now() + code.expires_in * 1000;
    const left = el('span', { class: 'meta' });
    const tick = () => {
      const s = (until - Date.now()) / 1000;
      if (s <= 0 || isDead()) { drawPair(null); return; }
      left.textContent = `valid for ${countdown(s)}`;
    };
    pairBox.appendChild(el('div', {},
      el('p', {}, 'On the tablet, type this code:'),
      el('div', { class: 'big-pct' }, code.code), left));
    tick();
    codeTimer = setInterval(tick, 1000);
  }

  async function loadDevices() {
    try {
      const list = await api.get('/api/devices');
      if (isDead()) return;
      clear(devBox);
      if (!list.length) { devBox.appendChild(el('p', { class: 'empty' }, 'None yet.')); return; }
      devBox.appendChild(el('ul', { class: 'rows' }, list.map((d) => el('li', { class: 'row' },
        el('span', { class: 'row-main' }, d.name, el('span', { class: 'meta' },
          ` · paired ${new Date(d.paired_at).toLocaleString()} · last seen ${d.last_seen ? new Date(d.last_seen).toLocaleString() : 'never'}`)),
        el('button', { type: 'button', onclick: async () => {
          try { await api.del(`/api/devices/${encodeURIComponent(d.id)}`); if (!isDead()) { say('Revoked.', true); loadDevices(); } } catch (e) { say(e.message); }
        } }, 'Revoke')))));
    } catch (e) {
      if (!isDead()) clear(devBox).appendChild(el('div', { class: 'error' }, `Couldn't list the devices: ${e.message}`));
    }
  }

  async function load() {
    try {
      const s = await api.get('/api/tablet-lan');
      if (!isDead()) { draw(s); await loadDevices(); }
    } catch (e) {
      if (!isDead()) clear(box).appendChild(el('div', { class: 'error', role: 'alert' }, `Couldn't read the tablet settings: ${e.message}`));
    }
  }

  await load();
  const off = api.onEvent('devices.changed', () => { drawPair(null); loadDevices(); });
  return () => { off(); if (codeTimer) clearInterval(codeTimer); };
}
