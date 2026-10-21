# BrainHub: notes for Claude Code sessions

A local "second brain" for one person (the owner), on Windows. The owner writes freely. A headless Claude Code agent files each entry into a git-versioned Markdown brain and proposes tasks and dates. The owner confirms them. Later the owner asks questions and gets their own words back, with citations.

**Phase 1 is done. Next: calibration, then phase 2.**

## Read these first (repo root = `D:\BrainHub`)
| File | What it's for |
|---|---|
| `docs/superpowers/specs/2026-10-20-brainhub-design.md` | **The spec, the binding authority.** Intent, goals, anti-goals, all 6 phases. |
| `docs/superpowers/phase1-decisions.md` | Every build-time ruling that amended the plan, plus known residuals. Read it before changing core behaviour. |
| `app/README.md` | How to run and test, where data lives, and the "Known limitations" list. |
| `docs/superpowers/plans/2026-10-20-brainhub-phase1.md` | The phase 1 plan as written. Rulings in the decisions doc override it. |

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
6. **UI:** user and agent text is set via `textContent` only (see `web/js/dom.js`, `el()`). Never `innerHTML` with data. No build step: plain ES modules.
7. **Claude runs only via the `claude` CLI** on the owner's subscription:
   - `ANTHROPIC_API_KEY` is stripped from the environment, and `--bare` is never used.
   - The agents run isolated: `--tools` with an explicit set, `--strict-mcp-config`, `--setting-sources ""` and `--no-session-persistence`. See `claude_runner.py`.
8. **Commits: plain messages, NO `Co-Authored-By` or `Claude-Session` trailers.** This is the owner's explicit preference.
9. **Never run the hub against `D:\BrainHub` while testing.** That's the owner's real brain. Use a temp root, e.g. `--root %TEMP%\bh-test`, and delete it afterwards. Don't touch `D:\BrainHub\brain` or `D:\BrainHub\data`.
10. **Don't put a `CLAUDE.md` at `D:\BrainHub\`.** The filing agent's worktrees live under `D:\BrainHub\data\…`, so it could load that file into the agent. This file lives in `app/` on purpose. Start dev sessions in `D:\BrainHub\app`.

## Run and test (from `D:\BrainHub\app`)
- **Run:** `.venv\Scripts\python -m brainhub --root <data root> [--port 8090]`, then open http://127.0.0.1:8090.
- **Python tests:** `.venv\Scripts\python -m pytest -q`. Phase 1 had 331 passing.
- **JS tests:** `node --test web/js/components/*.test.mjs web/js/views/*.test.mjs` (10 passing). Check a file's syntax with `node --check <file>`.
- **Test doubles:** `tests/fakes.py` has `FakeRunner` (the Claude boundary, with an `on_run(cwd)` hook to simulate agent edits), `FakeRepo` and `Clock`. Tests use real SQLite, real git and real files. Only the Claude boundary is faked.

## Code map
| Path | What it is |
|---|---|
| `brainhub/config.py` | `Paths`: data paths come from `--root`, code paths from the package. `DEFAULT_SETTINGS` and validated `save_settings`. |
| `brainhub/rawlog.py` | Raw log append and read. `read_log(brain, problems=[])` is the tolerant mode. |
| `brainhub/store.py` | SQLite: entries, filings, proposals, items and the FTS5 index. Exports `_items/items.json` and `proposals.json`. |
| `brainhub/gitrepo.py` | `BrainRepo`: commit, revert, worktrees, cherry-pick and the non-interactive push. |
| `brainhub/filing.py` | Filing prompt, result parsing, `FilingWorker` (queue, worktree flow, corrections, rebuild recovery). |
| `brainhub/review.py` | Confirm, dismiss and edit for proposals and items. |
| `brainhub/recall.py` | FTS query building, quick cited answer and dig deeper. |
| `brainhub/today.py` | Today sections, plus the catch-all "other". |
| `brainhub/backup.py` | Debounced private-repo push. |
| `brainhub/server.py` + `routes.py` | aiohttp app, startup steps (`startup_error`/`log_error`), API, SSE and WebSocket. |
| `brainhub/prompts/*.md` + `schemas/filing.schema.json` | Agent prompts. **`prompts/filing.md` is the main calibration knob.** |
| `web/` | UI: `js/api.js`, `app.js` (hash router plus view registry), `dom.js`, `components/`, `views/`. |

## Where things stand
- **Phase 1 is built** on branch `phase-1`. The owner merges it into `master` on GitHub.
- **Next: calibration.** The owner uses it on real writing and collects cases of "wrote X, it extracted Y, expected Z". Then tune `prompts/filing.md` and the Settings defaults.
- **Then phase 2** (spec §12): a Tauri desktop widget, the usage meter (Claude Code status line `rate_limits`), and an Agents panel via Claude Code hooks.
  - First verify that user-level hooks fire in the VS Code extension and in desktop-app sessions. The spec flags this as unconfirmed.
  - The hub's own `claude -p` runs must not show up as agents.
- **Later phases:** tablet (SM-T110, ES5-only), voice (Kokoro), Google Calendar, phone.

## How the owner likes to work
- Brainstorm, then a spec, then a plan, then subagent-driven execution with reviews.
- Concise answers, decisions surfaced plainly, and nothing published or pushed without asking.
