# BrainHub: notes for Claude Code sessions

A local "second brain" for one person (the owner), on a dual-boot laptop: Kubuntu 26.04 (the daily driver, where development happens) and Windows 11. The owner writes freely. A headless Claude Code agent files each entry into a git-versioned Markdown brain and proposes tasks and dates. The owner confirms them. Later the owner asks questions and gets their own words back, with citations. Phase 2 adds a usage meter, an Agents panel fed by the owner's Claude Code hooks, a desktop widget and a Tauri desktop shell. The dual-boot port makes it one lifeline on both OSes: the same master commit, one shared root on `D:`, the same features, and Claude Code memory, skills and settings shared through `claude-sync`.

**Phases 1 and 2 and the dual-boot port (PR #3) are merged into master. Next: the owner's test-drive fixes (branch `test-drive-fixes`, rulings 20–23 in `phase2-decisions.md`) get merged and checked in the next test drive, then the Windows half of the memory spike (ruling D2), the Windows-side checks, then the rollout with the owner (plan → Rollout).**

> `../NEXT-SESSION.md` is a short pointer: where things stand and the standing rules.

## Read these first (repo root = the dev copy `~/dev/BrainHub`)
| File | What it's for |
|---|---|
| `docs/superpowers/specs/2026-10-20-brainhub-design.md` | **The spec, the binding authority.** Intent, goals, anti-goals, all 6 phases. |
| `docs/superpowers/specs/2026-10-21-brainhub-phase2-design.md` | Phase 2 made concrete: hook client, installer, agent statuses, usage, widget, desktop shell. |
| `docs/superpowers/specs/2026-10-21-brainhub-dual-boot-design.md` | Where it runs and how, on both OSes: shared vs per-OS data, live copy and updates, guards, Claude Code parity. Amends the spec's §4, §11, §13.1 and §14. |
| `docs/superpowers/phase1-decisions.md`, `phase2-decisions.md`, `dual-boot-decisions.md` | Every build-time ruling that amended the plans (dual boot: D1–D12), plus known residuals. Read them before changing core behaviour. |
| `app/README.md` | How to set up, run and test on each OS, where data lives, the drive banner, verification drills, "Known limitations" and "Measured". |
| `app/desktop/README.md` | Building, configuring and running the desktop shell (both OSes, runtime mode). |
| `docs/superpowers/plans/2026-10-20-brainhub-phase1.md`, `…/2026-10-21-brainhub-phase2.md`, `…/2026-10-22-brainhub-dual-boot.md` | The plans as written. Rulings in the decisions docs override them. |

**The owner's anti-goal:** "more of a chore than paper, my head, or a plain Claude chat". It must never answer with something the owner didn't say, and never silently drop anything they wrote. When in doubt, choose simpler and faster. Every tunable belongs in Settings, not in code.

## Hard rules: don't break these
1. **The raw log is sacred.** `brain/log/YYYY/MM/YYYY-MM-DD.md` is verbatim and append-only, written before anything else on capture, and never rewritten or repaired automatically.
   - Format: `<!-- entry id=… ts=… source=… len=N -->`, then the text, then `<!-- /entry -->`.
   - `len` is the Python character count. Use UTF-8 with `newline=""`, under the module lock in `rawlog.py`.
2. **Nothing becomes an item until the owner confirms it.** The agent only creates *proposals*.
3. **The filing agent never touches the main brain checkout.**
   - It runs in a git worktree under `data/worktrees/<entry_id>`. Protected paths (`log/`, `_items/`, `map.json` and dot-files) are restored there, and the result is cherry-picked in.
   - Paths invalid on Windows or clashing by case fail the filing before the cherry-pick (`filing.invalid_windows_paths`, D7).
   - Never call `discard_changes()` on the main repo.
4. **Every hub commit re-indexes the `.md` files it changed** (`on_commit(sha)` in `server.py`). Recall must never quote stale text.
5. **Security:** the server binds `127.0.0.1` only. The `local_only` middleware checks Host and Origin, and JSON bodies require `Content-Type: application/json`, otherwise 415. Keep it that way. Phase 3 adds LAN pairing.
6. **UI:** user and agent text is set via `textContent` only (see `web/js/dom.js`, `el()`). Never `innerHTML` with data. No build step: plain ES modules. Agent-supplied strings (cwd, title, branch, notification message) count as agent text.
7. **Claude runs only via the `claude` CLI** on the owner's subscription:
   - `ANTHROPIC_API_KEY` is stripped from the environment, and `--bare` is never used.
   - The agents run isolated: `--tools` with an explicit set, `--strict-mcp-config`, `--setting-sources ""` and `--no-session-persistence`. See `claude_runner.py`. (`--setting-sources ""` is also why the hub's own agents never fire BrainHub's hooks.)
   - Auto memory is disabled for the hub's runs (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `child_env()`, D11): the isolation flags don't turn it off, and every filing used to leave a `~/.claude/projects/<key>/memory/` folder.
   - Non-essential traffic is off too (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, ruling 22): about 1.2–1.6 s faster per run.
   - `claude_path` is a bare command name, never a path (it lives in shared settings); each OS resolves it with `shutil.which`, then `~/.local/bin`.
8. **Commits: plain messages, NO `Co-Authored-By` or `Claude-Session` trailers.** This is the owner's explicit preference.
9. **Never run the hub against the real root while testing:** `/mnt/data/BrainHub` on Kubuntu, `D:\BrainHub` on Windows. They are the same folder: the owner's real brain and the live copy. Use a temp root and a temp state folder (`BRAINHUB_STATE_DIR=/tmp/bh-state-x … --root /tmp/bh-test-x`; on Windows under `%TEMP%`) and delete both afterwards. Never write under the real root; the only exception is read-only git inspection of the live copy (`git -c safe.directory=/mnt/data/BrainHub …`).
10. **Don't put a `CLAUDE.md` at the repo root** (`~/dev/BrainHub`, or the live copy `D:\BrainHub`). The filing agent's worktrees live under `<root>/data/…`, so it could load that file into the agent. This file lives in `app/` on purpose. Start dev sessions in `~/dev/BrainHub/app` (on Windows, in the dev clone's `app`).
11. **The hook client (`brainhub/hook_client.py`) is silent, always exits 0, and is stdlib-only.** It runs as a file (`python -I -S hook_client.py …`), never imports from `brainhub`, swallows every exception, and in `event` mode never writes to stdout or stderr (stdout on SessionStart/UserPromptSubmit would be injected into the owner's agent). It sends only the allow-listed fields: never prompts, tool data or transcripts. The status line mode prints exactly one line. In runtime mode the hooks run the stable copy `<state>/hook/hook_client.py` with the slot venv's base interpreter (P4, D10).
12. **Never write the owner's real Claude Code or BrainHub files from tests or manual checks:**
   - Claude Code: `~/.claude/settings.json` on Kubuntu and `%USERPROFILE%\.claude\settings.json` on Windows, and on both `~/.claude/settings.local.json`, `~/.claude/projects/**` and `~/.claude/skills/**`;
   - the state folder (`~/.local/share/brainhub`, `%LOCALAPPDATA%\BrainHub`), the config folder (`~/.config/brainhub`, `%LOCALAPPDATA%\BrainHub`), the shared Claude store (`/mnt/data/ClaudeShared` = `D:\ClaudeShared`), and `~/.config/autostart` or the HKCU Run key (never tick "Start at login" in a test run).
   - Use `integration … --settings <temp file>`, `desktop-config --target <temp file>`, `claude-sync` with temp `--shared`, `--claude-dir`, `--home` and `--projects-root`, and temp `BRAINHUB_STATE_DIR`, `BRAINHUB_CONFIG_DIR`, `BRAINHUB_SHARED_DIR` and `CLAUDE_CONFIG_DIR`. `tests/conftest.py` sets those four (and `LOCALAPPDATA`) to temp folders for every test.
   - The desktop exe writes its web view data and window position to the real folders regardless of env vars (Windows `%LOCALAPPDATA%\dev.brainhub.desktop` and `%APPDATA%\dev.brainhub.desktop\.window-state.json`; Linux `~/.local/share/dev.brainhub.desktop`, `~/.config/dev.brainhub.desktop` and `$XDG_RUNTIME_DIR/tray-icon`): delete them after a manual run if they weren't there before.
   - A real `claude -p` creates an empty `~/.claude/projects/<key of its cwd>/memory/` even with `--no-session-persistence` (D10): remove it after an e2e.
13. **The hub ignores events whose entrypoint starts with `sdk-`** (`claude -p` and SDK runs), and events whose cwd is inside its own `data/` or `brain/`. Claude Code sets `CLAUDE_CODE_ENTRYPOINT` itself for hook processes (`sdk-cli` under `-p`), so an e2e that needs rows must force it inside the hook command (see `app/README.md` → Measured).
14. **The hub never creates a root, and never loses a capture.** `rootguard.check_root` classifies the root before anything touches it; only setup on a "fresh" root creates a brain (and `data/root.json`). Missing, foreign and newer roots get the reduced app, which never opens `hub.db`, runs git or writes under the root. A capture that can't reach the raw log goes to this OS's holding inbox, on disk before the API says "saved". `hub.db` is moved aside only for real corruption (malformed, not a database, encrypted, or `quick_check`), never for "locked", "readonly" or "unable to open".
15. **The update step (`brainhub/update_step.py`) is stdlib-only and runs as a file**, like the hook client. On the live copy it only fast-forwards (`merge --ff-only --no-autostash --no-overwrite-ignore` to the exact sha CI checked): never reset, stash, checkout, clean or any other merge, and a dirty, diverged or off-master live copy is left alone (D9). Keep the CI job names `test (ubuntu-latest)` and `test (windows-latest)`: its gate reads them.

## Two OSes, one lifeline (dual-boot design §4–§6)
- **Shared root:** `D:\BrainHub` = `/mnt/data/BrainHub` (NTFS, mounted by fstab with `ntfs3`, `nofail`). One OS runs at a time. It holds the live copy, `brain/` and `data/` (`brain/` and `data/` are git-ignored).
- **Live copy:** the checkout at the shared root. Master only, never edited by hand; each OS fast-forwards it through the update step.
- **Dev copy:** `~/dev/BrainHub` on Kubuntu (btrfs). Branches, tests and spikes happen there, only against temp roots (rule 9). Occasional Windows development happens in a separate clone on the local disk, never in the live copy. GitHub carries code between the OSes: commit and push before a reboot.
- **Runtime slots** (P2): each OS runs the hub from `<state>/runtime/slot-a` or `slot-b`: `app/` is a `git archive` of a live-copy commit, `venv/` comes from `uv sync --frozen --no-install-project`. `runtime/current.json` (`{"slot", "commit", "deps"}`) is the pointer; a slot is current only while its `slot.json` names the same commit. `python -m brainhub runtime init --live <live copy>` builds the first one. The hub can start (reduced) even when `D:` is missing.
- **The update step**, run by the shell before every hub start with the current slot's python (75 s budget, one at a time via `runtime/update.lock`):
  1. **Pull:** fast-forward the live copy only when it is a clean checkout of master, the fetch works, `origin/master` is a fast-forward of HEAD, and CI finished green on both OSes for that sha (`gh api …/check-runs`: every run completed, none failed, both `test (…)` jobs succeeded; unreadable = not green).
  2. **Refresh:** when the live HEAD isn't the current slot's commit (and is on master, at or behind the local `origin/master`: the provenance guard), build the other slot and flip `current.json` only once its app and venv are both ready.
  3. **Hook:** keep `<state>/hook/hook_client.py` equal to the current slot's copy.
  - Anything skipped is quiet: `pending` in `<state>/update.json`, shown as "update pending (reason)" in Settings' Runtime line.
- **The desktop shell** is built per OS from the live copy into a local `CARGO_TARGET_DIR` with `--locked`; it shows "widget update available" when the live copy's shell source digest differs from the one built in. On Linux it runs under XWayland (D1). `desktop.json` has two modes: legacy (`python`, `app_dir`: dev) and runtime (`live`, `state`).

## Per-OS state and config folders (`osdirs.py`, P10)
- **State:** `$BRAINHUB_STATE_DIR`, else `${XDG_DATA_HOME:-~/.local/share}/brainhub` on Linux and `%LOCALAPPDATA%\BrainHub` on Windows. It holds the integration state (`claude_integration.json`, `statusline_passthrough.txt`), `holding/`, `hub.log`, `runtime/`, `update.json`, `shell.json`, `hook/` and claude-sync's files (`claude-sync.json`, `.log`, `-last.json`, `-backups/`).
- **Config:** `$BRAINHUB_CONFIG_DIR`, else `${XDG_CONFIG_HOME:-~/.config}/brainhub` on Linux and `%LOCALAPPDATA%\BrainHub` on Windows: `desktop.json`.
- An empty or relative XDG variable falls back to the default. The env overrides exist for tests and manual checks, and the Rust shell (`config.rs`) honours them too. Nothing in `osdirs` creates a folder.
- **Shared data stays OS-neutral:** `data/` holds `hub.db`, `settings.json`, `usage.json`, `worktrees/`, the root marker `root.json` and the hub lock `hub.lock`. Per-OS values never go in shared settings. Map projects keep folders per OS (`os_folders: {"windows": [...], "linux": [...]}`; a legacy `folders` entry counts for the OS its shape belongs to, P7); matching uses this OS's folders (case-insensitive on Windows, exact on Linux).
- `osdirs.os_name()` is `"windows"` or `"linux"`; `boot_id()` is Linux's kernel boot id or Windows' registry boot counter (D4). Agent rows record both, and rows from an earlier boot are ended at startup.

## Root guard, reduced mode, holding inbox (design §6.5)
- **Root states** (`rootguard.check_root`): ok, fresh (no marker, no `map.json`, `brain/` absent or empty: setup may create a brain; setup answers 409 on any other state), missing (absent, unreadable, or under an fstab mount point that isn't mounted), readonly, foreign (`brain/` has content but no `map.json`, or a marker without `map.json`, D5), newer (marker `brain_format` > `BRAIN_FORMAT`, or `hub.db` `user_version` > `SCHEMA_VERSION`). Any `OSError` while classifying means missing. A brain with `map.json` but no marker (made before markers) gets one.
- **Reduced app** (`create_reduced_app`; missing, foreign, newer): the pages, the drive banner (`/api/state.drive`), capture into the holding inbox, hook and usage posts answered 204 and ignored, every other `/api/` 503 with `drive`.
- **Read-only mode** (readonly): the full app with the store opened `mode=ro&immutable=1`, no writing startup steps, no workers or backup, and the `readonly_guard` middleware answering writes 503 (captures are held, hooks 204). Today, journal and recall work; confirm and tick are disabled with the reason. A `hub.db` that can't be read read-only gives the reduced app.
- **Holding inbox** (`holding.py`): `<state>/holding/<entry id>.json`, fsynced before "saved", tied to its root (marker id, else path; D8 addendum). The next full start on that root flushes each into the raw log with its own id and ts (its own day's file) before the log backfill, so it is filed normally. A capture whose append fails mid-run with `OSError` is held too. Unreadable files are kept and reported.
- **Recovery** (P8): reduced and read-only hubs re-check the root every 15 s (`RECHECK_SECONDS`); once it is ok the hub exits with 75 (`EXIT_RESTART`) and the shell starts it again at once, with no backoff. No re-check while "newer" (an update restarts the hub anyway).
- **Hub lock** `data/hub.lock` (`{os, boot, pid, pid_start, started}`): a lock from the other OS, an earlier boot or a dead (or reused) pid is taken over; this OS, this boot and a live process means refuse.
- **Quiet warnings** (`/api/state.warnings`, one line, never a popup): a time zone that differs from the marker's (D6), hibernation on (Windows, `winwatch.py`, D8 addendum).
- **Versions:** `hub.db` uses `PRAGMA user_version` (`store.SCHEMA_VERSION`, `MIGRATIONS`); the brain format is in the marker (`rootguard.BRAIN_FORMAT`). Bump them with a migration, never silently.

## Portability rules for new code
- No `win32`-only branch without a POSIX branch next to it, and the reverse. Each side gets a test; the side that can't run on this OS is skipped here and runs in CI on the other OS.
- Every file the hub writes in shared space (`brain/`, `data/`, the Claude store) is written with `newline="\n"`. Shared JSON holds no absolute paths and no per-OS state.
- Every git call on `brain/`, the live copy or the Claude store passes `-c safe.directory=<that path>` (forward slashes) and `-c core.ignorecase=` `true` on Windows, `false` on POSIX, whatever the shared `.git/config` says, which is never edited for it (D7). (The update step's live-copy calls don't pass `core.ignorecase` yet: see the residuals.) Live-copy calls also pass `-c gc.auto=0 -c maintenance.auto=false` (D9).
- Child processes: POSIX process groups (`start_new_session` or `process_group=0`, then `killpg` SIGTERM → SIGKILL) where Windows uses `taskkill /T`; `CREATE_NO_WINDOW` on Windows (Ruling 13).
- `.gitattributes` pins text to LF (`*.bat` CRLF). No test creates `nul` or any other Windows-reserved name.
- New tunables go through `DEFAULT_SETTINGS`/`validate_setting`/Settings, except per-OS values, which never go in shared settings.

## claude-sync (design §7, D2, D12)
`python -m brainhub claude-sync` (`claude_sync.py`, stdlib only, importing only `osdirs`) keeps Claude Code the same on both OSes through the shared store `/mnt/data/ClaudeShared` = `D:\ClaudeShared` (`BRAINHUB_SHARED_DIR`; a git repo that only `claude-sync init --shared <dir>`, run by hand, creates; a sync on a missing store changes nothing). Memory uses option 2 (a linked memory folder fails on Linux): after merging the local memory folder into the store (backup first; a clashing note kept as `<stem>.<os>.md`; `MEMORY.md` merged by line, with one header line), it writes `autoMemoryDirectory` into `~/.claude/settings.local.json` (home → `memory/home`) and into each paired project's `.claude/settings.local.json` (→ `memory/projects/<name>`). Projects pair by folder name under `~/dev` and `D:\`, plus `pairs.json` exceptions; the hub's filing-worktree keys are never paired, and the live copy is never pointed. Skills are moved into the store and linked back; settings sync per key both ways (later change wins, removals propagate), except the per-OS keys (`hooks`, `statusLine`, `permissions`, `env`, `defaultShell`, `apiKeyHelper`, `autoMemoryDirectory`, any value holding an absolute path, plus the owner's list in Settings). **It never deletes a memory note or a skill, and never shares a per-OS key.** In runtime mode the shell runs it once per start before the first hub, `--settings-only` when `settings.json` changes (5 s poll), and in full every 10 minutes; problems go to `<state>/claude-sync.log` and a quiet note in Settings.

## Run and test
**Kubuntu**, from `~/dev/BrainHub/app` (uv; Python 3.14 from `.python-version`):
- **Setup:** `uv sync --locked --extra test` (makes `.venv`).
- **Run:** `BRAINHUB_STATE_DIR=/tmp/bh-state-x uv run --no-sync python -m brainhub --root /tmp/bh-test-x [--port 8090]`, then open http://127.0.0.1:8090 (widget page: `/widget`).
- **Python tests:** `uv run --no-sync python -m pytest -q` (789 passed, 16 skipped at the end of the dual-boot build; the skips are the Windows-only halves). Only if parallel runs clash on ports: run each in its own network namespace, `unshare -rn sh -c 'ip link set lo up; uv run --no-sync python -m pytest -q'`. It runs as root inside the namespace, so the 5 chmod-based read-only tests skip there (784 passed, 21 skipped).
- **JS tests:** `source ~/.nvm/nvm.sh` first, then `node --test web/js/components/*.test.mjs web/js/views/*.test.mjs web/js/*.test.mjs` (43 passing). Check a file's syntax with `node --check <file>`.
- **Rust (desktop shell):** in `desktop/src-tauri`: `CARGO_TARGET_DIR=~/.cache/brainhub-target cargo test` (39 passing) and `cargo build --release`. Never build into `src-tauri/target`.

**Windows**, from the dev clone's `app` (uv, installed with `winget install astral-sh.uv`):
- **Setup:** `uv sync --locked --extra test`.
- **Run:** `set BRAINHUB_STATE_DIR=%TEMP%\bh-state`, then `uv run --no-sync python -m brainhub --root %TEMP%\bh-test [--port 8090]`.
- **Tests:** `uv run --no-sync python -m pytest -q`; the node command as above.
- **Rust:** in `desktop\src-tauri`, with `%USERPROFILE%\.cargo\bin` on `PATH` (it isn't by default): `set CARGO_TARGET_DIR=%LOCALAPPDATA%\brainhub-target`, then `cargo test` and `cargo build --release`.

**CLI** (either OS, as `uv run --no-sync python -m brainhub …` or with a slot's python):
- `integration install|uninstall|status --root <root> [--settings <path>]`.
- `desktop-config --root <root> [--target <path>]` (legacy) or `desktop-config --runtime --live <live copy> --root <root> [--target <path>]`. It writes the console python, never `pythonw.exe` (Ruling 13); restart the shell after re-running it.
- `runtime init --live <live copy>`, `runtime status`.
- `claude-sync [--shared D] [--claude-dir D] [--home D] [--projects-root D] [--dry-run] [--settings-only]`, `claude-sync init --shared <dir>`.

**CI** (`.github/workflows/ci.yml`): pytest and the node tests on `ubuntu-latest` and `windows-latest` for every PR and push to master; `cargo test` and `cargo build` when `app/desktop/**` changes.

**Test doubles:** `tests/fakes.py` has `FakeRunner` (the Claude boundary, with an `on_run(cwd)` hook to simulate agent edits), `FakeRepo`, `Clock` and `fake_cli` (a fake `claude`/`gh`/`uv` executable: a `.bat` on Windows, a shell script on POSIX). Tests use real SQLite, real git and real files. Only the Claude boundary is faked (plus `gh` and `uv` in the update step's tests). `hook_client.py` and `update_step.py` are tested as real subprocesses.

## Code map
| Path | What it is |
|---|---|
| `brainhub/config.py` | `Paths`: data paths come from `--root`, code paths from the package. `DEFAULT_SETTINGS` and validated `save_settings`. |
| `brainhub/osdirs.py` | Per-OS state and config folders, `os_name()`, `boot_id()` (stdlib only). |
| `brainhub/rootguard.py` | Root status, marker `data/root.json`, fstab mount guard, hub lock `data/hub.lock`, time zone check. |
| `brainhub/holding.py` | Per-OS holding inbox: durable hold, per-root listing, flush into the raw log. |
| `brainhub/winwatch.py` | Hibernation / Fast Startup warning (Windows; `[]` elsewhere). |
| `brainhub/rawlog.py` | Raw log append and read. `read_log(brain, problems=[])` is the tolerant mode; `append_entry(…, entry_id=)` for flushed captures. |
| `brainhub/store.py` | SQLite: entries, filings, proposals, items, agents and the FTS5 index; `user_version` migrations; read-only open. Exports `_items/items.json` and `proposals.json`. |
| `brainhub/gitrepo.py` | `BrainRepo`: commit, revert, worktrees, cherry-pick, stale lock clearing and the non-interactive push; `safe.directory` and per-OS `core.ignorecase` on every call. |
| `brainhub/brainmap.py` | The map: load, validate, save (LF), `os_folders`, `path_os`, `folders_for`. |
| `brainhub/filing.py` | Filing prompt, result parsing, Windows path guard, `step_label` (live progress), `FilingWorker` (queue, worktree flow, corrections, rebuild recovery, `progress`). |
| `brainhub/claude_runner.py` | `ClaudeRunner`: the isolated `claude -p` call (always `stream-json`, read line by line), `child_env()`, process-group kill on timeout, each run's `rate_limit_event` to `on_rate_limit`, filing's tool calls to `on_step`. |
| `brainhub/review.py` | Confirm, dismiss and edit for proposals and items. |
| `brainhub/recall.py` | FTS query building, quick cited answer and dig deeper. |
| `brainhub/today.py` | Today sections, plus the catch-all "other". |
| `brainhub/backup.py` | Debounced private-repo push. |
| `brainhub/server.py` + `routes.py` | aiohttp app, startup steps (`startup_error`/`log_error`), reduced app, read-only mode, root watch (exit 75), API, SSE and WebSocket. |
| `brainhub/__main__.py` | CLI: run the hub (port check, root status, lock, exit code), `integration …`, `desktop-config`, `runtime …`, `claude-sync …`. |
| `brainhub/usage.py` | `UsageStore`: per-window merge of status line `rate_limits` (the highest reading in a window wins, ruling 20) and of the hub's own runs (`rate_limits_from_event`, ruling 21), `data/usage.json`, the `/api/usage` view. |
| `brainhub/agents.py` | `AgentTracker`: hook event → status, project/branch/part/where resolution (this OS's folders), `suggest_link`, `os`/`boot` per row, `end_previous_boot`, linger and stale pruning. |
| `brainhub/widget.py` | `/api/widget` summary (synopsis, attention, tiles; ended sessions don't count) and the cached arm reachability probe (15 s reachable, 60 s offline). |
| `brainhub/hook_client.py` | Silent stdlib hook and status line client (rule 11). |
| `brainhub/claude_integration.py` | Install/uninstall/status of our hooks and status line in a Claude Code settings file (backup, atomic write, per-hook ownership); state in the state folder; `hook_paths()` (runtime vs dev), `paths_current`. |
| `brainhub/desktop_config.py` | Writes `desktop.json` in the config folder, legacy or runtime mode. |
| `brainhub/update_step.py` | The stdlib-only update step (rule 15): pull, slot refresh, hook copy, `update.json`, `shell_source`. |
| `brainhub/runtime.py` | `runtime init/status`; `hub_view` for `/api/state.runtime`. |
| `brainhub/claude_sync.py` | claude-sync: pairing, memory merge and pointing, skills, settings sync, store backup. |
| `brainhub/prompts/*.md` + `schemas/filing.schema.json` | Agent prompts. **`prompts/filing.md` is the main calibration knob.** |
| `web/` | UI: `js/api.js`, `app.js` (hash router plus view registry, drive banner), `dom.js`, `drive.js` (banner text), `components/`, `views/`. |
| `web/js/components/confirm-card.js`, `filing-status.js` | The filing card; its live status line ("Filing… <step> · N s", pure helper, tested). |
| `web/js/views/usage.js`, `agents.js`, `agents-model.js` | Usage meters; Agents table with inline part/Where edits and "link to …"; their pure helpers (tested). |
| `web/js/views/settings.js`, `runtime-model.js`, `claude-sync.js`, `claude-sync-model.js` | Settings, with the Runtime line and the Claude Code sync section (per-OS keys, quiet notes). |
| `web/widget.html`, `widget.css`, `js/widget.js`, `js/widget-model.js` | The widget page; `widget-model.js` holds the pure synopsis/tile/popup logic (tested). |
| `desktop/` | Tauri v2 shell: `src-tauri/src/lib.rs` (window, tray, autostart, XWayland, SIGTERM/SIGINT quit, `xdg-open`, watch loop, work-area clamp), `supervisor.rs` (hub start/restart with backoff, exit 75, SIGTERM stop, `hub.log` in the state folder; runtime mode: claude-sync at start, update step, slot spawn), `config.rs` (`desktop.json`, per-OS folders), `claude_watch.rs` (settings watch, 10-minute claude-sync), `shell_source.rs` + `build.rs` (shell source digest), `capabilities/widget.json`, `dist/` (the "Starting BrainHub…" page). |

## Where things stand
- **Phases 1 and 2 are merged** into master (phase 2 via PR #2).
- **The dual-boot port is merged** (plan Tasks 1–12, PR #3, 2026-10-22); CI runs on both OSes.
- **The owner's Linux test drive** (2026-10-22), fixed on branch `test-drive-fixes`: the usage meter lag (rulings 20–21), and filing/recall speed (rulings 22–23: no non-essential traffic in the hub's runs, live filing progress). Numbers in `app/README.md` → Measured → Test-drive follow-up.
- **Next:**
  1. The Windows half of the memory spike (D2), at a scheduled reboot, and the Windows-side checks (D1's "still to check", claude-sync's junctions and drive-letter keys for real).
  2. The rollout with the owner (plan → Rollout): live copy to master, `runtime init`, the shell, the integration, `claude-sync`, then the setup wizard on the real root, on Kubuntu and then Windows. It includes the phase 2 checks (Where and the status line for terminal and VS Code sessions on both OSes; fix `WHERE_BY_ENTRYPOINT` from the raw entrypoints) and design §10's verification.
  3. Calibration (still open from phase 1): the owner collects "wrote X, it extracted Y, expected Z" cases; tune `prompts/filing.md` and the Settings defaults.
  4. Phase 3 (spec §12): the tablet (SM-T110, ES5-only) with its AGENTS tab, LAN binding and pairing, measurement requests. It must reach the hub at one fixed address whichever OS is booted (design §8.3).
- **Later phases:** voice (Kokoro) and notifications, Google Calendar, phone.

## How the owner likes to work
- Brainstorm, then a spec, then a plan, then subagent-driven execution with reviews.
- Concise answers, decisions surfaced plainly, and nothing published or pushed without asking.
