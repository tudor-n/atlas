# The rename: BrainHub becomes ATLAS

Owner, 2026-10-24: "let's also rebrand the app as ATLAS, i'll change the repo name too. change the name in all the docs, the app everything."

The owner's choices:
- **Depth: everything, plus migration.** Every visible name and the code inside it are renamed: the package, the commands, the env vars, the app id and the per-OS folders. On its first start, ATLAS moves the old settings over and repairs the Claude Code hooks by itself.
- **The data folder is renamed too:** `/mnt/data/BrainHub` becomes `/mnt/data/ATLAS`, and `D:\BrainHub` becomes `D:\ATLAS`. The owner renames the folder, because nothing automated ever writes under the real root (rule 9). ATLAS then follows the new path.

This spec covers the rename itself plus the cut-over: what the code does on the first start, and what the owner does by hand. It sits on top of phase 4.5 (PR #7).

## 1. Names

| What | Was | Now |
|---|---|---|
| Brand, as shown | BrainHub / BRAINHUB | **ATLAS**. Top bar, window title, tray tooltip and menu ("Open ATLAS"), widget, tablet, launcher, "Starting ATLAS…", and the messages in prose. |
| Spoken by the voice | "Brain Hub" | "Atlas". The TTS spells all-caps words letter by letter, so `voice_lines.spoken_project` turns `ATLAS` into `Atlas`. |
| Python package, project, CLI | `brainhub`, `python -m brainhub …` | `atlas` (`app/atlas/`), `python -m atlas …`, `prog="atlas …"` |
| Rust crate, lib, binary, WM_CLASS | `brainhub-desktop`, `brainhub_desktop_lib` | `atlas-desktop`, `atlas_desktop_lib` |
| Tauri `productName` / `identifier` | `BrainHub Desktop` / `dev.brainhub.desktop` | `ATLAS` / `dev.atlas.desktop` |
| Launcher entry and icon | `dev.brainhub.desktop.desktop` / `.png` | `dev.atlas.desktop.desktop` / `.png` |
| Agents' measurement command | `brainhub-request` | `atlas-request` |
| Env vars | `BRAINHUB_*` (STATE_DIR, CONFIG_DIR, SHARED_DIR, BIN_DIR, SHELL_SOURCE, VOICE_FAKE, VOICE_FAKE_DELAY, VOICE_TEST_MODELS, VOICE_TRACE) | `ATLAS_*`, same suffixes |
| State folder | `~/.local/share/brainhub`, `%LOCALAPPDATA%\BrainHub` | `~/.local/share/atlas`, `%LOCALAPPDATA%\ATLAS` |
| Config folder | `~/.config/brainhub`, `%LOCALAPPDATA%\BrainHub` | `~/.config/atlas`, `%LOCALAPPDATA%\ATLAS` |
| Shared root | `/mnt/data/BrainHub` = `D:\BrainHub` | `/mnt/data/ATLAS` = `D:\ATLAS` |
| Cargo target | `~/.cache/brainhub-target`, `%LOCALAPPDATA%\brainhub-target` | `~/.cache/atlas-target`, `%LOCALAPPDATA%\atlas-target` |
| Hub git identity | `BrainHub <brainhub@localhost>` | `ATLAS <atlas@localhost>` |
| CLAUDE.md block and wrapper marks | `<!-- brainhub: … -->` … `<!-- /brainhub -->`, "written by `brainhub integration install`" | `<!-- atlas: … -->` … `<!-- /atlas -->`, "written by `atlas integration install`" |
| Web `localStorage` keys | `brainhub.browse`, `brainhub.setup.v1`, `brainhub.widget.draft` | `atlas.*`. The two drafts read the old key once when the new one is absent. |
| Docs | `*-brainhub-*.md`, "BrainHub", "brain-hub#N" | `*-atlas-*.md`, "ATLAS", "#N" (PR numbers survive a repo rename) |

**Unchanged on purpose:**
- **The tablet's `bh_device` cookie and its `bh.*` `localStorage` keys** (`bh.queue`, `bh.draft.*`, `bh.tab`). Renaming them would unpair the tablet and drop its unsent notes. Nobody ever sees them.
- **The common noun "the hub"**, meaning the backend process; "hub.db", `data/` and `brain/`.
- **CI job names** `test (ubuntu-latest)` / `test (windows-latest)` (rule 15).
- **The dev copy's folder** `~/dev/BrainHub`. Claude Code keys its memory and history by that path. The owner may rename it later; nothing depends on it.

**The old names survive only in four places:**
- the migration code and its tests;
- the recognition of our own old hook, status-line, wrapper and CLAUDE.md entries, so they can be found and replaced;
- the shell's legacy-slot fallback (§2.3);
- the decisions and history lines that say what was renamed.

## 2. The cut-over, done by the code

### 2.1 The folders: the desktop shell, first thing at start (`migrate.rs`, beside `config.rs`)
The migration runs only when all three hold:
- no `ATLAS_CONFIG_DIR` / `ATLAS_STATE_DIR` override is set;
- the new `desktop.json` is absent;
- the legacy `desktop.json` (`${XDG_CONFIG_HOME:-~/.config}/brainhub/desktop.json`, `%LOCALAPPDATA%\BrainHub\desktop.json`) exists.

It waits, for this start, while the pre-rename app still runs: when the legacy `desktop.json`'s port answers, or the legacy update step holds `runtime/update.lock`. It logs "Quit BrainHub first" and runs on the legacy `desktop.json` as before.

When it runs, the shell:
1. Renames the legacy state folder to the new one, then the legacy config folder to the new one (state first, so the new `desktop.json` existing always means "done"). On Windows these are one folder. Each rename is a single rename, and only when the target doesn't exist or is empty.
   - Right after the state move it leaves a **compat path** at the old state folder, so the absolute paths in the old hooks, wrapper and status line passthrough keep working until the hub repairs them: on Linux a symlink to the new folder; on Windows the old folder again with copies of `hook/hook_client.py`, `hook/request_client.py` and `statusline_passthrough.txt`, plus a marker file. Neither holds a `desktop.json`, so the migration never fires again through it, and a later start counts it as already moved.
2. Rewrites the moved `desktop.json`:
   - `state` gets the new state folder when it named the legacy one;
   - each of `root`, `live` and (legacy mode) `app_dir` is swapped when it is missing on disk and a sibling path exists with `BrainHub` replaced by `ATLAS` (one path component). That's the owner's renamed folder.
   - If the owner hasn't renamed the folder yet, the old path stays and still works.
   - The sibling swap (and the state swap) also runs on **every** start without an override, against whichever `desktop.json` is in use (atomically, only when something changes; logged to `hub.log`), and in memory when it can't be written. So a root renamed, or a drive mounted, only after the first start is followed at the next one.
3. Writes `<state>/migrated.json` (`{"from": "brainhub", "at", "moved": [...], "repointed": [...]}`) and logs one line to `hub.log`.
4. Autostart: if the legacy login entry is there (Linux `~/.config/autostart/BrainHub Desktop.desktop`; Windows the HKCU Run value `BrainHub Desktop`), the shell removes it and turns its own autostart on.

**If any step fails:** the shell logs the error and runs on the legacy `desktop.json` where it is. It never half-moves: a failed second rename leaves the first one in place, and the paths it writes match what is on disk. Every step is idempotent, so a later start finishes the job.

The old web view folders (`~/.local/share/dev.brainhub.desktop`, `~/.config/dev.brainhub.desktop`) are not moved; the owner deletes them (`TODO.md`). The window position gets remembered again.

### 2.2 The Claude Code hooks: the hub, once per start (`claude_integration.repair_legacy`)
This runs only in **the owner's real hub**: a runtime hub the shell started (it sets `ATLAS_RUNTIME_HUB=1` for those only), whose package runs from `<state>/runtime/slot-a|b/app/` of its own state folder, with that folder's `runtime/current.json` and stable hook copy in place (`runs_from_own_slot`). A dev or manual hub never touches Claude Code's settings by itself, whatever its `ATLAS_STATE_DIR`. It runs in every mode: full, reduced and read-only (then without the shared `data/` folder for adoption). It acts only when **our own old entries** are in the target settings: hooks or a status line whose script path ends in `brainhub/hook/hook_client.py` or `brainhub/hook_client.py` (the legacy suffixes, as whole path components). In that case the hub:
- reinstalls the integration at the current paths, with the same python, port and passthrough, through the existing `install` code (a backup first, an atomic write);
- removes a `brainhub-request` wrapper that carries the legacy mark, and installs `atlas-request`;
- replaces a legacy `<!-- brainhub: … -->` CLAUDE.md block with the new block, where it was.

Afterwards, once the settings no longer name the old state folder and no marked old wrapper is left, the hub removes the shell's compat path (§2.1), only while it is still exactly our symlink to the state folder in use, or our marked copy holding nothing else (`remove_legacy_compat`).

Anything that isn't recognisably ours is left alone. A failure is quietly logged to `hub.log`; Settings → Claude Code already shows "out of date" with its install button. It runs once per start and is a no-op when nothing legacy is found.

`install`, `uninstall` and `status` also recognise the legacy suffixes as ours from now on, so nothing is ever duplicated.

### 2.3 The first update after the rename: the shell's legacy-slot fallback (`supervisor.rs`)
At the first start, the current runtime slot still holds a pre-rename commit (`app/brainhub/`). For a slot, the shell picks the package by what the slot's `app/` contains: `atlas/` if it's there, else `brainhub/`. That choice covers `update_step.py`, `-m <pkg>` for the hub and claude-sync. Children get both `ATLAS_STATE_DIR` and `BRAINHUB_STATE_DIR`.

So the old update step runs once, builds the new slot from the live HEAD and flips. Its hook copy then fails, because it looks in `app/brainhub/`, and it notes this as pending. The new hub starts with `-m atlas`, and the next start's new update step copies the hook.

The new `update_step.install_hook` looks in `app/atlas/`, then in `app/brainhub/` for a legacy slot.

## 3. The cut-over, done by the owner (`TODO.md`)
1. Merge #6, #7, then this PR. Rename the GitHub repo.
2. Quit the widget from the tray, if it's running.
3. `mv /mnt/data/BrainHub /mnt/data/ATLAS`.
4. Point the live copy at the renamed repo, then fast-forward it:
   - `git -C /mnt/data/ATLAS remote set-url origin https://github.com/tudor-n/<new name>.git`
   - `git -C /mnt/data/ATLAS pull --ff-only`
   - Do the same `set-url` for the dev copy.
5. Build the shell: `cd /mnt/data/ATLAS/app/desktop/src-tauri && CARGO_TARGET_DIR=~/.cache/atlas-target cargo build --release --locked`.
6. Start the new shell once from a terminal (`~/.cache/atlas-target/release/atlas-desktop`). It moves the folders, builds the new runtime slot and repairs the hooks. Settings may say "update pending: hook client copy failed" this once.
7. Install the launcher with the new slot's python, from the slot's `app/` (`desktop-entry install --exec ~/.cache/atlas-target/release/atlas-desktop`; exact line in `TODO.md`). It also removes the old BrainHub entry and icon. Quit, then start ATLAS from the launcher. Check:
   - Settings → Claude Code says installed;
   - a Claude Code session shows up in Agents;
   - `atlas-request` works.
8. Optional:
   - rename the "BrainHub" project in Settings → Map;
   - delete `~/.cache/brainhub-target`, `~/.local/share/dev.brainhub.desktop` and `~/.config/dev.brainhub.desktop`.
9. Windows (on hold): the same rename on `D:\`, then build the shell there. The migration covers `%LOCALAPPDATA%\BrainHub`.

## 4. Tests
- **Everything renamed passes as before:** pytest, node and cargo.
- **New tests:**
  - **the shell migration (Rust, on temp env dirs):**
    - it moves the folders and rewrites `desktop.json`;
    - root sibling swap;
    - no override, no run;
    - an existing target isn't clobbered;
    - a failed move leaves the legacy config usable;
    - it's idempotent;
    - the legacy autostart path.
  - **package choice by slot contents.**
  - **`repair_legacy`:**
    - old hooks and status line are replaced in place;
    - the old wrapper and the old CLAUDE.md block are replaced;
    - nothing legacy means no write;
    - someone else's `hook_client.py` is left alone.
  - **`install_hook`'s legacy fallback.**
  - **`spoken_project("ATLAS") == "Atlas"`.**
  - **the legacy-key reads for the two drafts.**
- **A guard test:**
  - It fails on any `brainhub` / `BrainHub` / `BRAINHUB` / `brain-hub` in tracked files outside an allow-list: the migration and legacy-recognition code and its tests, the decisions doc's rename section, this spec and the dev-copy path `~/dev/BrainHub`.
  - It also fails on any tracked path containing `brainhub`.
