// Settings (phase 4.5 design §4): a 240px rail of sections with status dots, and the one selected
// section on the right with its own action bar. Each section is its own route, #/settings/<section>
// (nav-model.settingsSection; #/settings is the brain map), and only that section is loaded.
// The old in-page anchors #/settings/tablet and #/settings/voice are sections, and their roots keep
// the ids `tablet` and `voice`.
import * as api from '../api.js';
import { el } from '../dom.js';
import { sectionHead } from '../components/ui.js';
import { SETTINGS_SECTIONS, settingsSection } from '../nav-model.js';
import { backupDot, integrationDot, tabletDot, voiceDot, syncDot } from './settings-model.js';
import { syncNotes } from './claude-sync-model.js';
import {
  sectionBar, groupHead, mapSection, hierarchySection, preferencesSection, backupSection, claudeSection, aboutSection,
} from './settings-sections.js';
import { renderTabletSettings } from './tablet-settings.js';
import { renderVoiceSettings } from './voice-settings.js';
import { renderClaudeSync } from './claude-sync.js';

const RENDER = {
  map: mapSection,
  hierarchy: hierarchySection,
  preferences: preferencesSection,
  backup: backupSection,
  claude: claudeSection,
  tablet: (ctx) => renderTabletSettings(ctx.body, ctx.isDead, ctx),
  voice: (ctx) => renderVoiceSettings(ctx.body, ctx.isDead, ctx),
  sync: (ctx) => renderClaudeSync(ctx.body, ctx.isDead, ctx),
  about: aboutSection,
};
// The heading over each section (the rail's labels are short).
const TITLE = {
  map: 'BRAIN MAP', hierarchy: 'HIERARCHY', preferences: 'PREFERENCES', backup: 'BACKUP',
  claude: 'CLAUDE CODE INTEGRATION', tablet: 'TABLET', voice: 'VOICE', sync: 'CLAUDE CODE SYNC', about: 'ABOUT',
};

// The rail's dots for the sections that have a health of their own. Each check is independent and
// quiet: a failed fetch just leaves its dot off.
const DOTS = {
  claude: async () => integrationDot(await api.get('/api/integration')),
  tablet: async () => tabletDot(await api.get('/api/tablet-lan')),
  voice: async () => voiceDot(await api.get('/api/voice')),
  sync: async () => { const v = await api.get('/api/claude-sync'); return syncDot(v, syncNotes(v)); },
};

export async function render(container, params = []) {
  const id = settingsSection(params[0]);
  let dead = false;
  const subs = [];
  const isDead = () => dead;

  const dots = {};
  const rail = el('nav', { class: 'rail st-rail', 'aria-label': 'Settings sections' }, sectionHead('SETTINGS'),
    SETTINGS_SECTIONS.map((s) => {
      dots[s.id] = el('span', { class: 'dot', hidden: true, 'aria-hidden': 'true' });
      const cur = s.id === id;
      return el('a', { class: cur ? 'row cur' : 'row', href: `#/settings/${s.id}`, 'data-press': '', 'aria-current': cur ? 'page' : null },
        el('span', { class: 'grow' }, s.label), dots[s.id]);
    }));
  const setDot = (sid, kind) => {
    const d = dots[sid];
    if (!d) return;
    d.className = kind ? `dot ${kind}` : 'dot';
    d.hidden = !kind;
  };

  const head = groupHead(TITLE[id]);
  head.classList.add('st-head');
  const body = el('div', { class: 'st-body', id });
  const bar = sectionBar(SETTINGS_SECTIONS.find((s) => s.id === id).label);
  container.appendChild(el('div', { class: 'vw split settings' }, rail,
    el('div', { class: 'main st-main' }, head, el('div', { class: 'st-scroll' }, body), bar.node)));

  let state = {};
  try { state = await api.get('/api/state'); } catch (_) { /* the shell banner already says so */ }
  if (dead) return () => {};
  setDot('backup', backupDot(state.backup));
  for (const [sid, check] of Object.entries(DOTS)) {
    check().then((k) => { if (!dead) setDot(sid, k); }, () => {});
  }
  subs.push(api.onEvent('backup.status', ({ data }) => { if (!dead) setDot('backup', backupDot(data)); }));

  const ctx = { body, bar, state, head, isDead, setDot };
  try {
    const off = await RENDER[id](ctx);
    if (typeof off === 'function') subs.push(off);
  } catch (e) {
    console.error(e);
    if (!dead) bar.say(`Couldn't load this section: ${e.message}`);
  }
  return () => { dead = true; for (const off of subs) off(); };
}
