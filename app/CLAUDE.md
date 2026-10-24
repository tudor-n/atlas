# ATLAS: notes for Claude Code sessions

A local "second brain" for one person (the owner), on a dual-boot laptop: Kubuntu 26.04 (the daily driver, where development happens) and Windows 11. The owner writes freely. A headless Claude Code agent files each entry into a git-versioned Markdown brain and proposes tasks and dates. The owner confirms them. Later the owner asks questions and gets their own words back, with citations. Phase 2 adds a usage meter, an Agents panel fed by the owner's Claude Code hooks, a desktop widget and a Tauri desktop shell. The dual-boot port makes it one lifeline on both OSes: the same master commit, one shared root on `D:`, the same features, and Claude Code memory, skills and settings shared through `claude-sync`.

**Phases 1 and 2 and the dual-boot port (PR #3) are merged into master. The owner's test-drive fixes are merged too (rulings 20–23 in `phase2-decisions.md`). The Kubuntu rollout is done except the setup wizard, which the owner runs. **Phase 3 is merged** (#4, 6-dof-arm#3). **Phase 4 (voice and sass) is merged** (PR #5). Its Windows CI hang is fixed in PR #6 (`fix-windows-voice`). **Phase 4.5 is built on branch `phase-4.5`: a native Tauri main window, plus the PC app, widget and tablet redesigned in the owner's robot-arm UI language.** Design and decisions: `specs/2026-10-24-atlas-phase4_5-design.md`, `phase4_5-decisions.md`. **Phases 5 and 6 are on hold (owner, 2026-10-24).** Everything on Windows is on hold: the owner uses Kubuntu only for a while (2026-10-23). **The app is renamed ATLAS (was BrainHub), on branch `atlas`, a PR on top of #7:** every name, the package, the commands, env vars, app id and folders; the first start moves the old folders and repairs the hooks by itself. Spec: `specs/2026-10-24-atlas-rename-design.md`; decisions: `atlas-rename-decisions.md` (A1…).**

> `../NEXT-SESSION.md` is a short pointer: where things stand and the standing rules.

## Read these first (repo root = the dev copy `~/dev/BrainHub`)
| File | What it's for |
|---|---|
| `docs/superpowers/specs/2026-10-20-atlas-design.md` | **The spec, the binding authority.** Intent, goals, anti-goals, all 6 phases. |
| `docs/superpowers/specs/2026-10-21-atlas-phase2-design.md` | Phase 2 made concrete: hook client, installer, agent statuses, usage, widget, desktop shell. |
| `docs/superpowers/specs/2026-10-21-atlas-dual-boot-design.md` | Where it runs and how, on both OSes: shared vs per-OS data, live copy and updates, guards, Claude Code parity. Amends the spec's §4, §11, §13.1 and §14. |
| `docs/superpowers/specs/2026-10-23-atlas-phase3-design.md` | Phase 3, the workbench tablet: LAN access and pairing, the ES5 tablet page, measurement requests, tablet notes, the arm UI's HOME. |
| `docs/superpowers/specs/2026-10-24-atlas-phase4_5-design.md` | Phase 4.5: the native main window, and the instrument-panel design language (tokens, components, screens) every UI change must follow. |
| `docs/superpowers/specs/2026-10-23-atlas-phase4-design.md` | Phase 4, voice and sass: Kokoro in a worker process, when it speaks, the wording rules from the owner's listening test, quotes, controls. |
| `docs/superpowers/specs/2026-10-24-atlas-rename-design.md` | The rename to ATLAS: the names table, what stayed on purpose, the cut-over the code does on the first start and the owner's steps. Decisions: `atlas-rename-decisions.md`. |
| `docs/superpowers/phase1-decisions.md`, `phase2-decisions.md`, `dual-boot-decisions.md`, `phase3-decisions.md`, `phase4-decisions.md`, `phase4_5-decisions.md` | Every build-time ruling that amended the plans (dual boot: D1–D12; phase 3: T1–T27; phase 4: V1–V42; phase 4.5: N/S/B/C/D lines), plus known residuals. Read them before changing core behaviour. |
| `app/README.md` | How to set up, run and test on each OS, where data lives, the drive banner, verification drills, "Known limitations" and "Measured". |
| `app/desktop/README.md` | Building, configuring and running the desktop shell (both OSes, runtime mode). |
| `docs/superpowers/plans/2026-10-20-atlas-phase1.md`, `…/2026-10-21-atlas-phase2.md`, `…/2026-10-22-atlas-dual-boot.md` | The plans as written. Rulings in the decisions docs override them. |

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
5. **Security:** the server binds `127.0.0.1`. The `local_only` middleware checks Host and Origin, and JSON bodies require `Content-Type: application/json`, otherwise 415. Keep it that way.
   - Phase 3 adds one more listener, at `tablet_address`, only while **Tablet over Wi-Fi** is on and the laptop holds that address (`tablet.LanSite`, checked every 15 s).
   - `tablet.lan_guard` (the outermost middleware) judges a request by the local socket it arrived on. A LAN request may reach only `/tablet`, its whitelisted files and `/api/tablet/*`, at the LAN Host, from its own Origin, and (past the page and `POST /api/tablet/pair`) only with a paired device's `bh_device` cookie. Everything else gets 403, even when paired.
   - Never widen that surface; PC-only tablet endpoints live outside the `/api/tablet/` prefix (`/api/tablet-lan`, `/api/tablet-notes`, `/api/devices`, `/api/requests`).
6. **UI:** the PC app, widget and tablet speak the arm UI's language (phase 4.5 spec §2). That means black ground, 1px hairlines, uppercase tracked labels and mono numbers. Colour is used only for meaning: green = ok, amber = attention, `--err` for errors, never red. Use the tokens in `web/css/tokens.css` and the components in `components.css` / `js/components/ui.js`. `tests/test_web_design.py` refuses anything else: rgba, shadows, gradients, transitions other than the switch knob, external URLs, `style=`. User and agent text is set via `textContent` only (see `web/js/dom.js`, `el()`). Never `innerHTML` with data. No build step: plain ES modules. Agent-supplied strings (cwd, title, branch, notification message) count as agent text.
7. **Claude runs only via the `claude` CLI** on the owner's subscription:
   - `ANTHROPIC_API_KEY` is stripped from the environment, and `--bare` is never used.
   - The agents run isolated: `--tools` with an explicit set, `--strict-mcp-config`, `--setting-sources ""` and `--no-session-persistence`. See `claude_runner.py`. (`--setting-sources ""` is also why the hub's own agents never fire ATLAS's hooks.)
   - Auto memory is disabled for the hub's runs (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `child_env()`, D11): the isolation flags don't turn it off, and every filing used to leave a `~/.claude/projects/<key>/memory/` folder.
   - Non-essential traffic is off too (`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, ruling 22): about 1.2–1.6 s faster per run.
   - `claude_path` is a bare command name, never a path (it lives in shared settings); each OS resolves it with `shutil.which`, then `~/.local/bin`.
8. **Commits: plain messages, NO `Co-Authored-By` or `Claude-Session` trailers.** This is the owner's explicit preference.
9. **Never run the hub against the real root while testing:** `/mnt/data/ATLAS` on Kubuntu, `D:\ATLAS` on Windows. They are the same folder: the owner's real brain and the live copy. Use a temp root and a temp state folder (`ATLAS_STATE_DIR=/tmp/bh-state-x … --root /tmp/bh-test-x`; on Windows under `%TEMP%`) and delete both afterwards. Never write under the real root; the only exception is read-only git inspection of the live copy (`git -c safe.directory=/mnt/data/ATLAS …`). Until the owner renames it (`../TODO.md` step 8) the real root is still at its old path (pre-rename: `/mnt/data/BrainHub` = `D:\BrainHub`): the same rule applies to it.
10. **Don't put a `CLAUDE.md` at the repo root** (`~/dev/BrainHub`, or the live copy `D:\ATLAS`). The filing agent's worktrees live under `<root>/data/…`, so it could load that file into the agent. This file lives in `app/` on purpose. Start dev sessions in `~/dev/BrainHub/app` (on Windows, in the dev clone's `app`).
11. **The hook client (`atlas/hook_client.py`) is silent, always exits 0, and is stdlib-only.** It runs as a file (`python -I -S hook_client.py …`), never imports from `atlas`, swallows every exception, and in `event` mode never writes to stdout or stderr (stdout on SessionStart/UserPromptSubmit would be injected into the owner's agent). It sends only the allow-listed fields: never prompts, tool data or transcripts. The status line mode prints exactly one line. In runtime mode the hooks run the stable copy `<state>/hook/hook_client.py` with the slot venv's base interpreter (P4, D10).
12. **Never write the owner's real Claude Code or ATLAS files from tests or manual checks:**
   - Claude Code: `~/.claude/settings.json` on Kubuntu and `%USERPROFILE%\.claude\settings.json` on Windows, and on both `~/.claude/settings.local.json`, `~/.claude/CLAUDE.md` (phase 3 adds a line there), `~/.claude/projects/**` and `~/.claude/skills/**`;
   - the state folder (`~/.local/share/atlas`, `%LOCALAPPDATA%\ATLAS`), the config folder (`~/.config/atlas`, `%LOCALAPPDATA%\ATLAS`), the shared Claude store (`/mnt/data/ClaudeShared` = `D:\ClaudeShared`), `~/.local/bin/atlas-request`, the launcher entry (`~/.local/share/applications/dev.atlas.desktop.desktop`, `~/.local/share/icons/hicolor/256x256/apps/dev.atlas.desktop.png`; `desktop-entry` takes `--data-home`, and conftest sets `XDG_DATA_HOME`), and `~/.config/autostart` or the HKCU Run key (never tick "Start at login" in a test run).
   - Until the owner's first start of ATLAS these still have their old names (pre-rename: `~/.local/share/brainhub`, `~/.config/brainhub`, `%LOCALAPPDATA%\BrainHub`, `~/.local/bin/brainhub-request`, the launcher `dev.brainhub.desktop.desktop` and its icon, the login entry `~/.config/autostart/BrainHub Desktop.desktop` or the HKCU Run value `BrainHub Desktop`, the web view folders `dev.brainhub.desktop`): they are what the migration moves or removes, and are just as off limits. The shell's migration runs only without `ATLAS_CONFIG_DIR`/`ATLAS_STATE_DIR` overrides, so a manual run of the exe must always set both; its tests use fake env dirs only.
   - Use `integration … --settings <temp file>`, `desktop-config --target <temp file>`, `claude-sync` with temp `--shared`, `--claude-dir`, `--home` and `--projects-root`, and temp `ATLAS_STATE_DIR`, `ATLAS_CONFIG_DIR`, `ATLAS_SHARED_DIR`, `ATLAS_BIN_DIR` and `CLAUDE_CONFIG_DIR`. `tests/conftest.py` sets those five (and `LOCALAPPDATA`) to temp folders for every test. A manual tablet check uses a scratch hub on another port with its own state; turning on its LAN site at the laptop's address is fine (ufw keeps it local).
   - The desktop exe writes its web view data and window position to the real folders regardless of env vars (Windows `%LOCALAPPDATA%\dev.atlas.desktop` and `%APPDATA%\dev.atlas.desktop\.window-state.json`; Linux `~/.local/share/dev.atlas.desktop`, `~/.config/dev.atlas.desktop` and `$XDG_RUNTIME_DIR/tray-icon`): delete them after a manual run if they weren't there before.
   - A real `claude -p` creates an empty `~/.claude/projects/<key of its cwd>/memory/` even with `--no-session-persistence` (D10): remove it after an e2e.
13. **The hub ignores events whose entrypoint starts with `sdk-`** (`claude -p` and SDK runs), and events whose cwd is inside its own `data/` or `brain/`. Claude Code sets `CLAUDE_CODE_ENTRYPOINT` itself for hook processes (`sdk-cli` under `-p`), so an e2e that needs rows must force it inside the hook command (see `app/README.md` → Measured).
14. **The hub never creates a root, and never loses a capture.** `rootguard.check_root` classifies the root before anything touches it; only setup on a "fresh" root creates a brain (and `data/root.json`). Missing, foreign and newer roots get the reduced app, which never opens `hub.db`, runs git or writes under the root. A capture that can't reach the raw log goes to this OS's holding inbox, on disk before the API says "saved". `hub.db` is moved aside only for real corruption (malformed, not a database, encrypted, or `quick_check`), never for "locked", "readonly" or "unable to open".
15. **The update step (`atlas/update_step.py`) is stdlib-only and runs as a file**, like the hook client. On the live copy it only fast-forwards (`merge --ff-only --no-autostash --no-overwrite-ignore` to the exact sha CI checked): never reset, stash, checkout, clean or any other merge, and a dirty, diverged or off-master live copy is left alone (D9). Keep the CI job names `test (ubuntu-latest)` and `test (windows-latest)`: its gate reads them.

## Two OSes, one lifeline (dual-boot design §4–§6)
- **Shared root:** `D:\ATLAS` = `/mnt/data/ATLAS` (NTFS, mounted by fstab with `ntfs3`, `nofail`). One OS runs at a time. It holds the live copy, `brain/` and `data/` (`brain/` and `data/` are git-ignored).
- **Live copy:** the checkout at the shared root. Master only, never edited by hand; each OS fast-forwards it through the update step.
- **Dev copy:** `~/dev/BrainHub` on Kubuntu (btrfs). Branches, tests and spikes happen there, only against temp roots (rule 9). Occasional Windows development happens in a separate clone on the local disk, never in the live copy. GitHub carries code between the OSes: commit and push before a reboot.
- **Runtime slots** (P2): each OS runs the hub from `<state>/runtime/slot-a` or `slot-b`: `app/` is a `git archive` of a live-copy commit, `venv/` comes from `uv sync --frozen --no-install-project`. `runtime/current.json` (`{"slot", "commit", "deps"}`) is the pointer; a slot is current only while its `slot.json` names the same commit. `python -m atlas runtime init --live <live copy>` builds the first one. The hub can start (reduced) even when `D:` is missing.
- **The update step**, run by the shell before every hub start with the current slot's python (75 s budget, one at a time via `runtime/update.lock`):
  1. **Pull:** fast-forward the live copy only when it is a clean checkout of master, the fetch works, `origin/master` is a fast-forward of HEAD, and CI finished green on both OSes for that sha (`gh api …/check-runs`: every run completed, none failed, both `test (…)` jobs succeeded; unreadable = not green).
  2. **Refresh:** when the live HEAD isn't the current slot's commit (and is on master, at or behind the local `origin/master`: the provenance guard), build the other slot and flip `current.json` only once its app and venv are both ready.
  3. **Hook:** keep `<state>/hook/hook_client.py` equal to the current slot's copy.
  - Anything skipped is quiet: `pending` in `<state>/update.json`, shown as "update pending (reason)" in Settings' Runtime line.
- **The desktop shell** is built per OS from the live copy into a local `CARGO_TARGET_DIR` with `--locked`; it shows "widget update available" when the live copy's shell source digest differs from the one built in. On Linux it runs under XWayland (D1). `desktop.json` has two modes: legacy (`python`, `app_dir`: dev) and runtime (`live`, `state`).

## Per-OS state and config folders (`osdirs.py`, P10)
- **State:** `$ATLAS_STATE_DIR`, else `${XDG_DATA_HOME:-~/.local/share}/atlas` on Linux and `%LOCALAPPDATA%\ATLAS` on Windows. It holds the integration state (`claude_integration.json`, `statusline_passthrough.txt`), `holding/`, `hub.log`, `runtime/`, `update.json`, `shell.json`, `hook/` and claude-sync's files (`claude-sync.json`, `.log`, `-last.json`, `-backups/`).
- **Config:** `$ATLAS_CONFIG_DIR`, else `${XDG_CONFIG_HOME:-~/.config}/atlas` on Linux and `%LOCALAPPDATA%\ATLAS` on Windows: `desktop.json`.
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
`python -m atlas claude-sync` (`claude_sync.py`, stdlib only, importing only `osdirs`) keeps Claude Code the same on both OSes through the shared store `/mnt/data/ClaudeShared` = `D:\ClaudeShared` (`ATLAS_SHARED_DIR`; a git repo that only `claude-sync init --shared <dir>`, run by hand, creates; a sync on a missing store changes nothing). Memory uses option 2 (a linked memory folder fails on Linux): after merging the local memory folder into the store (backup first; a clashing note kept as `<stem>.<os>.md`; `MEMORY.md` merged by line, with one header line), it writes `autoMemoryDirectory` into `~/.claude/settings.local.json` (home → `memory/home`) and into each paired project's `.claude/settings.local.json` (→ `memory/projects/<name>`). Projects pair by folder name under `~/dev` and `D:\`, plus `pairs.json` exceptions; the hub's filing-worktree keys are never paired, and the live copy is never pointed. Skills are moved into the store and linked back; settings sync per key both ways (later change wins, removals propagate), except the per-OS keys (`hooks`, `statusLine`, `permissions`, `env`, `defaultShell`, `apiKeyHelper`, `autoMemoryDirectory`, any value holding an absolute path, plus the owner's list in Settings). **It never deletes a memory note or a skill, and never shares a per-OS key.** In runtime mode the shell runs it once per start before the first hub, `--settings-only` when `settings.json` changes (5 s poll), and in full every 10 minutes; problems go to `<state>/claude-sync.log` and a quiet note in Settings.

## Run and test
**Kubuntu**, from `~/dev/BrainHub/app` (uv; Python 3.14 from `.python-version`):
- **Setup:** `uv sync --locked --extra test` (makes `.venv`).
- **Run:** `ATLAS_STATE_DIR=/tmp/bh-state-x uv run --no-sync python -m atlas --root /tmp/bh-test-x [--port 8090]`, then open http://127.0.0.1:8090 (widget page: `/widget`).
- **Python tests:** `uv run --no-sync python -m pytest -q` (1372 passed and 20 skipped after the rename; the skips are the Windows-only halves). `tests/test_no_old_name.py` fails on any old name in a tracked file or path outside its small allow-list. `test_api_tablet.py::test_state_on_the_pc` fails while something (the owner's arm server) listens on 127.0.0.1:8080. Set `ATLAS_VOICE_TEST_MODELS` to a folder with the voice files to run the real-synthesis test. Only if parallel runs clash on ports: run each in its own network namespace, `unshare -rn sh -c 'ip link set lo up; uv run --no-sync python -m pytest -q'`. It runs as root inside the namespace, so the 5 chmod-based read-only tests skip there (784 passed, 21 skipped).
- **JS tests:** `source ~/.nvm/nvm.sh` first, then `node --test web/js/components/*.test.mjs web/js/views/*.test.mjs web/js/*.test.mjs web/tablet/*.test.mjs` (134 passing). Check a file's syntax with `node --check <file>`.
- **Rust (desktop shell):** in `desktop/src-tauri`: `CARGO_TARGET_DIR=~/.cache/atlas-target cargo test` (66 passing; also `cargo clippy --all-targets -- -D warnings`) and `cargo build --release`. Never build into `src-tauri/target`.

**Windows**, from the dev clone's `app` (uv, installed with `winget install astral-sh.uv`):
- **Setup:** `uv sync --locked --extra test`.
- **Run:** `set ATLAS_STATE_DIR=%TEMP%\bh-state`, then `uv run --no-sync python -m atlas --root %TEMP%\bh-test [--port 8090]`.
- **Tests:** `uv run --no-sync python -m pytest -q`; the node command as above.
- **Rust:** in `desktop\src-tauri`, with `%USERPROFILE%\.cargo\bin` on `PATH` (it isn't by default): `set CARGO_TARGET_DIR=%LOCALAPPDATA%\atlas-target`, then `cargo test` and `cargo build --release`.

**CLI** (either OS, as `uv run --no-sync python -m atlas …` or with a slot's python):
- `integration install|uninstall|status --root <root> [--settings <path>]`.
- `desktop-config --root <root> [--target <path>]` (legacy) or `desktop-config --runtime --live <live copy> --root <root> [--target <path>]`. It writes the console python, never `pythonw.exe` (Ruling 13); restart the shell after re-running it.
- `runtime init --live <live copy>`, `runtime status`.
- `claude-sync [--shared D] [--claude-dir D] [--home D] [--projects-root D] [--dry-run] [--settings-only]`, `claude-sync init --shared <dir>`.

**CI** (`.github/workflows/ci.yml`): pytest and the node tests on `ubuntu-latest` and `windows-latest` for every PR and push to master; `cargo test` and `cargo build` when `app/desktop/**` changes.

**Test doubles:** `tests/fakes.py` has `FakeRunner` (the Claude boundary, with an `on_run(cwd)` hook to simulate agent edits), `FakeRepo`, `Clock` and `fake_cli` (a fake `claude`/`gh`/`uv` executable: a `.bat` on Windows, a shell script on POSIX). Tests use real SQLite, real git and real files. Only the Claude boundary is faked (plus `gh` and `uv` in the update step's tests). `hook_client.py` and `update_step.py` are tested as real subprocesses.

## Code map
| Path | What it is |
|---|---|
| `atlas/config.py` | `Paths`: data paths come from `--root`, code paths from the package. `DEFAULT_SETTINGS` and validated `save_settings`. |
| `atlas/osdirs.py` | Per-OS state and config folders, `os_name()`, `boot_id()` (stdlib only). |
| `atlas/rootguard.py` | Root status, marker `data/root.json`, fstab mount guard, hub lock `data/hub.lock`, time zone check. |
| `atlas/holding.py` | Per-OS holding inbox: durable hold, per-root listing, flush into the raw log. |
| `atlas/winwatch.py` | Hibernation / Fast Startup warning (Windows; `[]` elsewhere). |
| `atlas/rawlog.py` | Raw log append and read. `read_log(brain, problems=[])` is the tolerant mode; `append_entry(…, entry_id=)` for flushed captures. |
| `atlas/store.py` | SQLite: entries, filings, proposals, items, agents and the FTS5 index; `user_version` migrations; read-only open. Exports `_items/items.json` and `proposals.json`. |
| `atlas/gitrepo.py` | `BrainRepo`: commit, revert, worktrees, cherry-pick, stale lock clearing and the non-interactive push; `safe.directory` and per-OS `core.ignorecase` on every call. |
| `atlas/brainmap.py` | The map: load, validate, save (LF), `os_folders`, `path_os`, `folders_for`. |
| `atlas/filing.py` | Filing prompt, result parsing, Windows path guard, `step_label` (live progress), `FilingWorker` (queue, worktree flow, corrections, rebuild recovery, `progress`). |
| `atlas/claude_runner.py` | `ClaudeRunner`: the isolated `claude -p` call (always `stream-json`, read line by line), `child_env()`, process-group kill on timeout, each run's `rate_limit_event` to `on_rate_limit`, filing's tool calls to `on_step`. |
| `atlas/review.py` | Confirm, dismiss and edit for proposals and items. |
| `atlas/recall.py` | FTS query building, quick cited answer and dig deeper. |
| `atlas/today.py` | Today sections, plus the catch-all "other". |
| `atlas/backup.py` | Debounced private-repo push. |
| `atlas/server.py` + `routes.py` | aiohttp app, startup steps (`startup_error`/`log_error`), reduced app, read-only mode, root watch (exit 75), API, SSE and WebSocket. |
| `atlas/__main__.py` | CLI: run the hub (port check, root status, lock, exit code), `integration …`, `desktop-config`, `runtime …`, `claude-sync …`. |
| `atlas/usage.py` | `UsageStore`: per-window merge of status line `rate_limits` (the highest reading in a window wins, ruling 20) and of the hub's own runs (`rate_limits_from_event`, ruling 21), `data/usage.json`, the `/api/usage` view. |
| `atlas/agents.py` | `AgentTracker`: hook event → status, project/branch/part/where resolution (this OS's folders), `suggest_link`, `os`/`boot` per row, `end_previous_boot`, linger and stale pruning. |
| `atlas/widget.py` | `/api/widget` summary (synopsis, attention, tiles; ended sessions don't count) and the cached arm reachability probe (15 s reachable, 60 s offline). |
| `atlas/hook_client.py` | Silent stdlib hook and status line client (rule 11). |
| `atlas/tablet.py` | Phase 3's way in: `Pairing` (codes, device token hashes), `holds_address`, `LanSite` (the LAN listener that follows the address), `lan_guard`, `TabletFeed` (the long-poll version). |
| `atlas/tablet_routes.py`, `tablet_view.py` | The tablet API (state, wait, notes, answers, ignore, pair), the page's files, the PC endpoints (requests, tablet notes, devices, LAN status); `tablet_view` builds the page's ready-made strings. |
| `atlas/request_client.py` | `atlas-request`: stdlib, runs as a file; an agent asks for workbench measurements (it says what happened, unlike the hook client). |
| `atlas/claude_integration.py` | Install/uninstall/status of our hooks and status line in a Claude Code settings file (backup, atomic write, per-hook ownership); state in the state folder; `hook_paths()` (runtime vs dev), `paths_current`. The pre-rename hooks, status line, wrapper and CLAUDE.md block count as ours; `repair_legacy` replaces them once per full start (`server._repair_claude_integration` → `routes.repair_legacy_integration`, the same arguments as Settings' install; quiet on failure). |
| `atlas/legacy.py` | The pre-rename names, only for the code that finds and replaces our own old entries (`claude_integration`, `desktop_entry`). |
| `atlas/desktop_config.py` | Writes `desktop.json` in the config folder, legacy or runtime mode. |
| `atlas/update_step.py` | The stdlib-only update step (rule 15): pull, slot refresh, hook copy (from `slot_package`: `atlas/`, else a pre-rename slot's package), `update.json`, `shell_source`. |
| `atlas/runtime.py` | `runtime init/status`; `hub_view` for `/api/state.runtime`. |
| `atlas/claude_sync.py` | claude-sync: pairing, memory merge and pointing, skills, settings sync, store backup. |
| `atlas/prompts/*.md` + `schemas/filing.schema.json` | Agent prompts. **`prompts/filing.md` is the main calibration knob.** |
| `web/` | UI: `index.html` (the shell grid), `js/api.js` (also `ws.close`), `app.js` (hash router on `nav-model.js`, view registry, Alt shortcuts, drive banner, reduced and setup modes), `dom.js`, `drive.js` (banner text), `components/`, `views/`. |
| `web/css/` | Phase 4.5's design system: `tokens.css` (every colour and size), `base.css` (the bundled Roboto/Roboto Mono `@font-face` from `web/fonts/`, fields, focus), `components.css` (`.b .pr .am .sw .chip .dot .lb .ro .row .seg .rail .sect-h .tabs .meter4 .notice .read`…), `app.css` (the shell: top bar, `.vw`/`.vw.split` with `.rail .main .side`, `.actbar`, capture bar, tray), `views/<view>.css` (one per view). |
| `web/js/nav-model.js`, `status-model.js`, `press-model.js`, `press.js`, `components/ui.js`, `components/topbar.js` | Routing (tabs, aliases, settings sections), the top bar's status cluster, the `.prs` pressed class and `twice()` (risky actions: first tap arms "TAP AGAIN"), the `el()` builders, the top bar. |
| `web/js/components/confirm-card.js`, `filing-status.js` | The filing card; its live status line ("Filing… <step> · N s", pure helper, tested). |
| `web/js/views/usage.js`, `agents.js`, `agents-model.js` | Usage meters; Agents table with inline part/Where edits and "link to …"; their pure helpers (tested). |
| `web/js/views/settings.js`, `settings-sections.js`, `settings-model.js`, `runtime-model.js`, `claude-sync.js`, `claude-sync-model.js` | Settings as a rail of sections (`#/settings/<section>`, one renders at a time), the Runtime line, the Claude Code sync section. |
| `web/js/views/today.js`, `today-review.js`, `today-model.js`, `components/confirm-card.js` | Today (the review line, items, snooze), the review pop-up `today-review.js` (the queue and one open card; it opens by itself for unseen entries) and the filing confirm card (also the tray's compact rows). |
| `web/js/views/browse-shell.js`, `browse.js`, `browse-model.js`, `agents.js`, `usage.js` | Browse (rail: deadlines, people, projects) and Agents with the usage panel (`#/usage` focuses it). |
| `web/widget.html`, `widget.css`, `js/widget.js`, `js/widget-model.js` | The widget page; `widget-model.js` holds the pure synopsis/tile/popup logic (tested). |
| `web/js/legacy-keys.js` | The setup wizard's and the widget's drafts read their pre-rename `localStorage` key once, when the new one is absent (tested). The tablet's `bh_device` cookie and `bh.*` keys never changed. |
| `tests/test_legacy.py`, `tests/test_no_old_name.py` | The rename's cut-over on the Python side; the guard on old names, whose `EVERYWHERE`/`ALLOWED` lists the rename script reads too. |
| `web/tablet/` | The tablet page: **ES5 only** for the SM-T110's Android 4.2 WebKit (`tests/test_tablet_es5.py` refuses newer syntax, missing APIs and CSS). `tablet.js` (screens, in-page keyboard, long-poll), `kb.js` and `logic.js` (pure, node-tested via `module.exports`), `tablet.css` (fixed 1024×600 layout). |
| `web/js/views/tablet.js`, `tablet-settings.js`, `tablet-model.js` | The PC's Tablet view and Settings → Tablet; pure helpers (tested). |
| `atlas/voice.py` | Phase 4: `Announcer` (the gates: on, installed, event, quiet hours, pause, dedupe; agent transitions, the reminder tick and catch-up, merge, quips; per-OS `voice.json`) and `Speaker` (one thread, a bounded queue, the worker's lifecycle, idle exit). |
| `atlas/voice_lines.py`, `quotes.py` | What the voice says (the owner's wording rules: short sentences, no colons, numbers in words) and the quip file `brain/_voice/quotes.md` (seed, parser, cycling picker). |
| `atlas/voice_worker.py` | The child process: kokoro-onnx synthesis, the espeak data-path guard (160 characters), players per OS, JSON lines on stdin/stdout, `ATLAS_VOICE_FAKE` for tests. |
| `atlas/desktop_entry.py` | `desktop-entry install|uninstall|status`: the Linux launcher entry and icon for the main window (phase 4.5); `install` also removes the pre-rename entry and icon when ours. |
| `atlas/voice_files.py`, `voice_routes.py` | The pinned download into `data/voice/`; the app wiring and `/api/voice*` (PC only). |
| `web/js/views/voice-settings.js`, `voice-model.js` | Settings → Voice and its pure helpers (tested); the widget's speaker uses `widgetVoice`. |
| `desktop/` | Tauri v2 shell: `src-tauri/src/lib.rs` (window, tray, autostart, XWayland, SIGTERM/SIGINT quit, `xdg-open`, watch loop, work-area clamp), `supervisor.rs` (hub start/restart with backoff, exit 75, SIGTERM stop, `hub.log` in the state folder; runtime mode: claude-sync at start, update step, slot spawn), `config.rs` (`desktop.json`, per-OS folders), `migrate.rs` (the rename's cut-over, first thing at start: the legacy state and config folders moved, `desktop.json` repointed, `migrated.json`, the legacy login entry; `LEGACY_PACKAGE` for the supervisor's `slot_package`, which also gives children the legacy state variable), `claude_watch.rs` (settings watch, 10-minute claude-sync), `voice.rs` (the tray's Pause voice POST, std TCP), the lazy `main` window (`show_main`, the widget-only `open_main` command, `--open`, close hides; phase 4.5), `shell_source.rs` + `build.rs` (shell source digest), `capabilities/widget.json`, `dist/` (the "Starting ATLAS…" page). |

## Where things stand
- **Phases 1 and 2 are merged** into master (phase 2 via PR #2).
- **The dual-boot port is merged** (plan Tasks 1–12, PR #3, 2026-10-22); CI runs on both OSes.
- **The owner's Linux test drive** (2026-10-22), fixed and merged (2026-10-23): the usage meter lag (rulings 20–21), and filing/recall speed (rulings 22–23: no non-essential traffic in the hub's runs, live filing progress). Numbers in `app/README.md` → Measured → Test-drive follow-up.
- **Next:**
  1. The Kubuntu rollout with the owner (plan → Rollout): done on 2026-10-23 (live copy to master, `runtime init`, the shell, the integration; `claude-sync` waits for Windows) except the setup wizard on the real root, which the owner runs. It includes the phase 2 checks for Kubuntu (Where and the status line for terminal and VS Code sessions; fix `WHERE_BY_ENTRYPOINT` from the raw entrypoints) and the Kubuntu half of design §10's verification.
  2. **On hold (owner, 2026-10-23): everything on Windows.** The owner uses Kubuntu only for a while: the Windows half of the memory spike (D2), the Windows-side checks (D1's "still to check", claude-sync's junctions and drive-letter keys for real), the Windows rollout and its half of §10. Meanwhile, code stays portable and CI keeps running on both OSes.
  3. Calibration (still open from phase 1): the owner collects "wrote X, it extracted Y, expected Z" cases; tune `prompts/filing.md` and the Settings defaults.
  4. **Phase 3 is merged** (2026-10-23). Its rollout (design §11) and device checks are on the owner's list in `../TODO.md`.
  5. **Phase 4 (voice and sass) is merged.** The Windows fix is in PR #6. Then its rollout (design §11, `../TODO.md`): download the voice, Play sample, a line of each kind, the tray pause, the quotes.
  6. **Phase 4.5 (the native app and the new look) is built on `phase-4.5`**; the owner merges it. Then its checks (`../TODO.md` step 7).
  7. **The rename to ATLAS** is built on branch `atlas` (on top of #7); the owner merges it after #6 and #7, then does the cut-over (`../TODO.md` step 8). The mechanical part is the script `rename.sh`, kept outside the repo (`atlas-rename-decisions.md` A2): re-run it on files that come in under the old names after a rebase.
- **On hold (owner, 2026-10-24):** phases 5 (Google Calendar) and 6 (phone), until the phases so far prove useful.
- **The owner's to-do list is `../TODO.md`:** every check or step on their side goes there.

## How the owner likes to work
- Brainstorm, then a spec, then a plan, then subagent-driven execution with reviews.
- Concise answers, decisions surfaced plainly, and nothing published or pushed without asking.
