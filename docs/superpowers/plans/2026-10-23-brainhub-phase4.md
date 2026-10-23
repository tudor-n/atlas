# BrainHub Phase 4 (voice and sass): build plan

Date: 2026-10-23. Design: `docs/superpowers/specs/2026-10-23-brainhub-phase4-design.md` (approved by the owner the same day). Branch `phase-4` in the dev copy.

How it's built:
- Test first, one commit per task, plain messages (no trailers, rule 8).
- Build-time calls go into `docs/superpowers/phase4-decisions.md` (rulings V1…).
- After the build, subagents review the whole branch and their findings are fixed (the owner's instruction).
- Nothing is pushed without asking.

## Shape
The voice is plain threads, not asyncio: hook events reach `AgentTracker` in a worker thread, and the voice must never hold up a request.
- **`voice_lines.py`** is pure: the wording. It builds the info lines, durations and unit words, the spoken project names, merges, and the 200-character cap.
- **`quotes.py`** is pure plus a file: it parses `brain/_voice/quotes.md`, holds the seed text, cycles the pools, fills placeholders and keeps the cooldown.
- **`voice.py`:**
  - `Announcer` (thread-safe): the gates, dedupe, agent transitions, the reminder tick and catch-up, pause, and `voice.json` in the state folder;
  - `Speaker`: one thread with a bounded queue that owns the worker process.
- **`voice_worker.py`** is the child process (`python -m brainhub voice-worker`). It speaks JSON lines on stdin/stdout, runs synthesis (kokoro-onnx, the espeak path guard) and plays the result through the OS player.
- **`voice_files.py`:** the pinned model download (sha256, `.part`, progress).

## Tasks

1. **Dependency and settings.**
   - What: `kokoro-onnx` in `pyproject.toml` and `uv.lock`. The design's ten keys go through `DEFAULT_SETTINGS`/`validate_setting`. `voice_events` and `voice_blend` are dict-valued, with their own rules.
   - Files: `pyproject.toml`, `uv.lock`, `config.py`. Tests: `test_config.py`.
2. **Wording (`voice_lines.py`).**
   - What: `duration_words`, `unit_words`, `spoken_project`, `agent_waiting_line`, `agent_finished_line`, `reminder_line`, `readback_note`, `readback_answer`, `catch_up_line`, `merge(lines)`, `cap`. They follow the wording rules (§4.1).
   - Tests: `test_voice_lines.py`.
3. **Quotes (`quotes.py`).**
   - What: parse (pools, comments, placeholders, problems with line numbers), the seed file (at least 100 quotes, five pools, three styles), `QuotePicker` (shuffled cycle, positions in a dict the caller persists, reshuffle on change, skip what can't be filled, 60 s cooldown).
   - `ensure_quotes(paths, repo, on_commit)` seeds and commits. `_voice/` joins the filing agent's protected paths.
   - Files: `quotes.py`, `filing.py` (protected paths), `server.py` (startup step). Tests: `test_quotes.py`, `test_filing.py` (protected).
4. **The worker (`voice_worker.py`).**
   - Protocol, one JSON object per line:
     - in: `{"op":"say","id","parts":[…],"blend","speed","volume","model_dir","model"}` and `{"op":"stop"}`;
     - out: `{"id","ok":true}` or `{"id","ok":false,"error"}`.
   - Synthesis: the parts are joined with 0.4 s of silence. The volume scales the samples. The WAV goes to `<state>/voice/`. Then the player plays it.
   - Players: Linux `pw-play`, `paplay`, `aplay` in their own process group; Windows `winsound`.
   - The espeak path guard copies the data to a short folder when the path is 160 characters or longer.
   - `BRAINHUB_VOICE_FAKE=1` makes a fake engine and player that write the request to a file: the tests' boundary, like `FakeRunner`.
   - Files: `voice_worker.py`, `__main__.py`. Tests: `test_voice_worker.py` (a real subprocess with the fake), plus one real-synthesis test that skips when the model isn't present.
5. **Announcer and Speaker (`voice.py`).**
   - Gates, in order: enabled, installed, event ticked, quiet hours (wrapping midnight), pause, dedupe.
   - Agent transitions come from `AgentTracker(on_transition=…)`: waiting once per stretch; finished when the running stretch is at least `voice_finish_min_s`.
   - Reminders: `tick(now)` speaks the items whose reminder times fall in `(last_tick, now]`. A date-only time is spoken at the end of quiet hours. Startup catch-up is one line; more than 24 h missed counts as none.
   - The queue: at most 5 waiting, 60 s staleness, a 3 s merge window. Stop and pause empty it.
   - The Speaker starts the worker on demand, kills it after 10 minutes idle, and never crashes the hub. It keeps its last error.
   - Files: `voice.py`, `agents.py` (the callback). Tests: `test_voice.py` (Clock, a fake speaker sink), `test_agents.py` (transitions).
6. **Voice files (`voice_files.py`).**
   - What: pinned URLs and sha256 for int8, fp32 and the voices; `installed(data_dir, model)`; `download(…, progress)` to `data/voice/` through `.part` with the check, then the rename. One download at a time.
   - Tests: `test_voice_files.py`, served by a local aiohttp server, with the URLs overridden.
7. **Wiring and API.**
   - `server.py`: the Announcer, Speaker and tick task, only in the full writable app (not in reduced or read-only mode); the worker killed at cleanup.
   - Tablet note and answer readbacks.
   - `/api/voice` (GET), `/download`, `/sample`, `/pause`, `/stop`, plus the `voice.*` broadcasts. They're PC-only.
   - Files: `server.py`, `routes.py`, `tablet_routes.py`. Tests: `test_api_voice.py` (including a LAN refusal), `test_api_tablet.py` (readback).
8. **Web.**
   - Settings → **Voice** (`voice-settings.js` plus the pure `voice-model.js`): switches, events, blend editor, speed, volume, the download with progress, Play sample, Edit quotes, warnings.
   - The widget's speaker toggle.
   - Tests: `voice-model.test.mjs`, `widget-model.test.mjs`.
9. **Tray.**
   - A **Pause voice** submenu (1 hour, until morning, resume) that POSTs to `/api/voice/pause` over a std `TcpStream` (no new crates).
   - Tests: `cargo test` for the request builder.
10. **Docs:** `phase4-decisions.md`, `app/README.md` (Phase 4, Measured), `app/CLAUDE.md`, `NEXT-SESSION.md`, `TODO.md` (the rollout steps).
11. **By hand:** a scratch hub on a temp root, with the real model downloaded into it. Check real synthesis, a sample, an agent event through a real hook post, a reminder a minute ahead, a tablet note, pause, and killing the hub while it speaks.
12. **Reviews:** subagents review the backend, the UI and shell, and the security and robustness. Every finding is fixed.

## Rollout (owner, after the merge; design §11)
Recorded in `TODO.md`.
