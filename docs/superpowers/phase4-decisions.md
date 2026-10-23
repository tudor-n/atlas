# Phase 4 (voice and sass): build-time decisions

Rulings made while building phase 4, as amendments to the plan (`plans/2026-10-23-brainhub-phase4.md`) and the design (`specs/2026-10-23-brainhub-phase4-design.md`). Numbered V1….

- **V1 (Task 1) `voice_speed` takes ints and floats.** The generic rule rejects a value whose type differs from its default. A slider or JSON can send `1` for `1.0`, so `voice_speed` and the blend weights accept any finite non-bool number. NaN and infinity are refused.
- **V2 (Task 1) Voice ids are checked by shape only** (`bm_george`: two letters, `_`, a name). The voices file may not be downloaded yet when Settings saves, so an id that the file lacks is reported by the worker when it speaks ("unknown voice"), not refused by the settings.
- **V3 (Task 3) `_voice/` is skipped by the notes index too**, not only protected from the filing agent. Otherwise recall could quote the quips as if they were the owner's notes.
- **V4 (Task 3) The quote file is seeded twice:** in setup's own commit (a new brain), and as a startup step with its own commit, "add the voice quotes" (a brain set up before phase 4). It's never overwritten, whatever its state.
- **V5 (Task 3) The seed has 107 quotes** (30 agent_finished, 30 agent_waiting, 25 reminder, 12 readback, 10 catch_up), in a roughly even mix of the three styles, and they follow the wording rules. A test enforces at least 100, plus no colons or dashes.
- **V6 (Task 3) A new cycle never opens with the quote the last one ended on**, so a reshuffle can't repeat a quip back to back.
- **V7 (Task 4) The worker's stdout belongs to the protocol.** `main` duplicates the real stdout for the JSON lines and points file descriptor 1 at stderr. So anything onnxruntime, phonemizer or espeak prints can never corrupt a reply.
- **V8 (Task 4) "stop" cuts only the line playing now.** The Speaker (Task 5) empties its own queue first, so nothing is left behind it. Lines already handed to the worker are at most the one playing, because the Speaker sends one line at a time and waits for its reply.
- **V9 (Task 4) Windows plays with `winsound.PlaySound(…, SND_ASYNC)` and waits out the clip's length**, because a synchronous PlaySound can't be stopped from another thread. Stop is `PlaySound(None, 0)`. It's untested by hand while Windows is on hold, and CI covers only the player's selection.
- **V10 (Task 4) The fake boundary is `BRAINHUB_VOICE_FAKE=<file>`**: no model and no audio, just one second of silence per part, with each request appended to the file. Tests run the real worker process with it, like `fake_cli` for claude. A real-synthesis test runs when `BRAINHUB_VOICE_TEST_MODELS` points at the voice files; it passed on Kubuntu.
