# BrainHub: notes for Claude Code sessions

A local "second brain" for one person (the owner), on Windows. The owner writes freely. A headless Claude Code agent files each entry into a git-versioned Markdown brain and proposes tasks and dates. The owner confirms them. Later the owner asks questions and gets their own words back, with citations. Phase 2 adds a usage meter, an Agents panel fed by the owner's Claude Code hooks, a desktop widget and a Tauri desktop shell.

**Phases 1 and 2 are built. Phase 2 lives on branch `phase-2`; the owner merges it on GitHub. Next: the owner's phase 2 verification checklist, calibration, then phase 3.**

> **Read `../NEXT-SESSION.md` first.** It is the handoff from the end of the phase 2 build: what the owner still has to run, Linux dual-boot notes, standing rules and machine facts.

## Read these first (repo root = `D:\BrainHub`)
| File | What it's for |
|---|---|
| `docs/superpowers/specs/2026-10-20-brainhub-design.md` | **The spec, the binding authority.** Intent, goals, anti-goals, all 6 phases. |
| `docs/superpowers/specs/2026-10-21-brainhub-phase2-design.md` | Phase 2 made concrete: hook client, installer, agent statuses, usage, widget, desktop shell. |
| `docs/superpowers/phase1-decisions.md`, `docs/superpowers/phase2-decisions.md` | Every build-time ruling that amended the plans, plus known residuals. Read them before changing core behaviour. |
| `app/README.md` | How to run and test, where data lives, the phase 2 owner checklist, "Known limitations" and "Measured". |
| `app/desktop/README.md` | Building, configuring and running the desktop shell. |
| `docs/superpowers/plans/2026-10-20-brainhub-phase1.md`, `…/2026-10-21-brainhub-phase2.md` | The plans as written. Rulings in the decisions docs override them. |

**The owner's anti-goal:** "more of a chore than paper, my head, or a plain Claude chat". It must never answer with something the owner didn't say, and never silently drop anything they wrote. When in doubt, choose simpler and faster. Every tunable belongs in Settings, not in code.

## Hard rules: don't break these
1. **The raw log is sacred.** `brain/log/YYYY/MM/YYYY-MM-DD.md` is verbatim and append-only, written before anything else on capture, and never rewritten or repaired automatically.
   - Format: `<!-- entry id=… ts=… source=… len=N -->`, then the text, then `<!-- /entry -->`.
   - `len` is the Python character count. Use UTF-8 with `newline=""`, under the module lock in `rawlog.py`.
2. **Nothing becomes an item until the owner confirms it.** The agent only creates *proposals*.
3. **The filing agent never touches the main brain checkout.**
   - It runs in a git worktree under `data/worktrees/<entry_id>`. Protected paths (`log/`, `_items/`, `map.json` and dot-files) are restored there, and the result is cherry-picked in.
   - Never call `discard_changes()` on the main repo.
4. **Every hub commit re-indexes the `.md` files it changed** (`on_commit(sha)` in `server.py`). Recall must never quote stale text.
5. **Security:** the server binds `127.0.0.1` only. The `local_only` middleware checks Host and Origin, and JSON bodies require `Content-Type: application/json`, otherwise 415. Keep it that way. Phase 3 adds LAN pairing.
6. **UI:** user and agent text is set via `textContent` only (see `web/js/dom.js`, `el()`). Never `innerHTML` with data. No build step: plain ES modules. Agent-supplied strings (cwd, title, branch, notification message) count as agent text.
7. **Claude runs only via the `claude` CLI** on the owner's subscription:
   - `ANTHROPIC_API_KEY` is stripped from the environment, and `--bare` is never used.
   - The agents run isolated: `--tools` with an explicit set, `--strict-mcp-config`, `--setting-sources ""` and `--no-session-persistence`. See `claude_runner.py`. (`--setting-sources ""` is also why the hub's own agents never fire BrainHub's hooks.)
8. **Commits: plain messages, NO `Co-Authored-By` or `Claude-Session` trailers.** This is the owner's explicit preference.
9. **Never run the hub against `D:\BrainHub` while testing.** That's the owner's real brain. Use a temp root, e.g. `--root %TEMP%\bh-test`, and delete it afterwards. Don't touch `D:\BrainHub\brain` or `D:\BrainHub\data`.
10. **Don't put a `CLAUDE.md` at `D:\BrainHub\`.** The filing agent's worktrees live under `D:\BrainHub\data\…`, so it could load that file into the agent. This file lives in `app/` on purpose. Start dev sessions in `D:\BrainHub\app`.
11. **The hook client (`brainhub/hook_client.py`) is silent, always exits 0, and is stdlib-only.** It runs as a file (`python -I -S hook_client.py …`), never imports from `brainhub`, swallows every exception, and in `event` mode never writes to stdout or stderr (stdout on SessionStart/UserPromptSubmit would be injected into the owner's agent). It sends only the allow-listed fields: never prompts, tool data or transcripts. The status line mode prints exactly one line.
12. **Never write the owner's real Claude settings (`%USERPROFILE%\.claude\settings.json`) or the real `%LOCALAPPDATA%\BrainHub\desktop.json` from tests or manual checks.** Use `integration … --settings <temp file>` and `desktop-config --target <temp file>`; `tests/conftest.py` points `CLAUDE_CONFIG_DIR` and `LOCALAPPDATA` at temp folders for every test. The desktop exe writes WebView2 data to the real `%LOCALAPPDATA%\dev.brainhub.desktop` and its window position to the real `%APPDATA%\dev.brainhub.desktop\.window-state.json` regardless of env vars: delete both after a manual run if they weren't there before. Never tick "Start at login" in a test run (HKCU Run key).
13. **The hub ignores events whose entrypoint starts with `sdk-`** (`claude -p` and SDK runs), and events whose cwd is inside its own `data/` or `brain/`. Claude Code sets `CLAUDE_CODE_ENTRYPOINT` itself for hook processes (`sdk-cli` under `-p`), so an e2e that needs rows must force it inside the hook command (see `app/README.md` → Measured → Phase 2).

## Run and test (from `D:\BrainHub\app`)
- **Run:** `.venv\Scripts\python -m brainhub --root <data root> [--port 8090]`, then open http://127.0.0.1:8090 (widget page: `/widget`).
- **Integration:** `.venv\Scripts\python -m brainhub integration install|uninstall|status --root <root> [--settings <path>]`.
- **Desktop config:** `.venv\Scripts\python -m brainhub desktop-config --root <root> [--target <path>]`. It writes the console `python.exe`, never `pythonw.exe` (Ruling 13); the shell starts the hub with `--port` from it and must be restarted after it is re-run.
- **Python tests:** `.venv\Scripts\python -m pytest -q` (436 passing at the end of phase 2).
- **JS tests:** `node --test web/js/components/*.test.mjs web/js/views/*.test.mjs web/js/*.test.mjs` (25 passing). Check a file's syntax with `node --check <file>`.
- **Rust (desktop shell):** in `app\desktop\src-tauri`, with `%USERPROFILE%\.cargo\bin` on `PATH` (it isn't by default): `cargo test` (14 passing) and `cargo build --release` → `target\release\brainhub-desktop.exe`.
- **Test doubles:** `tests/fakes.py` has `FakeRunner` (the Claude boundary, with an `on_run(cwd)` hook to simulate agent edits), `FakeRepo` and `Clock`. Tests use real SQLite, real git and real files. Only the Claude boundary is faked. `hook_client.py` is tested as a real subprocess.

## Code map
| Path | What it is |
|---|---|
| `brainhub/config.py` | `Paths`: data paths come from `--root`, code paths from the package. `DEFAULT_SETTINGS` and validated `save_settings`. |
| `brainhub/rawlog.py` | Raw log append and read. `read_log(brain, problems=[])` is the tolerant mode. |
| `brainhub/store.py` | SQLite: entries, filings, proposals, items, agents and the FTS5 index. Exports `_items/items.json` and `proposals.json`. |
| `brainhub/gitrepo.py` | `BrainRepo`: commit, revert, worktrees, cherry-pick and the non-interactive push. |
| `brainhub/filing.py` | Filing prompt, result parsing, `FilingWorker` (queue, worktree flow, corrections, rebuild recovery). |
| `brainhub/review.py` | Confirm, dismiss and edit for proposals and items. |
| `brainhub/recall.py` | FTS query building, quick cited answer and dig deeper. |
| `brainhub/today.py` | Today sections, plus the catch-all "other". |
| `brainhub/backup.py` | Debounced private-repo push. |
| `brainhub/server.py` + `routes.py` | aiohttp app, startup steps (`startup_error`/`log_error`), API, SSE and WebSocket. |
| `brainhub/__main__.py` | CLI: run the hub, `integration …`, `desktop-config`. |
| `brainhub/usage.py` | `UsageStore`: per-window merge of status line `rate_limits`, `data/usage.json`, the `/api/usage` view. |
| `brainhub/agents.py` | `AgentTracker`: hook event → status, project/branch/part/where resolution, linger and stale pruning. |
| `brainhub/widget.py` | `/api/widget` summary (synopsis, attention, tiles; ended sessions don't count) and the cached arm reachability probe (15 s reachable, 60 s offline). |
| `brainhub/hook_client.py` | Silent stdlib hook and status line client (rule 11). |
| `brainhub/claude_integration.py` | Install/uninstall/status of our hooks and status line in a Claude Code settings file (backup, atomic write, per-hook ownership). |
| `brainhub/desktop_config.py` | Writes `%LOCALAPPDATA%\BrainHub\desktop.json` for the shell (console `python.exe`, Ruling 13). |
| `brainhub/prompts/*.md` + `schemas/filing.schema.json` | Agent prompts. **`prompts/filing.md` is the main calibration knob.** |
| `web/` | UI: `js/api.js`, `app.js` (hash router plus view registry), `dom.js`, `components/`, `views/`. |
| `web/js/views/usage.js`, `agents.js`, `agents-model.js` | Usage meters; Agents table with inline part/Where edits; their pure helpers (tested). |
| `web/widget.html`, `widget.css`, `js/widget.js`, `js/widget-model.js` | The widget page; `widget-model.js` holds the pure synopsis/tile/popup logic (tested). |
| `desktop/` | Tauri v2 shell: `src-tauri/src/lib.rs` (window, tray, autostart, watch loop that navigates to the widget once, work-area clamp), `supervisor.rs` (hub start/restart with backoff and `--port`), `config.rs` (`desktop.json`), `capabilities/widget.json`, `dist/` (the "Starting BrainHub…" page). |

## Where things stand
- **Phase 1 is built** and merged by the owner from branch `phase-1`.
- **Phase 2 is built** on branch `phase-2` (usage meter, Agents panel, hook client and installer, widget, desktop shell). The owner merges it into `master` on GitHub. The real e2e with Claude Code 2.1.306 passed (see `app/README.md` → Measured).
- **Next:**
  1. The owner runs the phase 2 verification checklist (`app/README.md`): install the integration for real, and check terminal, VS Code and desktop-app sessions (what Where says there, whether the status line runs there), and Start at login across a reboot. Fix `WHERE_BY_ENTRYPOINT` in `agents.py` from the raw entrypoints the owner reports.
  2. Calibration (still open from phase 1): the owner collects "wrote X, it extracted Y, expected Z" cases; tune `prompts/filing.md` and the Settings defaults.
  3. Phase 3 (spec §12): the tablet (SM-T110, ES5-only) with its AGENTS tab, LAN binding and pairing, measurement requests.
- **Later phases:** voice (Kokoro) and notifications, Google Calendar, phone.

## How the owner likes to work
- Brainstorm, then a spec, then a plan, then subagent-driven execution with reviews.
- Concise answers, decisions surfaced plainly, and nothing published or pushed without asking.
