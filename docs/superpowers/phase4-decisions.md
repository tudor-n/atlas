# Phase 4 (voice and sass): build-time decisions

Rulings made while building phase 4, as amendments to the plan (`plans/2026-10-23-brainhub-phase4.md`) and the design (`specs/2026-10-23-brainhub-phase4-design.md`). Numbered V1….

- **V1 (Task 1) `voice_speed` takes ints and floats.** The generic rule rejects a value whose type differs from its default. A slider or JSON can send `1` for `1.0`, so `voice_speed` and the blend weights accept any finite non-bool number. NaN and infinity are refused.
- **V2 (Task 1) Voice ids are checked by shape only** (`bm_george`: two letters, `_`, a name). The voices file may not be downloaded yet when Settings saves, so an id that the file lacks is reported by the worker when it speaks ("unknown voice"), not refused by the settings.
