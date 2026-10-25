// Pure motion model for the ATLAS dot (voice-input decisions VI8): the radius it should have at a
// moment of a spoken line, from the line's loudness envelope (one 0–100 value per frame_ms, sent by
// the voice worker as the line starts to sound), and one smoothing step towards it. No DOM.

export const BOX = 64;   // the dot's viewBox: 0 0 64 64, the logo's grid
export const REST = 18;  // the radius at rest
export const PEAK = 30;  // the radius at the loudest frame (the box's edge is 32)

// The level (0–1) `elapsedMs` into the line, or null once the envelope has run out. The square root
// lifts the quieter syllables, so the dot moves with the words and not only with the stressed ones.
export function levelAt(env, frameMs, elapsedMs) {
  if (!Array.isArray(env) || !(frameMs > 0) || !(elapsedMs >= 0)) return null;
  const i = Math.floor(elapsedMs / frameMs);
  if (i >= env.length) return null;
  const v = Number(env[i]);
  return Number.isFinite(v) ? Math.sqrt(Math.min(100, Math.max(0, v)) / 100) : 0;
}

export const radiusFor = (level) => REST + (PEAK - REST) * (level || 0);

// One step from `r` towards `target` after `dtMs`: quick to swell (a syllable's onset), slower to
// settle, independent of the frame rate.
export function step(r, target, dtMs) {
  const tau = target > r ? 35 : 110;
  const k = 1 - Math.exp(-Math.max(0, dtMs) / tau);
  return r + (target - r) * k;
}

// Close enough to rest to stop the animation and set it exactly.
export const settled = (r) => Math.abs(r - REST) < 0.05;
