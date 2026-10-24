// Settings → Tablet (phase 3 design §4, phase 4.5 §4): the "Tablet over Wi-Fi" switch and address,
// pairing, paired devices, and the measurement units. `ui` is Settings' section context: its action
// bar (SAVE and the status line) and the rail's dot.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { button, dot, switchEl } from '../components/ui.js';
import { twice } from '../press.js';
import { countdown, lanLine, parseUnits } from './tablet-model.js';
import { tabletDot } from './settings-model.js';
import { prefRow, groupHead, intro, failLine } from './settings-sections.js';

export async function renderTabletSettings(box, isDead, ui) {
  const { bar, setDot = () => {} } = ui;
  const pairBox = el('div', {});
  const devBox = el('div', {});
  const devHead = groupHead('PAIRED DEVICES');
  let codeTimer = null;
  let code = null;  // the pairing code on show, kept across redraws until it runs out or is used
  let devices = null;  // how many were paired at the last look: one more means the code was used
  let lanOn = true;  // Tablet over Wi-Fi as saved: while it is off, a code can't be used yet
  const say = (text, ok) => bar.say(text, ok);

  async function save(patch, done) {
    say('');
    try {
      await api.put('/api/settings', patch);
      if (!isDead()) { say(done, true); await load(); }
    } catch (e) { if (!isDead()) say(e.message); }
  }

  function draw(s) {
    clear(box);
    setDot('tablet', tabletDot(s));
    lanOn = !!s.tablet_lan;
    // The small white switch, like every Settings preference: its position is a draft until SAVE,
    // so it can't claim "live". The dot and the line beside it say whether it is listening.
    const on = switchEl({ on: !!s.tablet_lan, small: true, title: 'Tablet over Wi-Fi' });
    on.id = 'tablet-lan';
    const addr = el('input', { type: 'text', id: 'tablet-address', class: 'm', value: s.tablet_address, placeholder: '192.168.0.200', spellcheck: 'false' });
    const units = el('input', { type: 'text', id: 'tablet-units', class: 'm wide', value: s.tablet_units.join(', '), spellcheck: 'false' });
    const lan = lanLine(s);
    box.append(
      intro('The workbench tablet reaches ATLAS over your home Wi-Fi at one fixed address. '
        + 'Reserve it for this laptop in your router, and let the port through the firewall.'),
      el('div', { class: 'pfs' },
        prefRow('TABLET OVER WI-FI', el('div', { class: 'pair' }, on,
          el('span', { class: 'st-lan' }, dot(s.listening ? 'g' : s.tablet_lan ? 'a' : ''), lan)), { forId: 'tablet-lan' }),
        prefRow("THIS LAPTOP'S HOME ADDRESS", el('div', { class: 'pair' }, addr,
          s.this_address && s.this_address !== s.tablet_address
            ? button(`USE ${s.this_address} (NOW)`, { size: 40, onclick: () => { addr.value = s.this_address; } })
            : null), { forId: 'tablet-address', hint: 'The address the tablet types in. It must be this laptop’s address on the home network.' }),
        prefRow('UNITS', units, { forId: 'tablet-units', hint: 'On the measurement keypad, separated by commas.' })),
      el('section', { class: 'st-grp' }, groupHead('PAIRING'), pairBox),
      el('section', { class: 'st-grp' }, devHead, devBox));
    bar.set(button('SAVE', { primary: true, onclick: () => save({
      tablet_lan: on.isOn(), tablet_address: addr.value.trim(), tablet_units: parseUnits(units.value),
    }, 'Saved.') }));
    drawPair();
  }

  function drawPair() {
    if (codeTimer) { clearInterval(codeTimer); codeTimer = null; }
    clear(pairBox);
    if (code && code.until <= Date.now()) code = null;
    if (!code) {
      pairBox.appendChild(el('div', { class: 'pf' },
        el('div', { class: 'pfl' }, el('span', { class: 'lb' }, 'NEW DEVICE'),
          el('p', { class: 'hint' }, lanOn ? 'Shows a one-time code to type on the tablet.'
            : 'Shows a one-time code to type on the tablet. Turn on Tablet over Wi-Fi first: until then the tablet can’t reach ATLAS.')),
        el('div', { class: 'pfc' }, button('PAIR A DEVICE', { onclick: async () => {
          try {
            const c = await api.post('/api/devices/pairing-code', {});
            if (!isDead()) { code = { code: c.code, until: Date.now() + c.expires_in * 1000 }; drawPair(); }
          } catch (e) { say(e.message); }
        } }))));
      return;
    }
    const left = el('span', { class: 'dl' });
    const tick = () => {
      const s = (code.until - Date.now()) / 1000;
      if (s <= 0 || isDead()) { code = null; drawPair(); return; }
      left.textContent = `VALID FOR ${countdown(s)}`;
    };
    pairBox.appendChild(el('div', { class: lanOn ? 'st-code' : 'st-code off' },
      el('span', { class: 'lb' }, 'ON THE TABLET, TYPE THIS CODE'),
      el('div', { class: 'big', 'aria-live': 'polite' }, code.code), left,
      lanOn ? null : el('p', { class: 'why' }, "Tablet over Wi-Fi is off, so the tablet can't reach ATLAS to use this code. Turn it on and save.")));
    tick();
    codeTimer = setInterval(tick, 1000);
  }

  const stamp = (iso) => (iso ? new Date(iso).toLocaleString() : 'never');

  async function loadDevices() {
    try {
      const list = await api.get('/api/devices');
      if (isDead()) return;
      if (devices !== null && list.length > devices && code) { code = null; drawPair(); }  // it was used
      devices = list.length;
      devHead.setCount(list.length);
      clear(devBox);
      if (!list.length) { devBox.appendChild(el('div', { class: 'empty' }, 'None yet.')); return; }
      for (const d of list) {
        const revoke = button('REVOKE', { amber: true, size: 40, title: `Revoke ${d.name}` });
        twice(revoke, { onGo: async () => {
          try { await api.del(`/api/devices/${encodeURIComponent(d.id)}`); if (!isDead()) { say('Revoked.', true); loadDevices(); } } catch (e) { say(e.message); }
        } });
        devBox.appendChild(el('div', { class: 'row' },
          el('span', { class: 'grow' }, el('span', { class: 'dn' }, d.name),
            el('span', { class: 'dm' }, `PAIRED ${stamp(d.paired_at)} · LAST SEEN ${d.last_seen ? stamp(d.last_seen) : 'NEVER'}`)),
          revoke));
      }
    } catch (e) {
      if (!isDead()) clear(devBox).appendChild(failLine(`Couldn't list the devices: ${e.message}`));
    }
  }

  async function load() {
    try {
      const s = await api.get('/api/tablet-lan');
      if (!isDead()) { draw(s); await loadDevices(); }
    } catch (e) {
      if (!isDead()) clear(box).appendChild(failLine(`Couldn't read the tablet settings: ${e.message}`));
    }
  }

  await load();
  const off = api.onEvent('devices.changed', () => { loadDevices(); });
  return () => { off(); if (codeTimer) clearInterval(codeTimer); };
}
