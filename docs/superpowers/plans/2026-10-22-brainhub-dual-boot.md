# BrainHub Dual Boot (Kubuntu + Windows) Implementation Plan

> **For agentic workers:** implement this plan task by task, in order, with a review after each task (implementer, then a reviewer that checks the task against this plan, the design and `app/CLAUDE.md`). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** one BrainHub lifeline across both OSes of the dual-boot laptop. Whichever OS is booted runs the same master commit against one shared root on `D:` (`/mnt/data/BrainHub` = `D:\BrainHub`), with the same widget, tray, Agents panel, usage meter, integration and backup. A missing or read-only drive never creates an empty brain and never loses a capture. Claude Code memory, skills and portable settings are the same on both OSes.

**Architecture:**
- **OS-neutral shared data, per-OS state.** Everything in `brain/` and the shared part of `data/` is free of absolute paths and per-OS state. Per-OS state lives in a new per-OS state folder (`~/.local/share/brainhub/` on Linux, `%LOCALAPPDATA%\BrainHub\` on Windows).
- **Root guard.** At start, the hub classifies the root (ok, fresh, missing, read-only, foreign, newer) before it touches anything. Anything but ok/fresh runs a reduced app with a banner, and captures go to a per-OS holding inbox.
- **Runtime slots.** Each OS runs the hub from one of two local runtime slots (A/B: an exact copy of a live-copy commit plus a venv). A stdlib-only update step, run by the shell before every hub start, fast-forwards the live copy (CI-gated), fills the inactive slot and flips a pointer file.
- **The same Tauri shell on both OSes**, with XDG paths, `xdg-open`, SIGTERM-then-SIGKILL and the Wayland approach chosen by the spike.
- **`claude-sync`:** a stdlib-only module that links each OS's Claude Code memory and skills to a shared store on `D:` and keeps the portable settings in step.

**Tech Stack:**
- Python 3.12+ with aiohttp, pytest and pytest-aiohttp. **uv on both OSes**, with `app/uv.lock` committed (Plan decision P1).
- Vanilla ES modules, `node --test` (Node 24, via nvm on Kubuntu).
- Rust 1.99.1, Tauri 2.x (existing crates). On Kubuntu the Tauri prerequisites need installing once (Task 2).
- `gh` (logged in on Kubuntu; installed once on Windows during rollout), git 2.5x, Claude Code CLI.
- GitHub Actions: `ubuntu-latest` and `windows-latest`.

**Spec:**
- `docs/superpowers/specs/2026-10-21-brainhub-dual-boot-design.md` (approved; referred to as "design §N").
- Its parent `docs/superpowers/specs/2026-10-20-brainhub-design.md`, the phase 2 design, `docs/superpowers/phase1-decisions.md` and `docs/superpowers/phase2-decisions.md`.
- Read the dual-boot design and `app/CLAUDE.md` (hard rules) before any task.

## Global Constraints

**Repo and commits**
- Work in the dev copy `~/dev/BrainHub` on branch `dual-boot` (branched from master after the phase-2 merge). Commit after each task with a plain message (`feat: …`, `fix: …`, `test: …`, `ci: …`, `docs: …`). **Never add `Co-Authored-By` or `Claude-Session` trailers.**
- **Nothing is pushed without asking the owner.** Pushing the branch (to let CI run on Windows) needs an explicit OK each time it's first needed. The owner merges PRs on GitHub.
- Never commit `app/nul` (created on Linux by the old cmd-syntax test until Task 1 fixes it) or any other Windows-reserved name.

**Never touch the real data or settings**
- Never run the hub against `/mnt/data/BrainHub` and never write anything under it. The only exception is read-only git inspection of the live copy (`git -c safe.directory=/mnt/data/BrainHub …`), and the rollout section at the end, which is done with the owner.
- **Never write the real `~/.claude/settings.json`, `~/.claude/projects/**`, `~/.claude/skills/**`, the real state folder (`~/.local/share/brainhub`) or the real `~/.config/brainhub`.** Tests point everything at temp folders. Task 3 extends the autouse fixture in `tests/conftest.py` with `BRAINHUB_STATE_DIR` and `BRAINHUB_CONFIG_DIR`, and later tasks add `BRAINHUB_SHARED_DIR` the same way.
- Manual runs use a temp root (`/tmp/bh-test-…`) and a temp state folder (`BRAINHUB_STATE_DIR=/tmp/bh-state-…`), deleted afterwards.

**Existing hard rules stay** (`app/CLAUDE.md` rules 1–13): the raw log is sacred; nothing becomes an item without confirmation; the filing agent never touches the main checkout; every hub commit re-indexes; `127.0.0.1` + `local_only` + JSON content type; `textContent` only; claude CLI only, isolated; the hook client is silent and stdlib-only; no `CLAUDE.md` at the repo root. New tunables go through `DEFAULT_SETTINGS`/`validate_setting`/Settings, except per-OS values, which are never in shared settings.

**Portability rules for new code**
- No `win32`-only branch without a POSIX branch next to it, and the reverse. A `sys.platform` branch gets a test on each side: the side that can't run on the current OS is a test skipped there and run by CI on the other OS.
- Every new file the hub writes in shared space is written with `newline="\n"`; every new JSON file in shared space holds no absolute paths.
- Every new git call on the live copy, `brain/` or the shared Claude store passes `-c safe.directory=<that path>`.

**Times and tests**
- Times stored by the hub stay local ISO strings at seconds precision.
- Commands run from `~/dev/BrainHub/app`:
  - Python: `uv run python -m pytest -q`. The 10 Windows-only failures are fixed in Task 1; from then on everything passes on Linux (and in CI on both OSes).
  - JS: `node --test web/js/components/*.test.mjs web/js/views/*.test.mjs web/js/*.test.mjs` (load nvm first: `source ~/.nvm/nvm.sh`).
  - Rust: in `app/desktop/src-tauri`, `CARGO_TARGET_DIR=~/.cache/brainhub-target cargo test` (needs the Task 2 apt packages). Never build into `src-tauri/target` on `D:`.

## Plan Decisions (the builder's calls the design left open)

| # | Decision | Design ref |
|---|---|---|
| P1 | **uv on both OSes; `app/uv.lock` is committed.** CI and the runtime slots use `uv sync --frozen`. The rollout installs uv on Windows (`winget install astral-sh.uv`). | §5.2 part 3, §5.5 |
| P2 | **Runtime copy = two slots (A/B)** under the state folder: `runtime/slot-a/{app,venv}` and `runtime/slot-b/…`, with `runtime/current.json` (`{"slot", "commit", "deps"}`) as the atomic pointer. The update step fills the inactive slot and then rewrites `current.json`, so the runtime copy and venv switch together and a failed venv sync leaves the old pair live. The slot's app is a `git archive` of the commit (a file copy of the tracked tree). The venv holds the dependencies only (`uv sync --frozen --no-install-project`); the hub runs as `python -m brainhub` with cwd = the slot's `app`. | §5.1, §5.2 |
| P3 | **The update step is `brainhub/update_step.py`: stdlib-only, run as a file** (like the hook client) by the shell before each hub start. It uses the git, gh and uv CLIs. A first slot is created by `python -m brainhub runtime init`. | §5.2 |
| P4 | **Stable hook paths:** after every slot flip the update step copies `hook_client.py` to `<state>/hook/hook_client.py` (atomic replace). The hooks and status line run it with the **base interpreter** (`sys._base_executable` of the slot venv, resolved to a console python), which no slot flip moves. | §8.2 |
| P5 | **Root marker = `data/root.json`:** `{"id": <32 hex>, "brain_format": 1, "created": <iso>, "tz": {"jan": <UTC offset s on 15 Jan>, "jul": <on 15 Jul>}}` (amended by ruling D6). It also carries the brain format version (§5.2) and the creating OS's time-zone offsets, which every start compares against its own (§6.5 item 7). | §5.2, §6.5 |
| P6 | **`claude_path` stays a bare command name** (validation rejects path separators). `ClaudeRunner` resolves it with `shutil.which`, then falls back to `~/.local/bin/<name>` (`<name>.exe` on Windows). | §6.2 |
| P7 | **Per-OS map folders:** a project gains `"os_folders": {"windows": [...], "linux": [...]}`. The legacy `folders` list keeps working: each entry counts for the OS its shape belongs to (drive letter or UNC → windows; leading `/` → linux). The editor writes `os_folders` and drops `folders`. | §6.4 |
| P8 | **Coming back from reduced mode:** while reduced, the hub re-checks the root every 15 s. When it becomes usable, it stops with exit code **75**, and the shell's supervisor restarts it at once (no backoff), so the full app starts fresh. A hand-run hub prints that it stopped so it can be restarted. | §6.5 |
| P9 | **CI gate:** a master commit may be pulled only when its check runs (via `gh api repos/<owner>/<repo>/commits/<sha>/check-runs`) include `test (ubuntu-latest)` and `test (windows-latest)` with conclusion `success`, and no check run has failed (`failure`, `cancelled`, `timed_out`, `action_required`). Pending checks mean "not yet". | §5.5 |
| P10 | **Per-OS folders:** state = `$BRAINHUB_STATE_DIR`, else Linux `${XDG_DATA_HOME:-~/.local/share}/brainhub`, Windows `%LOCALAPPDATA%\BrainHub`. Shell config = `$BRAINHUB_CONFIG_DIR`, else Linux `${XDG_CONFIG_HOME:-~/.config}/brainhub`, Windows `%LOCALAPPDATA%\BrainHub`. The env overrides exist for tests and are honoured by the Rust side too. | §4 |
| P11 | **Boot identity** (agent clean-up and the hub lock): Linux `/proc/sys/kernel/random/boot_id`; Windows the registry boot counter (`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters\BootId`, read-only), falling back to the boot time from `GetTickCount64` rounded to the minute. A row or lock from the other OS always has a different boot id. (Amended by ruling D4.) | §6.5, §6.6 |

## Review Focus

1. **A missing, empty or read-only `D:` never creates a brain, never moves `hub.db` aside, and never loses a capture.** *Pinned:* Task 5 `test_missing_root_is_never_created`, `test_unmounted_fstab_mount_point_is_missing`; Task 6 `test_readonly_root_keeps_hub_db`, `test_capture_held_then_flushed_with_original_ts`, `test_flush_is_idempotent_after_crash`.
2. **Names that are legal on Linux but not on Windows never reach `brain/`.** *Pinned:* Task 7 `test_filing_rejects_windows_invalid_paths`, `test_filing_rejects_case_clash`.
3. **The update step never merges, resets, stashes or switches branches, and leaves a dirty or diverged live copy alone.** *Pinned:* Task 9 `test_dirty_live_copy_is_left_alone`, `test_diverged_history_is_left_alone`, `test_ci_not_green_is_not_pulled`, `test_failed_venv_sync_keeps_previous_slot`.
4. **Integration installed on both OSes, then removed on one, leaves the other untouched.** *Pinned:* Task 3 `test_integration_state_is_per_os`; Task 10 `test_uninstall_on_one_os_keeps_other_statusline`.
5. **`claude-sync` never deletes a memory note or skill, and never shares a per-OS settings key.** *Pinned:* Task 11 `test_merge_keeps_both_on_conflict`, `test_per_os_keys_never_shared`, `test_local_folder_backed_up_before_link`.
6. **Two hubs never run on one root, but a lock left by the other OS never blocks a start.** *Pinned:* Task 5 `test_lock_from_other_os_is_taken_over`, `test_lock_live_same_boot_refuses`.

---

## File Structure

| Path | Responsibility | Task |
|---|---|---|
| `.gitattributes` (create) | LF for text, binaries marked | 1 |
| `.github/workflows/ci.yml` (create) | pytest + node tests on both OSes; Rust on `app/desktop/**` changes | 1 |
| `app/uv.lock` (commit) | locked dependencies (P1) | 1 |
| `app/tests/fakes.py`, `test_claude_runner.py`, `test_hook_client.py`, `test_agents.py`, `test_gitrepo.py` (modify) | portable fakes, per-OS cases | 1 |
| `app/desktop/src-tauri/src/supervisor.rs` (modify) | unix spawn test | 1 |
| `docs/superpowers/dual-boot-decisions.md` (create) | build-time rulings, spike results | 2 → |
| `app/brainhub/osdirs.py` (create) | per-OS state and config folders, OS name, boot id | 3 |
| `app/brainhub/claude_integration.py` (modify) | state + passthrough in the state folder | 3, 10 |
| `app/brainhub/config.py` (modify) | `claude_path` bare-name rule | 3 |
| `app/brainhub/claude_runner.py` (modify) | `claude` resolution fallback; process groups | 3, 7 |
| `app/brainhub/brainmap.py` (modify) | LF `map.json`; `os_folders`; folders for the current OS | 3, 4 |
| `app/brainhub/agents.py`, `routes.py`, `web/js/components/map-editor.js`, `web/js/views/agents*.js` (modify) | per-OS folder matching, name-match suggestion | 4 |
| `app/brainhub/rootguard.py` (create) | root status, marker, mount guard, hub lock, tz check | 5 |
| `app/brainhub/store.py` (modify) | `user_version` + migrations; agents `os`/`boot` columns; read-only open | 5, 6, 7 |
| `app/brainhub/holding.py` (create) | per-OS holding inbox | 6 |
| `app/brainhub/server.py`, `__main__.py` (modify) | reduced app, read-only mode, exit 75, startup clean-up | 5, 6, 7 |
| `app/brainhub/winwatch.py` (create) | Fast Startup / hibernation check (Windows) | 6 |
| `app/web/js/app.js`, `web/js/widget*.js`, `web/style.css`, `web/widget.css` (modify) | drive banner, held count, quiet warnings | 6 |
| `app/brainhub/gitrepo.py` (modify) | `safe.directory`, `core.filemode=false`, stale locks | 7 |
| `app/brainhub/filing.py`, `prompts/filing.md` (modify) | Windows-invalid / case-clash rejection, lowercase slugs | 7 |
| `app/brainhub/hook_client.py` (modify) | POSIX process-group kill of the passthrough | 7 |
| `app/brainhub/desktop_config.py` (modify) | XDG target, `bin/python`, runtime mode | 8, 9 |
| `app/desktop/src-tauri/src/{config,supervisor,lib}.rs` (modify) | XDG paths, xdg-open, SIGTERM, log in the state folder, exit 75, update step | 8, 9 |
| `app/brainhub/update_step.py` (create) | stdlib-only update step (P2, P3, P9) | 9 |
| `app/brainhub/runtime.py` (create) | `runtime init/status` CLI helpers | 9 |
| `app/brainhub/claude_sync.py` (create) | Claude Code parity: links, merge, settings sync, backup | 11 |
| `app/README.md`, `app/desktop/README.md`, `app/CLAUDE.md`, `README.md`, `NEXT-SESSION.md` | docs | 12 |

---

### Task 1: Portable test suite, line endings and CI on both OSes

**Files:**
- Create: `.gitattributes`, `.github/workflows/ci.yml`
- Commit: `app/uv.lock` (regenerate with `uv lock` if it is stale)
- Modify: `app/tests/fakes.py` (or a new `app/tests/fake_cli.py`), `app/tests/test_claude_runner.py`, `app/tests/test_hook_client.py`, `app/tests/test_agents.py`, `app/tests/test_gitrepo.py`, `app/desktop/src-tauri/src/supervisor.rs`

**Interfaces:**
- `.gitattributes` at the repo root:
  ```
  * text=auto eol=lf
  *.png binary
  *.ico binary
  *.icns binary
  *.bat text eol=crlf
  ```
  `brain/.gitattributes` (`* -text`) is unaffected: `brain/` is its own repo. After adding it, run `git add --renormalize .` and check with `git ls-files --eol` that nothing changed except what's expected (the index is already all LF today).
- `fake_cli(tmp_path, body) -> str` (test helper): writes `fake_claude.py`. On Windows it returns a `.bat` wrapper (today's form). On POSIX it returns an executable `fake_claude` shell script (`#!/bin/sh\nexec "<sys.executable>" "<script>" "$@"\n`, mode 0o755).
- `.github/workflows/ci.yml`:
  - `on`: `pull_request`, `push` to `master`, `workflow_dispatch`.
  - Job `test`, matrix `os: [ubuntu-latest, windows-latest]`, job name `test (${{ matrix.os }})` (the names P9's gate reads). Steps: checkout; `astral-sh/setup-uv` (with cache); `uv sync --frozen --extra test` in `app`; `uv run python -m pytest -q`; `actions/setup-node` (Node 24); the node test command.
  - Job `desktop`, matrix the same, name `desktop (${{ matrix.os }})`. It runs only when `app/desktop/**` changed (use `dorny/paths-filter` in a small `changes` job, or skip with an `if:`). On Ubuntu it apt-installs `libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev`. It caches cargo (`Swatinem/rust-cache`) and runs `cargo test` and `cargo build` (debug is enough for a build check).
  - `concurrency` cancels superseded runs on the same ref.
  - Pin action versions to major tags.

- [x] **Step 1: Make the 10 failing tests portable** (keep each test's intent):
  - `test_claude_runner.py`: the 8 live-process tests use `fake_cli`.
  - `test_hook_client.py::test_statusline_passthrough_hang_is_bounded`: per-OS hang command. Windows keeps `ping -n 10 127.0.0.1 >nul & echo late`; POSIX uses `sleep 10; echo late`. No test may write `nul` or any other reserved name.
  - `test_agents.py::test_project_from_map_is_case_and_separator_insensitive`: split into `…_windows` (today's asserts, `skipif(sys.platform != "win32")`) and `test_project_from_map_is_exact_on_posix`. Map folder `/home/u/dev/RobotArmControl/`: `/home/u/dev/RobotArmControl/src` matches; `/home/u/dev/robotarmcontrol/src` and `/home/u/dev/RobotArmControlOld` don't; longest match wins between `/home/u/Work` and `/home/u/Work/robot`.
  - The two "Windows console flags" skips stay Windows-only; that's correct, they check `CREATE_NO_WINDOW`.
- [x] **Step 2: Rust.** Add `#[cfg(unix)] spawns_logs_and_stops_own_child_unix`, using `/bin/sh` as the stand-in python (it exits at once on null stdin, like `cmd.exe` today). Make `config_missing_file_defaults` use a path that is missing on both OSes (keep the `Z:\…` one; it's simply missing on Linux).
- [x] **Step 3: Run** the Python and JS suites on Linux: everything passes, and `app/nul` does not appear (`git status` is clean apart from the intended changes).
- [x] **Step 4: Add `.gitattributes`, `uv.lock` and `ci.yml`.** Validate the workflow locally with `actionlint` if available (`go run`/binary download is fine in a temp dir); otherwise review the YAML carefully.
- [x] **Step 5: Commit** `ci: portable test suite, LF line endings, CI on Ubuntu and Windows`.
- [x] **Step 6: Windows CI run (needs the owner).** Ask the owner to OK pushing `dual-boot` and opening a draft PR, so the workflow runs on both OSes. Fix whatever Windows CI reports (CRLF assumptions, path assumptions) before moving on; Windows failures from here on block the next task.

---

### Task 2: Wayland widget spike (gates Task 8)

**Files:**
- Create: `docs/superpowers/dual-boot-decisions.md` (Ruling D1: the spike result)
- No product code, unless a tiny change is needed to run the shell on Linux at all. Then it goes in as the first commit of Task 8, not here.

**Setup:**
- Ask the owner to install the Tauri prerequisites (sudo):
  `sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev`
- Build with `CARGO_TARGET_DIR=~/.cache/brainhub-target cargo build` in `app/desktop/src-tauri`.
- Run a hub on a temp root and temp state (`uv run python -m brainhub --root /tmp/bh-spike --port 8097`). Point the shell at it **without code changes**: `config.rs` reads `LOCALAPPDATA`, so run the shell with `LOCALAPPDATA=/tmp/bh-spike-cfg` and a `/tmp/bh-spike-cfg/BrainHub/desktop.json` holding `{"port": 8097}` (connect-only, nothing spawned). Run it with `HOME`-relative Tauri data left as-is, and note which folders the run creates (`~/.local/share/dev.brainhub.desktop`, `~/.config/dev.brainhub.desktop`, …) so they can be deleted afterwards.

**Checks (each option):**
- Option 1: XWayland, `GDK_BACKEND=x11 ./brainhub-desktop`.
- Option 2: native Wayland plus a KWin window rule for keep-above, skip-taskbar, skip-switcher and position. Install it with `kwriteconfig6` into `~/.config/kwinrulesrc` and reload with `qdbus6 org.kde.KWin /KWin reconfigure`. Record the exact rule, and remove it afterwards.
- For each option record yes/no:
  - transparent background;
  - frameless;
  - stays above a maximised normal window;
  - not in the taskbar or Alt+Tab;
  - draggable (`start_dragging` from the page);
  - position remembered across a restart;
  - kept on screen after the widget grows (expand/popup);
  - legible over a light and a dark wallpaper;
  - tray icon visible with a working menu (StatusNotifierItem);
  - no blank window (if blank, retry with `WEBKIT_DISABLE_DMABUF_RENDERER=1`).
- Screenshots via `spectacle -b -n -f -o /tmp/bh-spike-N.png` can be viewed to check the visual items. Items that need the owner's eyes or hands (dragging, Alt+Tab) are asked of the owner in one short list.

- [x] **Step 1:** Prerequisites installed; shell builds.
- [x] **Step 2:** Option 1 checks recorded.
- [x] **Step 3:** Option 2 checks recorded. (Baseline only: native Wayland without a rule. Option 1 was chosen; see D1.)
- [x] **Step 4: Decide.** Pick the option that meets the parent spec §3.1 target fully; prefer option 1 if both do (no KWin rule to install). **If neither meets it fully: stop and bring the results to the owner** (design §8.1). Write the result as Ruling D1, with the env vars or rule the shell must apply.
- [x] **Step 5:** Clean up (KWin rule, Tauri data folders, temp roots). **Commit** `docs: Wayland widget spike result (ruling D1)`.

---

### Task 3: Per-OS state folder, OS-neutral shared data

**Files:**
- Create: `app/brainhub/osdirs.py`, `app/tests/test_osdirs.py`
- Modify: `app/brainhub/claude_integration.py`, `app/brainhub/routes.py`, `app/brainhub/__main__.py`, `app/brainhub/config.py`, `app/brainhub/claude_runner.py`, `app/brainhub/brainmap.py`, `app/tests/conftest.py`, and the affected tests (`test_claude_integration.py`, `test_api_integration.py`, `test_config.py`, `test_claude_runner.py`, `test_brainmap.py`)

**Interfaces:**
- `osdirs.py` (stdlib only):
  - `os_name() -> str`: `"windows"` or `"linux"` (`"macos"` mapped to `"linux"` for folder rules is out of scope; raise nothing, just return `"linux"` for any non-win32).
  - `state_dir() -> Path`: P10. Never created here.
  - `config_dir() -> Path`: P10.
  - `boot_id() -> str`: P11. It never raises: on failure it returns `"unknown-" + str(os.getpid())`, so the caller treats it as a fresh boot.
- `tests/conftest.py`: the autouse fixture also sets `BRAINHUB_STATE_DIR=<tmp>/state` and `BRAINHUB_CONFIG_DIR=<tmp>/config`.
- `claude_integration`:
  - `install(…, state_dir: Path)` and `uninstall(…, state_dir: Path)` replace `data_dir`. `STATE_FILE` and `PASSTHROUGH_FILE` live in `state_dir`.
  - **Legacy adoption (Windows only):** if `<data>/claude_integration.json` or `<data>/statusline_passthrough.txt` exists and the state folder has neither, move them into the state folder. A new `adopt_legacy(data_dir, state_dir, os_name)` is called by install and uninstall (never by the read-only API view: ruling D3). On Linux the legacy files are left alone and never read.
  - Routes and the CLI pass `osdirs.state_dir()`.
- `config.validate_setting("claude_path", v)`: also rejects values containing `/` or `\` (P6), with the message "claude_path must be a command name (like claude), not a path; each OS finds it on its own PATH".
- `ClaudeRunner.__init__`: `shutil.which(name)`, then `Path.home() / ".local" / "bin" / (name + (".exe" if win32 else ""))` if that is a file.
- `brainmap.save_map`: write with `open(…, "w", encoding="utf-8", newline="\n")`.
- `/api/state` gains `"os": osdirs.os_name()`.

- [x] **Step 1: Write failing tests.**
  - `test_osdirs.py`:
    - `test_state_dir_env_override`.
    - `test_state_dir_linux_xdg` (posix-only): `XDG_DATA_HOME` set, then unset, falling back to `~/.local/share/brainhub`.
    - `test_state_dir_windows_localappdata` (win32-only).
    - `test_config_dir_*` the same way.
    - `test_boot_id_is_stable_within_a_process` (two calls equal and non-empty).
  - `test_claude_integration.py`:
    - `test_integration_state_is_per_os`: two state folders (two "OSes") on one settings file each, with one shared data folder. Install on both, uninstall on one: the other's state and passthrough files are intact, and nothing was written to `data/`.
    - `test_legacy_state_adopted_on_windows_only`: monkeypatch `os_name`.
  - `test_config.py`: `test_claude_path_must_be_bare` (`"claude"` ok; `"C:\\x\\claude.exe"` and `"/usr/bin/claude"` rejected; a stored absolute path in `settings.json` falls back to `"claude"` with a problem listed).
  - `test_claude_runner.py`: `test_claude_found_in_local_bin_fallback` (monkeypatch `Path.home` and an empty PATH).
  - `test_brainmap.py`: `test_map_json_is_lf` (raw bytes contain no `\r\n`, on any OS).
- [x] **Step 2: Run:** FAIL.
- [x] **Step 3: Implement.** Update existing integration tests to pass `state_dir`.
- [x] **Step 4: Run** the full Python suite: PASS.
- [x] **Step 5: Commit** `feat: per-OS state folder; integration state leaves shared data; bare claude_path; LF map.json`.

---

### Task 4: Per-OS project folders in the map, name-match suggestion

**Files:**
- Modify: `app/brainhub/brainmap.py`, `app/brainhub/agents.py`, `app/brainhub/routes.py`, `app/web/js/components/map-editor.js` (+ its test), `app/web/js/views/agents.js`, `app/web/js/views/agents-model.js` (+ test), `app/web/js/views/setup.js` (if the hint text needs it)
- Test: `app/tests/test_brainmap.py`, `app/tests/test_agents.py`, `app/tests/test_api_agents.py`

**Interfaces:**
- `brainmap.path_os(folder: str) -> str | None`: `"windows"` for `X:\…`, `X:/…` or `\\…`; `"linux"` for `/…`; None otherwise (ignored).
- `brainmap.folders_for(project: dict, os_name: str) -> list[str]`: `os_folders[os_name]` plus the legacy `folders` entries whose `path_os` matches (P7).
- `validate_map`: `os_folders`, if present, must be a dict with only `windows`/`linux` keys whose values are lists of strings, and each entry's `path_os` must match its key. That catches a Windows path typed into the Linux column.
- `agents.resolve_project(cwd, m)` uses `folders_for(p, os_name())`.
- `agents.suggest_link(cwd, m) -> dict | None`: for an unmapped cwd, take its git top (else cwd). If its folder name equals the last component of exactly one other-OS folder of one project (case-insensitive), return `{"slug", "name", "folder": <the top folder path>}`. Otherwise (no match, or ambiguous) None. Rows gain `suggest` (None for mapped rows).
- `POST /api/agents/{session_id}/link` (JSON `{"slug"}`): appends the row's suggested folder to that project's `os_folders[os_name()]`, saves the map, commits `map edit`, broadcasts `map.changed` and `agents.changed`, and re-resolves the row. 409 when the row no longer has that suggestion. Nothing is ever mapped automatically.
- Map editor: two textareas per project, "Folders on Windows" and "Folders on Linux"; the current OS's column is labelled "(this PC)" (from `state.os`). Editing writes `os_folders` and removes `folders`. A path in the wrong column shows the same inline error style as other map errors.
- Agents view: an unmapped row with `suggest` shows one small button, "link to <name>", calling the endpoint.

- [x] **Step 1: Write failing tests.**
  - `test_folders_for_mixes_os_folders_and_legacy`.
  - `test_validate_rejects_wrong_column`.
  - `test_resolve_ignores_other_os_folders`: a Windows folder never matches on Linux and the reverse (posix-only and win32-only halves).
  - `test_suggest_link_by_folder_name`: map project robotarm with `os_folders.windows = ["D:\\RobotArmControl"]`; a Linux cwd `<tmp>/dev/RobotArmControl/src` inside a git repo whose top is `<tmp>/dev/RobotArmControl` → suggestion with that top folder. An ambiguous name → None. A mapped row → None.
  - `test_api_link_adds_folder_and_commits`; `test_api_link_409_without_suggestion`.
  - JS: the map-editor model round-trips `os_folders` and drops `folders`; the agents model shows the link action only when `suggest` is set.
- [x] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run** Python + JS: PASS.
- [x] **Step 5: Commit** `feat: per-OS project folders in the map, one-click link by folder name`.

---

### Task 5: Root identity, mount guard, hub lock, time zone, data-version guard

**Files:**
- Create: `app/brainhub/rootguard.py`, `app/tests/test_rootguard.py`
- Modify: `app/brainhub/server.py`, `app/brainhub/routes.py` (setup writes the marker), `app/brainhub/store.py` (`user_version`, migrations), `app/brainhub/__main__.py`, `app/tests/test_server.py`, `app/tests/test_store.py`

**Interfaces:**
- `rootguard.BRAIN_FORMAT = 1`. Marker file `data/root.json` (P5).
- `@dataclass(frozen=True) class RootStatus: state: str; message: str`, where `state` is one of:
  - `"ok"`: marker present, readable, format understood, root writable;
  - `"fresh"`: root exists, no marker, no `brain/map.json`, and `brain/` absent or empty (other files, like the live copy's `app/`, are allowed). Setup may create a brain here.
  - `"missing"`: the root doesn't exist, or the mount guard fails;
  - `"readonly"`: as ok (or a legacy brain), but the root isn't writable;
  - `"foreign"`: no marker, and `brain/` has content but no `map.json`;
  - `"newer"`: the marker's `brain_format` is greater than `BRAIN_FORMAT`, or the `hub.db` `user_version` is greater than the code's `SCHEMA_VERSION` (checked by the server after opening the store).
- `rootguard.check_root(paths, *, fstab=Path("/etc/fstab"), ismount=os.path.ismount) -> RootStatus`. **It never creates or writes anything**, except the legacy adoption below.
  - **Mount guard (POSIX):** find the longest mount point listed in `fstab` (excluding `/`) that the root is inside. If it exists and `ismount(mp)` is False, the result is `"missing"` with the design's banner text for an unmounted drive.
  - **Writability:** `os.access(root, W_OK)`, plus on POSIX `os.statvfs(root).f_flag & os.ST_RDONLY`.
  - **Legacy adoption:** no marker but `brain/map.json` present (a brain made before markers) and writable → write a marker and return ok.
- `rootguard.create_marker(paths) -> dict`: called only by `POST /api/setup` on a `"fresh"` root (setup refuses otherwise, with 409 and the status message).
- `rootguard.tz_mismatch(marker) -> str | None`: compares `time.timezone`/`time.altzone` with the marker's. It returns a quiet warning ("This OS's time zone differs from the one this brain was created with; entry times would be off by the difference. Set the same time zone on both OSes.") or None.
- **Hub lock:** `rootguard.acquire_lock(paths) -> None | str`. `data/hub.lock` holds `{"os", "boot", "pid", "started"}`:
  - take it over when the os differs, the boot differs, or the pid is dead (POSIX `os.kill(pid, 0)`; Windows `OpenProcess` via ctypes);
  - return a refusal message when it's the same OS, same boot and a live pid;
  - write it atomically; `release_lock(paths)` removes it only if it is still ours (same pid and boot).
  - `run_server` calls `acquire_lock` after the port check (on ok/fresh/readonly roots; read-only skips writing and only checks) and exits with the refusal message if refused. `release_lock` runs on cleanup.
- `create_app`:
  - computes the status first;
  - stops creating `paths.data` unless the status is ok or fresh (fresh creates `data/` inside the existing root, which is allowed);
  - puts `app["root_status"]`;
  - adds a `tz_mismatch` warning to a new `app["warnings"]` list (shown in `/api/state` as `warnings`).
  - Statuses other than ok/fresh/readonly are wired in Task 6. In this task, `missing`/`foreign`/`newer` make `create_app` raise `RootUnavailable(status)`, which `run_server` prints before exiting non-zero. Task 6 replaces that with the reduced app.
- **Store versioning:**
  - `SCHEMA_VERSION = 1` and `MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]]` (empty now; Task 7 adds 2).
  - `init_schema` reads `PRAGMA user_version`: 0 on a db created before versioning → create the schema and set it to `SCHEMA_VERSION`; lower → run migrations in order, then set; higher → raise `SchemaTooNew`.

- [x] **Step 1: Write failing tests** (`test_rootguard.py`, real temp folders, fake fstab text):
  - `test_missing_root_is_never_created`: `create_app` on a non-existent root raises `RootUnavailable`, and the folder still doesn't exist.
  - `test_unmounted_fstab_mount_point_is_missing`: fstab lists `<tmp>/mnt`, `ismount` returns False → missing, even though the folder exists and is empty.
  - `test_fresh_root_allows_setup_and_writes_marker`: root with an `app/` folder and an empty `brain/`; `POST /api/setup` succeeds and `data/root.json` has 32-hex `id` and `brain_format == 1`.
  - `test_setup_refused_on_non_fresh_root`.
  - `test_foreign_root`: `brain/notes.md` without `map.json`.
  - `test_legacy_brain_gets_marker`.
  - `test_newer_brain_format_refused`.
  - `test_schema_too_new_refused`, `test_schema_versioned_from_zero`.
  - `test_readonly_root_detected` (POSIX: `chmod 0o555` on the root; skip when running as root).
  - `test_tz_mismatch_warning` (monkeypatch `time.timezone`).
  - `test_lock_from_other_os_is_taken_over`, `test_lock_from_other_boot_is_taken_over`, `test_lock_dead_pid_is_taken_over`, `test_lock_live_same_boot_refuses` (use the test's own pid), `test_release_only_our_lock`.
  - Existing tests that build apps on the `paths` fixture keep working: the fixture's empty `brain/` and `data/` count as fresh, and tests that save a map directly count as legacy and get a marker. Fix any that break by adjusting fixtures, never by weakening the guard.
- [x] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run** the full suite: PASS.
- [x] **Step 5: Commit** `feat: root guard (marker, mount check, never create a root), hub lock, tz check, schema versioning`.

---

### Task 6: Reduced mode, holding inbox, read-only root, banner, Fast Startup watch

**Files:**
- Create: `app/brainhub/holding.py`, `app/brainhub/winwatch.py`, `app/tests/test_holding.py`, `app/tests/test_reduced.py`, `app/tests/test_winwatch.py`
- Modify: `app/brainhub/rawlog.py` (`append_entry(…, entry_id=None)`), `app/brainhub/server.py`, `app/brainhub/routes.py`, `app/brainhub/store.py` (read-only open), `app/brainhub/__main__.py` (exit code 75), `app/web/js/app.js`, `app/web/js/api.js`, `app/web/js/widget.js`, `app/web/js/widget-model.js` (+ test), `app/web/style.css`, `app/web/widget.css`

**Interfaces:**
- `holding.py`:
  - `hold(state_dir, text, source, now) -> dict`: validates like `append_entry` (blank text and unknown source raise `ValueError`), picks `entry_id = rawlog.new_entry_id(ts)`, and writes `<state>/holding/<entry_id>.json` (`{"id", "ts", "source", "text"}`) via tmp, fsync, `os.replace`, then fsync the folder on POSIX. Returns the record. Only after it returns may the API answer "saved".
  - `held(state_dir) -> list[dict]`: sorted by ts; unreadable files are skipped and reported, never deleted.
  - `flush(state_dir, brain, store) -> list[str]`: for each held record, oldest first:
    - if the store or the raw log already has its id, just delete the held file (a crash between append and delete);
    - else call `rawlog.append_entry(brain, text, source, now=ts, entry_id=id)` and then delete the held file.
    - Returns the ids appended. The server adds them to the store and the filing queue exactly as captures are.
- `rawlog.append_entry(…, entry_id: str | None = None)`: when given, it is used (validated against the id pattern, and its timestamp part must match `ts`).
- **Reduced app** (`server.create_reduced_app(paths, status)`), used for `missing`, `foreign` and `newer`. It serves:
  - `/`, `/widget` and `/static/`;
  - `GET /api/state`: `setup_done` null, `drive: {"state", "message", "held": N}`, plus `warnings` and `os`. The UI checks `drive` before `setup_done`, so the setup wizard is never offered in reduced mode;
  - `POST /api/entries` → holding inbox;
  - `POST /api/hooks/event` and `POST /api/usage` → 204, ignored;
  - `GET /api/widget` → a minimal summary with the banner;
  - everything else under `/api/` → 503 `{"error": <message>, "drive": {...}}`.
  - It never opens `hub.db`, never runs git, and never writes under the root. Middleware is unchanged (`local_only`, JSON).
- **Read-only mode** (`readonly` status): the full app, but:
  - the store opens with `sqlite3.connect("file:<db>?mode=ro&immutable=1", uri=True)`;
  - the startup sync skips every writing step (git lock, git init/config, owner edits, gitattributes, restores, backfill, notes index, filing recovery);
  - workers and the backup task don't start;
  - a middleware answers mutating requests with 503 plus the reason, except `POST /api/entries` (→ holding inbox) and the two hook endpoints (→ 204, ignored);
  - `GET` endpoints work: Today, journal, recall/ask (recall runs `claude` with cwd = the brain, read-only tools).
- **`hub.db` damage handler:** `_open_store` moves the db aside only for real corruption. That means `sqlite3.DatabaseError` whose message contains "malformed", "not a database" or "file is encrypted", or `PRAGMA quick_check` not returning ok. "locked", "readonly", "unable to open" and `OperationalError`s are re-raised: the hub shows a startup error and does not move anything.
- **Recovery (P8):** in reduced or read-only mode, a background task re-runs `check_root` every 15 s; when it returns ok, it sets `app["exit_code"] = 75` and stops the server gracefully. `run_server` exits with `app["exit_code"]` (default 0). Use `AppRunner`/`TCPSite` with a stop event instead of `web.run_app` if needed, keeping SIGINT/SIGTERM handling.
- **Flush:** on an ok start, `flush` runs before `_backfill_log`, so held captures are indexed and queued by the normal path.
- `winwatch.fast_startup_warnings() -> list[str]` (Windows only; `[]` elsewhere): reads `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power` values `HiberbootEnabled` and `HibernateEnabled` via `winreg`, read-only. A value of 1 gives one quiet warning with the fix ("Fast Startup or hibernation is on again; run `powercfg /h off` as admin so Linux can use D: safely."). Errors give `[]`. Added to `app["warnings"]` at start.
- **UI:**
  - `app.js` shows a drive banner from `state.drive`: the message, plus "N captures held on this OS" when N > 0. In reduced mode only the capture box and the banner render; other views show the banner instead of erroring.
  - `warnings` render as a quiet one-line note under the header, never a popup.
  - The widget shows the banner text in its synopsis line, and the capture box keeps working.

- [x] **Step 1: Write failing tests.**
  - `test_holding.py`:
    - `test_hold_is_durable_and_listed`;
    - `test_capture_held_then_flushed_with_original_ts`: held at 2026-10-20 23:59:30, flushed on 2026-10-22; it lands in `log/2026/10/2026-10-20.md` with its own ts and id;
    - `test_flush_is_idempotent_after_crash`: the id is already in the log and the file still held → no duplicate;
    - `test_unreadable_held_file_is_kept`.
  - `test_reduced.py`:
    - `test_missing_root_serves_banner_and_holds_capture`: `POST /api/entries` → 200 and a file in the holding folder; `GET /api/today` → 503 with `drive`; the root is still absent afterwards.
    - `test_hook_events_ignored_in_reduced_mode` (204).
    - `test_readonly_root_keeps_hub_db`: make a brain, then make the root read-only (chmod, POSIX). The app starts, `GET /api/today` works, `POST /api/proposals/confirm` → 503, and `hub.db` was not moved or modified (same mtime and size).
    - `test_locked_db_is_not_moved_aside` (hold an exclusive lock from another connection).
    - `test_recovery_requests_exit_75`: missing root → create it with a marker → after one re-check tick (inject the interval), `app["exit_code"] == 75`.
  - `test_winwatch.py`: monkeypatched reader; `[]` on non-Windows.
  - JS: the widget model's synopsis shows the banner when `drive` is set; the app banner helper formats the held count.
- [x] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run** Python + JS: PASS.
- [x] **Step 5: Manual check** on temp roots: a missing root, a read-only root (`chmod -R a-w`), and recovery (the hub exits 75 once the root appears). Note the results in the decisions doc.
- [x] **Step 6: Commit** `feat: reduced mode with holding inbox, read-only root, safe hub.db handling, Fast Startup watch`.

---

### Task 7: Git hygiene, filing path guard, clean-up at start, process groups

**Files:**
- Modify: `app/brainhub/gitrepo.py`, `app/brainhub/server.py`, `app/brainhub/filing.py`, `app/brainhub/prompts/filing.md`, `app/brainhub/store.py` (migration 2), `app/brainhub/agents.py`, `app/brainhub/claude_runner.py`, `app/brainhub/hook_client.py`, `app/web/js/views/agents.js` (OS in the tooltip)
- Test: `app/tests/test_gitrepo.py`, `app/tests/test_filing.py`, `app/tests/test_server.py`, `app/tests/test_agents.py`, `app/tests/test_claude_runner.py`, `app/tests/test_hook_client.py`

**Interfaces:**
- `BrainRepo`:
  - every git call (`_run`, `push`) passes `-c safe.directory=<self.path resolved>`, with `/` separators on Windows; `init` also sets `core.filemode false`;
  - `clear_stale_locks() -> list[str]` deletes `index.lock`, `HEAD.lock`, `config.lock`, `packed-refs.lock` and `refs/**/*.lock` under `.git`, and returns what it removed. It replaces `_clear_stale_git_lock` at startup (same reporting in `startup_errors`). Only called at startup, before any hub git process runs.
- `filing.invalid_windows_paths(changed: list[str], existing: list[str]) -> list[str]`: for each new or renamed path, report any component that has:
  - one of `<>:"|?*\` or a control character;
  - a trailing dot or space;
  - a reserved device name (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`), with or without an extension, any case;
  - or a path equal, case-insensitively, to a different existing path or another new path.
  - The worker calls it in the worktree **before** the cherry-pick (it lists the worktree commit's added/renamed files and compares them with `git ls-files` of main). A non-empty result fails the filing visibly ("filed paths not valid on Windows: …") into To review, like other filing failures; nothing is cherry-picked.
- `prompts/filing.md`: "Use lowercase, dash-separated file and folder names (a-z, 0-9, -)."
- **Store migration 2** (`SCHEMA_VERSION = 2`): `ALTER TABLE agents ADD COLUMN os TEXT`, `ADD COLUMN boot TEXT`.
- `AgentTracker`:
  - new rows record `os = os_name()` and `boot = boot_id()`;
  - `end_previous_boot() -> int` (called once at startup, ok/readonly-writable only) sets `ended_at = now` on rows not ended whose `boot` differs from the current one (or is NULL). The normal linger then removes them.
  - Public rows include `os`; the Agents tooltip shows "on Windows" / "on Linux".
- **Process groups (POSIX):**
  - `ClaudeRunner._spawn` passes `start_new_session=True`; `_kill` sends `os.killpg(proc.pid, SIGTERM)`, waits up to 2 s, then `SIGKILL`s the group (ignore `ProcessLookupError`). Windows keeps `taskkill /T`.
  - `hook_client._run_passthrough` does the same on POSIX (`start_new_session=True`, `killpg` on timeout); it stays silent and bounded.
- **Worktree prune across OSes:** keep today's behaviour (`requeue_unfinished` removes leftovers under `data/worktrees`), and add a test where the leftover's `.git` file names an absolute path from the other OS.

- [x] **Step 1: Write failing tests.**
  - `test_gitrepo.py`:
    - `test_git_calls_pass_safe_directory` (spy on `subprocess.run` args);
    - `test_init_sets_filemode_false`;
    - `test_clear_stale_locks_removes_ref_locks` (create `.git/refs/heads/main.lock`).
  - `test_filing.py`:
    - `test_filing_rejects_windows_invalid_paths`: FakeRunner's `on_run` writes `projects/a:b.md`, `notes/con.md` and `x/trailing. /f.md` → filing failed, main repo HEAD unchanged, the failure message names the paths;
    - `test_filing_rejects_case_clash`: existing `projects/Robot.md`, new `projects/robot.md` → failed;
    - `test_valid_paths_still_file`.
  - `test_server.py`/`test_agents.py`:
    - `test_ghost_rows_ended_at_start`: a row with `boot="old"`, status running, not ended → after startup, ended;
    - `test_migration_2_adds_columns` (a v1 db file).
  - `test_claude_runner.py` (posix-only): `test_timeout_kills_process_group`. The fake CLI spawns `sleep 30` and writes its pid to a file; after the timeout that pid is gone.
  - `test_hook_client.py` (posix-only): `test_passthrough_child_killed_on_timeout` (same pattern).
- [x] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run** the full suite: PASS.
- [x] **Step 5: Commit** `feat: brain git hygiene for two OSes, Windows-safe filing paths, boot clean-up, process groups`.

---

### Task 8: The desktop shell on Linux

**Prerequisite:** Ruling D1 (Task 2) says which Wayland approach to use.

**Files:**
- Modify: `app/desktop/src-tauri/src/config.rs`, `supervisor.rs`, `lib.rs`, `Cargo.toml` (`libc` for unix signals, if used), `app/brainhub/desktop_config.py`, `app/brainhub/__main__.py`, `app/tests/test_desktop_config.py`
- Create (only if D1 picked option 2): `app/brainhub/kwin_rule.py` (+ test) and a `desktop-config --kwin-rule` step

**Interfaces:**
- `config.rs`:
  - `default_path()` honours `BRAINHUB_CONFIG_DIR` first; on Windows it stays `%LOCALAPPDATA%\BrainHub\desktop.json`; on unix it is `${XDG_CONFIG_HOME:-$HOME/.config}/brainhub/desktop.json`.
  - New `state_dir()`, mirroring P10 (`BRAINHUB_STATE_DIR`, XDG data home, `%LOCALAPPDATA%\BrainHub`). Like `osdirs`, an empty or relative XDG value falls back to the default.
- `supervisor.rs`:
  - `hub.log` moves to `<state>/hub.log` (no longer under the root), so a missing drive can still be diagnosed;
  - stop = SIGTERM (unix, via `libc::kill`), wait up to 5 s with `try_wait`, then `kill()`. Windows keeps `kill()`; the hub's own children are handled by the hub.
  - Exit code 75 (P8): the next start happens at once and doesn't count as an attempt.
- `lib.rs`:
  - `open_browser`: unix `xdg-open <url>` (detached, stdio null); Windows unchanged;
  - apply D1: set `GDK_BACKEND=x11` before Tauri initialises (unless the env already sets `GDK_BACKEND`); build the widget hidden, set its GTK type hint to Utility (keeps it out of Alt+Tab), then show it; if KWin still lists it in Alt+Tab, `desktop-config` installs a KWin rule with `skipswitcher` instead;
  - quit cleanly on SIGTERM/SIGINT (unix): `app.exit(0)`, so the window state is saved and the supervisor stops the hub (D1);
  - autostart via the existing plugin (it writes `~/.config/autostart/` on Linux).
- `desktop_config.py`:
  - `default_target()` uses the same rules (Python `osdirs.config_dir() / "desktop.json"`);
  - `console_python` handles the POSIX venv layout (`bin/python`; there's no pythonw on Linux).
  - The written file is unchanged in shape for now (Task 9 adds runtime mode).
- `CARGO_TARGET_DIR`: the README tells each OS to use a local target dir (`~/.cache/brainhub-target` on Linux, `%LOCALAPPDATA%\brainhub-target` on Windows).

- [x] **Step 1: Write failing tests.**
  - Rust:
    - `config_path_honours_env_override`;
    - `config_path_xdg` (unix);
    - `state_dir_env_override`;
    - `exit_75_restarts_without_backoff` (unix: the stand-in python is `/bin/sh -c 'exit 75'` via a tiny script);
    - `stop_sends_sigterm_first` (unix: the stand-in traps TERM and writes a marker file before exiting).
  - Python: `test_default_target_xdg` (posix), `test_console_python_posix_layout`.
- [x] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run** cargo test + pytest: PASS.
- [x] **Step 5: Manual check on Kubuntu** (temp root, temp state, `BRAINHUB_CONFIG_DIR` temp): build, `desktop-config --target`, then run the shell. It spawns the hub, the widget meets D1's checklist, tray → Open opens the browser, tray → Quit stops the hub (SIGTERM visible in `hub.log`). Don't tick "Start at login" with the real autostart folder in a test run. Clean up the Tauri data folders afterwards.
- [x] **Step 6: Commit** `feat: desktop shell on Linux (XDG paths, xdg-open, SIGTERM stop, Wayland per D1)`.

---

### Task 9: Runtime slots and the CI-gated update step

**Files:**
- Create: `app/brainhub/update_step.py`, `app/brainhub/runtime.py`, `app/tests/test_update_step.py`
- Modify: `app/brainhub/__main__.py` (`runtime init|status`), `app/brainhub/desktop_config.py` (runtime mode), `app/desktop/src-tauri/src/config.rs`, `supervisor.rs` (run the update step before each start), `app/web/js/views/settings.js` (+ `/api/state` field) for "update pending (reason)"

**Interfaces:**
- `update_step.py`: stdlib only, run as a file, `python -I -S update_step.py --live <dir> --state <dir> [--timeout 20] [--budget 75]` (per network call; the whole step, ruling D9). It prints one JSON line `{"pulled", "slot", "commit", "pending": <reason or null>}` and always exits 0, except on a usage error. Writes `<state>/update.json` with the same plus a timestamp, `shell_source` and the `stderr` tail of the last failed call. One step at a time per state folder (`runtime/update.lock`, D9).
  1. **Pull** (P9), only if all hold, otherwise `pending = <reason>`, quietly:
     - the live dir exists and is a git repo;
     - `git status --porcelain --untracked-files=normal` is empty;
     - the branch is `master`;
     - `git fetch origin master` succeeds within the timeout;
     - `HEAD` is an ancestor of `origin/master`;
     - the CI gate is green for `origin/master`'s sha: every check run completed, none failed, both `test (…)` jobs succeeded (D9).
     - Then `git merge --ff-only --no-autostash --no-overwrite-ignore <that sha>` (an ignored file the commit tracks makes it fail, D9). **Never** merge, reset, stash, checkout or clean.
     - The CI gate shells out to `gh api` with the repo slug parsed from `git config --get remote.origin.url` (not `get-url`, D9); an unreadable status means "not green".
  2. **Refresh:** if `current.json`'s commit ≠ the live HEAD (or there is no slot whose `slot.json` names that commit), and the HEAD is on `master` at or behind the local `origin/master` (the provenance guard, D9; `runtime init` refuses otherwise), build the inactive slot:
     - empty its `app/`, then `git archive HEAD app | tar -x` (Python `tarfile` from the archive bytes, no `tar` binary) into the slot;
     - the venv: if the inactive slot's `deps` hash (sha256 of `app/pyproject.toml` + `app/uv.lock` + `app/.python-version` when present) matches, keep its venv; otherwise run `uv sync --frozen --no-install-project` with `UV_PROJECT_ENVIRONMENT=<slot>/venv`, cwd = `<slot>/app`.
     - Only when both succeed, rewrite `current.json` atomically (tmp + replace), then copy `hook_client.py` to `<state>/hook/hook_client.py` (P4).
     - On any failure the old slot stays current and `pending` says why.
  3. Every git call passes `-c safe.directory=<live>`; subprocesses get `GIT_TERMINAL_PROMPT=0` and stdin closed, and each gets at most the budget left; a fast-forward or venv sync that might not fit is put off (D9).
- `runtime.py` + CLI:
  - `python -m brainhub runtime init --live <dir>` (creates the first slot from the live HEAD without the pull part);
  - `python -m brainhub runtime status` (prints `current.json` + `update.json`).
- `desktop.json` runtime mode: `{"port", "root", "live": <live dir>, "state": <state dir>}`. The supervisor, before each spawn:
  - runs `<current slot python> -I -S <current slot app>/brainhub/update_step.py --live … --state …` (timeout 90 s; on timeout or failure it carries on);
  - re-reads `current.json`;
  - spawns `<slot>/venv/<bin|Scripts>/python -m brainhub --root <root> --port <port>` with cwd `<slot>/app`.
  - Legacy mode (`python` + `app_dir`) still works for dev.
- **Shell freshness:** the update step also writes `shell_source` (sha256 of `app/desktop/src-tauri/src/**`, `capabilities/**`, `Cargo.toml`, `Cargo.lock`, `build.rs` and `tauri.conf.json` from the live copy; the algorithm is in `shell_source`'s docstring) to `update.json`. The shell compares it with the hash baked in at build time (`build.rs` env var) and shows "widget update available" in the tray tooltip and Settings. Rebuilding stays a manual, documented command (design §5.3, builder's choice).
- **Settings:** a "Runtime" line shows the current commit, the last check, and "update pending (reason)" when set (via `/api/state.runtime`, read from `<state>/update.json` and `current.json`).

- [x] **Step 1: Write failing tests** (`test_update_step.py`, real git: a temp "origin" bare repo, a "live" clone, a fake `gh` and a fake `uv` as executables on a temp PATH, via the Task 1 fake-CLI helper pattern):
  - `test_fast_forward_when_ci_green`;
  - `test_ci_not_green_is_not_pulled` (pending, failed, and gh missing → all "not green");
  - `test_dirty_live_copy_is_left_alone` (an untracked file → no fetch result applied, the file untouched, `pending == "dirty working tree"`);
  - `test_other_branch_is_left_alone`;
  - `test_diverged_history_is_left_alone`;
  - `test_offline_fetch_failure_is_quiet`;
  - `test_refresh_builds_inactive_slot_and_flips`;
  - `test_failed_venv_sync_keeps_previous_slot` (fake uv exits 1 → `current.json` unchanged);
  - `test_venv_reused_when_deps_unchanged` (fake uv not called);
  - `test_hook_client_copied_after_flip`;
  - `test_never_runs_destructive_git` (spy: a PATH wrapper around git logs subcommands, and none of reset/stash/checkout/clean/merge without `--ff-only` appear);
  - `test_live_missing_is_quiet`.
  - Rust: `runtime_mode_spawn_args` (python path and cwd from a temp `current.json`); `legacy_mode_unchanged`.
- [x] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run** pytest + cargo test: PASS.
- [x] **Step 5: Manual check** with a temp "live" clone of the dev copy and a temp state: `runtime init`, start the shell in runtime mode, make a commit in the temp origin, restart → the update is refused while CI isn't green (fake gh) and taken once green.
- [x] **Step 6: Commit** `feat: runtime slots and a CI-gated, fast-forward-only update step`.

---

### Task 10: Claude Code integration on Linux and with runtime slots

**Files:**
- Modify: `app/brainhub/claude_integration.py`, `app/brainhub/routes.py`, `app/brainhub/__main__.py`, `app/brainhub/agents.py` (`WHERE_BY_ENTRYPOINT` if Linux values differ)
- Test: `app/tests/test_claude_integration.py`, `app/tests/test_api_integration.py`

**Interfaces:**
- `claude_integration.hook_paths() -> tuple[str, str]`: `(python, script)`.
  - In runtime mode (`<state>/runtime/current.json` exists): python = the slot venv's base interpreter (`sys._base_executable` resolved, via `console_python`), script = `<state>/hook/hook_client.py`.
  - Otherwise (dev): today's `sys.executable` + package `hook_client.py`.
  - Install (API and CLI) uses it.
- Status line command on POSIX: built with `shlex.quote` (`sh` runs it); Windows keeps today's form. The passthrough file stays per OS (Task 3).
- `OUR_SUFFIX` matching also recognises `brainhub/hook/hook_client.py`, and `hook/hook_client.py` under a `brainhub`/`BrainHub` state folder, so status/uninstall find hooks written in runtime mode.
- **Linux entrypoints:** run one real interactive check with the owner (a terminal session and a VS Code session on Kubuntu, with the integration installed against a temp `CLAUDE_CONFIG_DIR` copy only if possible; otherwise during rollout). Record the raw `CLAUDE_CODE_ENTRYPOINT` values in the decisions doc, and extend `WHERE_BY_ENTRYPOINT` if they differ.

- [x] **Step 1: Write failing tests:**
  - `test_runtime_mode_hooks_use_stable_paths`;
  - `test_posix_statusline_quoting` (a path with a space and a `'`);
  - `test_uninstall_on_one_os_keeps_other_statusline` (two state folders, two settings files, one shared root);
  - `test_status_recognises_runtime_hook_path`.
- [x] **Step 2: Run:** FAIL. **Step 3: Implement.** **Step 4: Run:** PASS.
- [x] **Step 5: Real e2e on Kubuntu** against a temp `CLAUDE_CONFIG_DIR` (never the real one), mirroring the phase 2 "Measured" e2e: a `claude -p` run with the hook command forcing `CLAUDE_CODE_ENTRYPOINT=cli` → a row appears in Agents; the status line POSTs usage. Record the timings in `app/README.md` → Measured.
- [x] **Step 6: Commit** `feat: Claude Code integration on Linux; stable hook paths across runtime slots`.

---

### Task 11: Claude Code parity (`claude-sync`)

**Step 0 is a spike that gates the rest** (design §7.1). It needs one reboot into Windows, so schedule it with the owner.

**Files:**
- Create: `app/brainhub/claude_sync.py`, `app/tests/test_claude_sync.py`
- Modify: `app/brainhub/__main__.py` (`claude-sync`), `app/desktop/src-tauri/src/lib.rs` (run it at login before the hub starts; watch `settings.json` while running), `app/web/js/views/settings.js` (the per-OS key list and quiet notes), `docs/superpowers/dual-boot-decisions.md`

**Spike (each OS, in a scratch project and a scratch shared folder, never the real memory):**
1. Linked memory folder: a symlink (Kubuntu) or junction (Windows) at `~/.claude/projects/<scratch-key>/memory` → `<scratch-shared>/memory/projects/scratch`. In a real session in the scratch project (`claude` interactive, or `claude -p` with a prompt asking it to save a memory note), check that the note lands in the shared folder and is read back in a new session.
2. If 1 fails: per-project `autoMemoryDirectory` in the scratch project's `.claude/settings.local.json`, plus an equivalent for a home session.
3. If both fail: mirroring.

Record the winner (Ruling D2), with the exact key encoding observed (the `<key>` for `/home/tudor/dev/X` and for `D:\X`).

**Interfaces (written for option 1; for 2/3 only the "link" step changes). Ruling D2 picked option 2 on Linux: step 2 "Link" becomes "point": write `autoMemoryDirectory` into `~/.claude/settings.local.json` (home) and each paired project's `.claude/settings.local.json` (merge, never other keys); the merge and backup of a real local memory folder stay the same, and the local folder is left in place (not replaced by a link) after its notes are merged:**
- Shared store: `$BRAINHUB_SHARED_DIR`, else `/mnt/data/ClaudeShared` (Linux) or `D:\ClaudeShared` (Windows). Layout as design §7.2.
- `python -m brainhub claude-sync [--shared <dir>] [--claude-dir <dir>] [--dry-run]`: stdlib only, no hub dependency. It does nothing if the shared dir isn't available (exits 0 with a one-line log). Steps:
  1. **Pairs:** home key ↔ `memory/home`; project keys ↔ `memory/projects/<name>`. The name is the folder's last component; Windows keys that differ only by drive-letter case are one project; `pairs.json` lists the exceptions.
  2. **Link:** for each paired key with notes on either side, ensure `~/.claude/projects/<key>/memory` is a link to the shared folder. If it is a real folder: merge (copy what the shared copy lacks; on a name clash with different content keep both, renaming the local one `<stem>.<os>.md`; merge `MEMORY.md` line by line, dropping duplicates), back the local folder up to `<state>/claude-sync-backups/<key>-<timestamp>/`, then replace it with the link. **Never delete anything else.**
  3. **Header line:** keep exactly one line at the top of each shared `MEMORY.md`: "Shared by Windows and Kubuntu. Label any note that is only true on one OS: (Windows only) / (Linux only)." Re-add it if missing; never duplicate it.
  4. **Skills:** each folder in `~/.claude/skills/` that is not a link and not plugin- or sync-managed (`synced`, and anything listed in `~/.claude/plugins`) is moved to `shared/skills/<name>` and linked back. Each shared skill missing locally is linked in.
  5. **Settings:** per key, both ways. Shared keys = everything except the per-OS list (`hooks`, `statusLine`, `permissions`, `env`, `defaultShell`, `apiKeyHelper`, plus any key whose value contains an absolute path, plus keys the owner adds in BrainHub Settings, stored in `settings.shared.json` as `per_os_keys`). `settings.shared.json` holds `{key: {"value", "changed_at", "deleted": bool}}`. The last-sync snapshot per OS lives in `<state>/claude-sync-last.json`. A local change since the snapshot is recorded first; then the store is applied (later change wins per key; removals propagate). `settings.json` is backed up, then replaced atomically.
  6. **Backup:** the shared dir is a git repo (`* -text`, `core.autocrlf=false`, `core.filemode=false`, `safe.directory`). Commit the changes ("claude-sync <os> <timestamp>") and push non-interactively to `origin` if one is set (the owner creates the private repo). Quiet on failure.
  - Problems go to `<state>/claude-sync.log`, and a summary to `<state>/claude-sync.json`, which BrainHub Settings shows as a quiet note.
- The shell runs `claude-sync` once at startup (before the update step, with the current slot's python; skipped quietly if absent). While running, it re-runs the settings part when `~/.claude/settings.json` changes (poll the mtime every 5 s; a notify crate is not needed).
- **One-time labelling (design §7.5)** is a rollout step done with the owner, not code.

- [ ] **Step 1 (spike):** Linux half now; Windows half at the scheduled reboot. Write Ruling D2. (Linux half done, ruling D2; the Windows half is still open.) **If nothing works on one OS: stop and bring it to the owner.**
- [x] **Step 2: Write failing tests** (`test_claude_sync.py`, temp claude dir, temp shared dir, posix symlinks; Windows junction cases run in CI):
  - `test_links_home_and_paired_projects`;
  - `test_local_folder_backed_up_before_link`;
  - `test_merge_keeps_both_on_conflict`;
  - `test_memory_index_merged_without_duplicates`;
  - `test_header_line_kept_once`;
  - `test_drive_letter_case_keys_pair`;
  - `test_pairs_json_exception`;
  - `test_skills_moved_and_linked_except_managed`;
  - `test_shared_key_change_flows_out` (OS A sets `model`, OS B picks it up);
  - `test_later_change_wins`;
  - `test_removal_propagates` (a plugin removed from `enabledPlugins`);
  - `test_per_os_keys_never_shared` (`hooks`, `permissions` and a key holding `/home/…` never leave);
  - `test_settings_written_atomically_with_backup`;
  - `test_missing_shared_dir_changes_nothing`;
  - `test_never_deletes_notes` (run twice with clashes, then count the files);
  - `test_commit_and_quiet_push_failure`.
- [x] **Step 3: Run:** FAIL. **Step 4: Implement.** **Step 5: Run:** PASS.
- [x] **Step 6: Commit** `feat: claude-sync (shared Claude Code memory, skills and settings across OSes)`.

---

### Task 12: Docs

**Files:** `app/CLAUDE.md`, `app/README.md`, `app/desktop/README.md`, `README.md`, `NEXT-SESSION.md`, `docs/superpowers/dual-boot-decisions.md`

- [x] `app/CLAUDE.md`: no longer "on Windows". Add the live/dev/runtime model, the per-OS state folder, the root guard and reduced mode, the portability rules, and the Linux run/test commands next to the Windows ones. Rule 9 names both real-root paths; rule 12 names both OSes' real settings and the state/config folders.
- [x] `app/README.md`: setup on each OS (uv, the runtime init, desktop-config, the integration, claude-sync); the drive banner and the holding inbox; the verification drills (design §10); Measured on Linux.
- [x] `app/desktop/README.md`: Linux prerequisites, `CARGO_TARGET_DIR`, the Wayland ruling, autostart on KDE.
- [x] Replace `NEXT-SESSION.md` with a short pointer: where things stand, and where the rules and memory now live (shared store). It stops being the only carrier between OSes.
- [x] `dual-boot-decisions.md`: every ruling (D1, D2, …) and the known residuals.
- [x] **Commit** `docs: dual boot (both OSes, runtime model, guards, claude-sync)`, then ask the owner whether to push and open the PR.

---

## Rollout (with the owner; design §9 steps 3–5)

Not code. Each step is confirmed with the owner as it happens.

1. **Owner merges** the `dual-boot` PR once CI is green on both OSes.
2. **Kubuntu:**
   - switch the live copy `/mnt/data/BrainHub` from the local `dual-boot` branch to master (`git switch master && git pull --ff-only`). The design commit on D:'s local `dual-boot` branch is already in master; delete that branch only with the owner's OK;
   - add the repo-local `core.filemode=false` on the live copy;
   - `runtime init`; build the shell (local `CARGO_TARGET_DIR`); `desktop-config` in runtime mode;
   - install the integration, then one interactive terminal session and one VS Code session: record their `CLAUDE_CODE_ENTRYPOINT` (row tooltip), check that Where is right, that the status line shows, and that `/api/usage` updates (the status line doesn't run under `claude -p`, D10); `gh auth setup-git` for the brain push; check the time zone;
   - remove stray `~/.claude/projects/*-data-worktrees-e-*` folders left by earlier filing runs (D11), on both OSes;
   - run `claude-sync` (it merges the Kubuntu home memory into the shared store), then label one-OS notes with the owner;
   - start the shell and run the **setup wizard** on `/mnt/data/BrainHub`, with both OS columns. The first real capture lands on the shared drive.
3. **Windows:** install uv and gh (`gh auth login`); `runtime init`; build the shell; `desktop-config`; install the integration; `claude-sync` (merges the Windows home memory and the 10 project memories; label one-OS notes); re-check Fast Startup. The wizard doesn't run again.
4. **Verification:** design §10 on both OSes (round trip, per-OS checks, drills on scratch roots, parity, updates). Results go into `app/README.md` → Measured and the decisions doc.

## Not in this plan (by design)
- A KDE plasmoid, WSL/VMs/the workbench server, live sync between running OSes, carrying uncommitted dev work across a reboot (design §2 non-goals).
- Automatic shell rebuilds or CI-shipped binaries (manual rebuild with a notice; design §5.3 leaves it open).
- Phases 3–5 features. Their per-OS requirements (one fixed tablet address, tokens in shared `data/`) are noted in design §8.3 for those phases.
