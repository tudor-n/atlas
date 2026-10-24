// Browse (phase 4.5 design §3, §4): a 240px rail (DEADLINES, PEOPLE, then the map's projects and
// courses) beside the selected page, which browse.js draws. The routes stay an API: `#/deadlines`,
// `#/people` and `#/project/<slug>` open that page; `#/browse` opens the last-used one (default
// deadlines) and keeps its own hash. The map is cached here and refreshed on `map.changed`.
import * as api from '../api.js';
import { el, clear } from '../dom.js';
import { renderDeadlines, renderPeople, renderProject } from './browse.js';
import { browsePath, railPlaces } from './browse-model.js';
import { browseTarget } from '../nav-model.js';

const KEY = 'atlas.browse';
let mapCache = null;    // the last /api/map answer
let mapLoading = null;  // its pending fetch

function loadLast() {
  try { return window.localStorage.getItem(KEY); } catch (_) { return null; }
}
function saveLast(path) {
  if (browseTarget(path) !== path) return;  // only a valid route path is remembered
  try { window.localStorage.setItem(KEY, path); } catch (_) { /* blocked or full storage */ }
}

function fetchMap(force) {
  if (mapCache && !force) return Promise.resolve(mapCache);
  if (!mapLoading) {
    mapLoading = api.get('/api/map')
      .then((m) => { mapCache = m; return m; })
      .finally(() => { mapLoading = null; });
  }
  return mapLoading;
}

function railRow(path, text, current, extra) {
  return el('a', { class: current ? 'row cur' : 'row', href: `#/${path}`, 'data-press': true,
    'aria-current': current ? 'page' : null }, el('span', { class: 'grow' }, text), extra || null);
}

export async function render(container, params) {
  const path = browsePath(params, loadLast());
  saveLast(path);
  let dead = false;

  const rail = el('nav', { class: 'rail', 'aria-label': 'Browse' });
  const main = el('div', { class: 'main' });
  container.appendChild(el('div', { class: 'vw split browse' }, rail, main));

  function drawRail(map, error) {
    const places = railPlaces(map);
    clear(rail).append(
      railRow('deadlines', 'DEADLINES', path === 'deadlines'),
      railRow('people', 'PEOPLE', path === 'people'),
      el('div', { class: 'sect-h' }, el('h2', { class: 'lb' }, 'Projects'),
        places.length ? el('span', { class: 'n' }, String(places.length)) : null),
      ...(places.length
        ? places.map((p) => railRow(p.path, p.name, path === p.path,
          p.kind === 'course' ? el('span', { class: 'sb' }, 'COURSE') : null))
        : [map || error ? el('div', { class: 'empty' }, error ? "Couldn't load the map." : 'No projects yet.') : '']));
  }

  // The rail at once from the cache (when there is one), refreshed from the hub in the background;
  // the first visit waits for the map, since a project page needs it.
  drawRail(mapCache);
  const fresh = fetchMap(true).then(
    (m) => { if (!dead) drawRail(m); return m; },
    (e) => { if (!dead && !mapCache) drawRail(null, e); return mapCache; });
  const map = mapCache || await fresh;
  if (dead) return undefined;

  const [kind, slug] = path.split('/');
  let inner = null;
  if (kind === 'project') inner = await renderProject(main, [slug], undefined, { map });
  else if (kind === 'people') inner = await renderPeople(main, [], undefined, { map });
  else inner = await renderDeadlines(main, [], undefined, { map });

  const off = api.onEvent('map.changed', async () => {
    try {
      const m = await fetchMap(true);
      if (!dead) drawRail(m);
    } catch (_) { /* keep the rail as it was */ }
  });
  return () => {
    dead = true;
    off();
    if (typeof inner === 'function') inner();
  };
}
