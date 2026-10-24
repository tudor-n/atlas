// The two drafts' localStorage keys from before the rename (rename spec §1: ATLAS was BrainHub).
// A draft is read from its old key once, when the new key is absent, and moved under the new one,
// so nothing the owner typed before the update is lost.
export const LEGACY_KEYS = Object.freeze({
  'atlas.setup.v1': 'brainhub.setup.v1',
  'atlas.widget.draft': 'brainhub.widget.draft',
});

// `storage` is a localStorage or null; every access may throw (blocked or full storage).
export function adoptLegacy(storage, key) {
  const old = LEGACY_KEYS[key];
  try {
    if (!storage || !old || storage.getItem(key) !== null) return;
    const value = storage.getItem(old);
    if (value === null) return;
    storage.setItem(key, value);
    storage.removeItem(old);
  } catch (_) { /* best effort: the old key stays for the next read */ }
}
