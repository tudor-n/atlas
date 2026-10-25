// The ATLAS dot (voice-input decisions VI8): the logo's centre node on its own, ATLAS blue. While ATLAS
// speaks it grows and shrinks with the line: voice.speaking brings the line's loudness envelope as it
// starts to sound, and the dot plays it frame by frame (atlas-dot-model.js). It settles on voice.quiet,
// when the envelope runs out, or when the socket drops. Besides the switch knob, this is the one thing
// that moves (phase 4.5 spec §2, amended): only the circle's r changes, through requestAnimationFrame,
// and under prefers-reduced-motion it stays still.
import * as api from '../api.js';
import { BOX, REST, levelAt, radiusFor, step, settled } from './atlas-dot-model.js';

const SVG = 'http://www.w3.org/2000/svg';

export function atlasDot({ cls = 'atl-dot' } = {}) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', `0 0 ${BOX} ${BOX}`);
  svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const hub = document.createElementNS(SVG, 'circle');
  hub.setAttribute('class', 'hub');
  hub.setAttribute('cx', String(BOX / 2));
  hub.setAttribute('cy', String(BOX / 2));
  hub.setAttribute('r', String(REST));
  svg.appendChild(hub);

  let r = REST;
  let line = null;     // {env, frameMs, t0} while a line plays
  let frame = 0;       // the pending animation frame, or 0
  let last = 0;
  const still = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

  const draw = () => hub.setAttribute('r', r.toFixed(2));

  function tick(now) {
    frame = 0;
    const dt = last ? now - last : 16;
    last = now;
    const level = line ? levelAt(line.env, line.frameMs, now - line.t0) : null;
    if (line && level === null) line = null;  // the envelope ran out
    r = step(r, radiusFor(level), dt);
    if (!line && settled(r)) {
      r = REST;
      draw();
      last = 0;
      return;
    }
    draw();
    frame = requestAnimationFrame(tick);
  }

  const run = () => { if (!frame) frame = requestAnimationFrame(tick); };

  function speak({ data }) {
    if (still()) return;
    const env = data && Array.isArray(data.env) ? data.env : [];
    const frameMs = data && data.frame_ms > 0 ? data.frame_ms : 40;
    line = { env, frameMs, t0: performance.now() };
    run();
  }

  function quiet() {
    line = null;
    if (r !== REST) run();  // settle from wherever it is
  }

  const subs = [api.onEvent('voice.speaking', speak), api.onEvent('voice.quiet', quiet), api.onEvent('ws.close', quiet)];
  svg.destroy = () => {
    for (const off of subs.splice(0)) off();
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    line = null;
  };
  return svg;
}
