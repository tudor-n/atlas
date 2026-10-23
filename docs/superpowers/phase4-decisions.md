# Phase 4 (voice and sass): build-time decisions

Rulings made while building phase 4, as amendments to the plan (`plans/2026-10-23-brainhub-phase4.md`) and the design (`specs/2026-10-23-brainhub-phase4-design.md`). Numbered V1….

- **V1 (Task 1) `voice_speed` takes ints and floats.** The generic rule rejects a value whose type differs from its default. A slider or JSON can send `1` for `1.0`, so `voice_speed` and the blend weights accept any finite non-bool number. NaN and infinity are refused.
- **V2 (Task 1) Voice ids are checked by shape only** (`bm_george`: two letters, `_`, a name). The voices file may not be downloaded yet when Settings saves, so an id that the file lacks is reported by the worker when it speaks ("unknown voice"), not refused by the settings.
- **V3 (Task 3) `_voice/` is skipped by the notes index too**, not only protected from the filing agent. Otherwise recall could quote the quips as if they were the owner's notes.
- **V4 (Task 3) The quote file is seeded twice:** in setup's own commit (a new brain), and as a startup step with its own commit, "add the voice quotes" (a brain set up before phase 4). It's never overwritten, whatever its state.
- **V5 (Task 3) The seed has 107 quotes** (30 agent_finished, 30 agent_waiting, 25 reminder, 12 readback, 10 catch_up), in a roughly even mix of the three styles, and they follow the wording rules. A test enforces at least 100, plus no colons or dashes.
- **V6 (Task 3) A new cycle never opens with the quote the last one ended on**, so a reshuffle can't repeat a quip back to back.
