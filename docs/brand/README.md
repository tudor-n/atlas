# The ATLAS and DUMMY logos

From the logo handoff (2026-10-24): the production SVGs, without their provenance metadata. DUMMY is
the 6-DOF arm (its own repo, `tudor-n/6-dof-arm`, carries its full set); ATLAS uses DUMMY's mark
only on the tablet's ROBOT tile.

| File | Use |
|---|---|
| `atlas-mark-dark.svg` / `-light.svg` | The bare mark (64 grid): white ink on dark, black ink on white |
| `atlas-icon-dark.svg` / `-light.svg` | The 192 launcher tile (radius 42); the dark one is the app icon and favicon |
| `dummy-mark-dark.svg` | DUMMY's mark, for the tablet's ROBOT tile |

Rules from the handoff, as the code applies them:
- **The knockout matches what the mark sits on.** In the web app the marks are inline SVG: ink is
  `currentColor`, the knockout parts (`.ks` strokes, `.kf` fills) take `--mk-ground`, which a
  pressed (white) cell sets to white. The tablet's pressed ROBOT tile sets them to `#262626`.
- **ATLAS blue `#3ec7ff` (`--atlas`) is only ever the centre node.** It is not a UI colour.
  The ATLAS screen's talking dot is that centre node on its own, without the ring and squares (voice-input decisions VI8).
- **Top-bar lockup:** the 26 px mark, a 10 px gap, the wordmark in mono 600 13 px tracked .14em,
  with -.14em right margin to cancel the trailing tracking. Below 1100 px the app shows the mark alone.
- **App icons:** `app/desktop/src-tauri/icons/` are rendered from `atlas-icon-dark.svg` with the
  tile's inner border kept at one device pixel at every size.
