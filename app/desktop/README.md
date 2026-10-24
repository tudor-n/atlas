# ATLAS: the desktop shell

A thin Tauri v2 shell around the hub. It shows the hub's `/widget` page in a small frameless, transparent, always-on-top window, and (phase 4.5) the full app in its own **main window**: a normal, resizable window with a taskbar entry and a launcher icon. It owns the tray icon and the "Start at login" entry, and it starts and restarts the hub for you. All the content comes from the hub; the shell only frames it. The same code builds on Windows (WebView2) and on Kubuntu/Linux (WebKitGTK).

## Prerequisites (Windows)
- **Rust** via [rustup](https://rustup.rs) (the MSVC toolchain, the default on Windows). `cargo` lives in `%USERPROFILE%\.cargo\bin`; add it to `PATH` if your shell doesn't have it.
- **Visual Studio 2022 C++ build tools** ("Desktop development with C++": MSVC and a Windows SDK).
- **WebView2 runtime.** It ships with Windows 11; on Windows 10 install the Evergreen runtime from Microsoft.
- The hub set up as in `app/README.md` → Setup on each OS (uv, a dev copy's venv, `runtime init`).

## Prerequisites (Linux)
- **Rust** via [rustup](https://rustup.rs). `cargo` lives in `~/.cargo/bin`.
- **The Tauri libraries**, once:
  ```
  sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev
  ```
- The hub set up as in `app/README.md` → Setup on each OS (uv, a dev copy's venv, `runtime init`).

Node is not needed to build. It was used once, with `npx @tauri-apps/cli@2 init`, to generate `src-tauri/icons/`.

## Build
Build into a target folder on the local disk, never into `src-tauri/target`: on the dual boot the repo lives on the shared drive `D:`, and each OS keeps its own build output.

Windows, from `D:\ATLAS\app\desktop\src-tauri`:
```
set CARGO_TARGET_DIR=%LOCALAPPDATA%\atlas-target
cargo build --release
```
Linux, from `app/desktop/src-tauri`:
```
CARGO_TARGET_DIR=~/.cache/atlas-target cargo build --release
```
On the dual boot you build from the live copy (`<live copy>/app/desktop/src-tauri`): add `--locked` there (see [Runtime mode](#runtime-mode), step 4). The first build takes about 5 minutes. The result is `<target dir>/release/atlas-desktop.exe` on Windows (about 11 MB) and `<target dir>/release/atlas-desktop` on Linux, with no installer. `cargo test` (same `CARGO_TARGET_DIR`) runs the unit tests (backoff, config and folder rules, port check, supervisor).

## Configure: `desktop-config`
From `app/` in a dev copy, with `uv run --no-sync python` (or the venv's python: `.venv\Scripts\python` on Windows, `.venv/bin/python` on Linux):
```
python -m atlas desktop-config --root <data root>
```
This writes `desktop.json` to the shell's config folder and prints its path:
- Windows: `%LOCALAPPDATA%\ATLAS\desktop.json`;
- Linux: `${XDG_CONFIG_HOME:-~/.config}/atlas/desktop.json`;
- `ATLAS_CONFIG_DIR`, if set, replaces the folder on both OSes (tests and manual checks use it; the shell reads it too).

```json
{"port": 8090, "root": "/tmp/bh-test", "python": "/home/tudor/dev/BrainHub/app/.venv/bin/python", "app_dir": "/home/tudor/dev/BrainHub/app"}
```
- `port` is the hub's `port` setting. The shell passes it to the hub it starts (`--port`) and reads the file only when it starts, so after you change the port in Settings: re-run the command, then **restart ATLAS** (tray → Quit, then start it again).
- `python` is the console python that ran the command. On Windows that is `python.exe` (if you run it with `pythonw.exe`, the `python.exe` beside it). The shell starts it without a window, which gives the hub a hidden console; the git, claude and taskkill processes the hub runs share that console, so none of them flashes a window. (With `pythonw.exe`, which has no console at all, each of them would open a visible one.) On Linux it is the venv's `bin/python`, kept as it is (it's a symlink to the base interpreter, which would run without the venv).
- `--target PATH` writes the file elsewhere (used for tests).

Without the file, the shell assumes port 8090 and only connects: it never starts a hub.

## Runtime mode
The setup above (legacy mode) runs the hub straight from a checkout, which is right for development. On the dual boot each OS instead runs the hub from one of two **runtime slots** in its state folder (`runtime/slot-a`, `runtime/slot-b`: an exact copy of a live-copy commit's `app/` plus its own venv), and an update step keeps them in step with the live copy (`/mnt/data/ATLAS` = `D:\ATLAS`) before every hub start.

1. **Build the first slot**, once per OS (from a dev copy's `app/`, as in `app/README.md` → Setup on each OS; `ATLAS_STATE_DIR` moves the state folder, as everywhere):
   ```
   python -m atlas runtime init --live <live copy>
   ```
   It builds only a live HEAD on branch `master` that the local `origin/master` has (at it or behind it), and says why not otherwise. The first venv sync may download Python and takes a few minutes. `python -m atlas runtime status` shows the current slot (`runtime/current.json`) and what the last update step did (`update.json`).
2. **Point the shell at it:**
   ```
   python -m atlas desktop-config --runtime --live <live copy> --root <data root>
   ```
   `desktop.json` is then `{"port", "root", "live", "state"}` (the state folder as resolved when you ran it). Restart the shell after re-running it.
3. **What the shell does.** At start (once it knows it is the only instance) it writes `shell.json` (its own source digest, below) to the state folder. Then, whenever it is about to start the hub:
   - once per shell start, before the first hub it starts: `<slot python> -m atlas claude-sync` in the slot's `app/` (shared Claude Code memory and settings). It gets 60 s, its output goes to `hub.log`, and a failure doesn't stop the hub. A shell that only connects to a hub already running skips it (the watch below still runs);
   - the update step: `<slot python> -I -S <slot>/app/atlas/update_step.py --live <live> --state <state>`. It fast-forwards the live copy (only a clean checkout of `master`, only to a commit CI passed on both OSes, never anything but `--ff-only`), builds the other slot when the live HEAD moved, and flips `current.json` once that slot is complete. It keeps to a 75 s budget; after 90 s the shell kills it with its process tree (Linux: SIGTERM, on which the step kills the git or uv it is running and exits, then SIGKILL 3 s later for anything left; Windows: `taskkill /T /F`). Its one JSON line (`{"pulled", "slot", "commit", "pending"}`) goes to `hub.log`; why an update is held back ("CI pending", "dirty working tree", …) also shows in Settings;
   - then the hub, from the slot `current.json` names now: `<state>/runtime/slot-<x>/venv/bin/python -m atlas --root <root> --port <port>` (`venv\Scripts\python.exe` on Windows), in `<state>/runtime/slot-<x>/app`.
   - The hub, the update step and claude-sync all get `ATLAS_STATE_DIR=<state>` (`desktop.json`'s), and `hub.log` is in that folder too, even when the shell's own default state folder (its env, or the XDG default) is another one: the logs, the slots and what Settings shows never split.
   - All of it runs off the tray's thread: the tray menu keeps working meanwhile, and the widget (and the main window) show "STARTING THE HUB…" until the hub answers. A hub that exits with code 75 is started again at once, after another update step. **Restart hub** stops the hub and the next start runs the update step again (a way to take an update now). **Quit** during the update step (or a claude-sync) lets it finish on its own: its budget bounds it, and until then it holds `runtime/update.lock`, so a shell started again at once logs "update already running" and starts the hub from the current slot.
   - **No slot yet** (`runtime init` never ran): the shell starts nothing and only connects to the port; `hub.log` and the tray say so. It notices a slot made later without a restart.
   - **While it runs**, the shell keeps Claude Code's settings and memory in step: it reads the modification time of Claude Code's `settings.json` every 5 s (`$CLAUDE_CONFIG_DIR/settings.json` if set, else `~/.claude/settings.json`, `%USERPROFILE%\.claude\settings.json` on Windows) and on a change runs `claude-sync --settings-only`; every 10 minutes it runs a full `claude-sync`, so memory notes written during sessions are committed and pushed in batches. One at a time (the run at start included), off the tray's thread, with the same 60 s limit; claude-sync's own write to `settings.json` doesn't count as a change. Each run adds about three lines to `hub.log`.
4. **Widget update available.** The shell can't update itself while it runs; started from the launcher entry (`desktop-entry install --live`, below) it is rebuilt before it starts. Its build bakes in a digest of its own source (`build.rs`: `src/`, `capabilities/`, `Cargo.toml`, `Cargo.lock`, `build.rs`, `tauri.conf.json`, and the starting pages in `../dist/`), and each update step records the same digest of the live copy's `app/desktop/src-tauri`. When they differ, the tray says "widget update available (rebuild: see app/desktop/README.md)", in its tooltip and as the first line of its menu (on both OSes; Linux's tray shows no tooltip), and Settings' Runtime line says "widget update available". To update, build again from the **live copy**, in `<live copy>/app/desktop/src-tauri` with the local `CARGO_TARGET_DIR` as in [Build](#build), but with `--locked`:
   ```
   cargo build --release --locked
   ```
   Without `--locked`, cargo may rewrite `Cargo.lock` in the live copy, which then counts as dirty and holds back every later update ("dirty working tree"). Then tray → Quit and start the shell again; the notice goes away once the new shell runs. Built from another checkout, the digest only matches when that checkout's shell source is the same.

## Run
Start `atlas-desktop` (`.exe` on Windows). The widget first shows "STARTING THE HUB…" and switches to the hub's widget once the port answers. Started with `--open` (the launcher entry does that), it opens the main window too.

- **Hub supervisor.** Every 2 s the shell checks for a TCP connection on `127.0.0.1:<port>`. If nothing answers and `desktop.json` says how, it starts `python -m atlas --root <root> --port <port>` in `app_dir` (on Windows with `CREATE_NO_WINDOW`, a hidden console); in runtime mode it starts it from the current slot, after the update step (see [Runtime mode](#runtime-mode)). If that hub exits, it is started again after 1, 2, 4 … up to 60 s; the delay resets after 5 minutes of uptime. A hub that exits with code 75 (its data folder became usable again after reduced mode) is started again at once, and that doesn't count as a failed attempt. A hub that is already running (say, one you started by hand) is used as it is and never stopped.
- **Stopping the hub** (tray → Quit, Restart hub, or the shell ending): on Linux the shell sends SIGTERM, so the hub shuts down cleanly, and kills it only if it is still running 10 s later. On Windows it kills it; the hub cleans up after its own children.
- **Widget window.** Drag the bar to move it; the position is remembered (only the position: its size follows the page). When it is built and whenever it resizes (expand, a popup), it is moved back inside the screen's work area if it sticks out, so expanding never grows it off the bottom of the screen. Alt+F4 only hides it.
- **The widget page** is loaded once, when the hub first answers. If the hub restarts later, the page is not reloaded: it shows `ATLAS offline · retrying` and reconnects by itself, so text you were typing stays. (It also keeps an unsaved capture as a draft in its local storage.)
- **Main window** (label `main`, phase 4.5). Built the first time you open it: 1440×900 (shrunk to fit a smaller screen's work area on its first open, before it is shown), at least 900×600, black from the first frame. It shows "STARTING THE HUB…" until the hub answers, then the hub's app at `http://127.0.0.1:<port>/`; like the widget, it is never reloaded after that once it has loaded. A load that fails (the hub was down or restarting, and the web view shows its "could not connect" page) is tried again once the hub answers, for the widget too. Its position, size and maximised state are remembered. Closing it only hides it (the page, its WebSocket and any draft stay; reopening is instant); Quit is in the tray. It has no IPC capability: the hub page in it can't call the shell.
- **Opening it at a view.** The widget's tiles call the shell's one command, `open_main`, which only the widget may call (`allow-open-main` in `capabilities/widget.json`): `window.__TAURI__.core.invoke('open_main', {view})`, where `view` is a hash route such as `agents` or `settings/tablet` (`[a-z]+(/[a-z0-9-]+)?`; anything else just opens the window). On the hub page already, it changes `location.hash` without a reload; otherwise it goes there with that view once the hub answers.
- **Tray:** Open ATLAS (the main window), Open in browser (the full app in your browser: `cmd /C start` on Windows, `xdg-open` on Linux), Show/Hide widget, Pause voice, Start at login, Restart hub (only for a hub the shell started), Quit (stops the hub the shell started, then exits).
- Launching the shell a second time shows the widget of the running one; with `--open` (or `--open=<view>`) it shows the running one's main window instead.

### Launcher entry (Linux)
From `app/` in a dev copy (or with a slot's python), point it at the binary you built:
```
python -m atlas desktop-entry install --exec ~/.cache/atlas-target/release/atlas-desktop
```
It writes `${XDG_DATA_HOME:-~/.local/share}/applications/dev.atlas.desktop.desktop` (`Exec="<binary>" --open`, `StartupWMClass=atlas-desktop`, `Icon=dev.atlas.desktop`) and copies `src-tauri/icons/128x128@2x.png` to `…/icons/hicolor/256x256/apps/dev.atlas.desktop.png`. Running it again changes nothing unless the binary moved. `desktop-entry status [--exec <binary>]` prints what is there as JSON (`current`: the entry matches that binary); `desktop-entry uninstall` removes just those two files. `--data-home <dir>` writes elsewhere (tests use a temp folder). Autostart still starts the shell without `--open`, so login shows only the widget. On Windows it says "not on Windows yet".

**With `--live <live copy>`** (what the owner's install uses), the entry starts the live copy's launcher instead of the binary: `Exec="/usr/bin/python3" -I -S "<live>/app/atlas/launcher.py" --exe "<binary>" --open`. The launcher (`atlas/launcher.py`, stdlib only) is what makes the shell update itself:
1. if a shell already runs, it hands `--open` to it at once and does nothing else;
2. otherwise (runtime mode only) it runs the update step exactly as the shell does, so master is pulled before the shell starts (CI-green commits only, as always);
3. when the live copy's HEAD isn't the commit the shell was last built from (`<state>/launcher.json`), it runs `cargo build --release --locked` in `<live>/app/desktop/src-tauri` into the binary's own target folder, with a desktop notification ("Updating ATLAS…"). Cargo redoes only what changed: a commit that doesn't touch the shell costs a few seconds, a shell change about a minute (icons included: `build.rs` reruns on `icons/`);
4. then it execs the shell. A failed build keeps the old binary (and says so in a notification); the next launch tries again.
One launcher at a time (`<state>/launcher.lock`); everything goes to `<state>/launcher.log`. So: merge on GitHub, quit ATLAS from the tray, start it from the launcher, and it runs the new master, shell and all. While it runs, it only gets the hub's part of an update (tray → Restart hub); the shell's part waits for the next start, and the tray says "widget update available" meanwhile.

### On Linux (KDE Plasma on Wayland)
- **XWayland (ruling D1).** On Wayland an app can't keep itself above other windows, stay out of the taskbar or choose its position, so the shell runs itself under XWayland: it sets `GDK_BACKEND=x11` for itself before GTK starts, unless `GDK_BACKEND` is already set. The hub and the browser it opens don't inherit it.
- **Out of Alt+Tab.** The widget is a GTK "utility" window, which KWin leaves out of Alt+Tab, the taskbar and the pager. KWin still reports `skipSwitcher` as false for it: its Alt+Tab list only takes normal windows and dialogs, so the window type alone keeps it out.
- **Logout and Ctrl+C.** SIGTERM (sent at logout) and SIGINT (Ctrl+C in a terminal) quit the shell as tray → Quit does: the window position is saved and the hub is stopped. A second signal ends it at once.
- **Blank window?** Start it with `WEBKIT_DISABLE_DMABUF_RENDERER=1` (a WebKitGTK problem with some GPU drivers; not needed on the iGPU).
- The terminal shows `libayatana-appindicator is deprecated`; that warning is harmless.

## The first start after the rename
The app has a new name (was BrainHub, and the shell "BrainHub Desktop"). Before it reads `desktop.json`, the shell moves the pre-rename folders over by itself (`src-tauri/src/migrate.rs`; rename spec §2.1):
- It runs only when no `ATLAS_CONFIG_DIR` or `ATLAS_STATE_DIR` override is set, the new `desktop.json` is missing and the old one exists (pre-rename: `~/.config/brainhub/desktop.json`, `%LOCALAPPDATA%\BrainHub\desktop.json`).
- It waits while the old app still runs (its port answers, or its update step holds `runtime/update.lock`): the log says "Quit" the old app first, and this start runs on the old `desktop.json`.
- It renames the old state folder to the new one, then (Linux) the old config folder (pre-rename: `~/.local/share/brainhub`, `~/.config/brainhub`); each only onto a missing or empty target.
- At the old state folder it leaves a compat path, so the old hooks keep working until the hub repairs them: a symlink on Linux, a marked copy of the hook files on Windows. The hub removes it once nothing names it.
- It rewrites `desktop.json`: `state` follows the move, and `root`, `live` and `app_dir` follow your renamed data folder (`/mnt/data/ATLAS`) when the old path is gone and the renamed one exists. That last part runs on every start, so renaming the folder (or mounting the drive) after the first start is fine too.
- It records what it did in `<state>/migrated.json` and one `hub.log` line. If anything fails it logs why and runs on the old `desktop.json` where it is; the next start finishes the job.
- An old login entry (pre-rename: `~/.config/autostart/BrainHub Desktop.desktop`, the HKCU Run value `BrainHub Desktop`) is removed, and the shell turns its own **Start at login** on instead.
- At that first start the current runtime slot still holds a pre-rename commit: the shell runs that slot's own package for the update step and claude-sync (it picks the package by what the slot's `app/` holds), and gives every child the state folder under the old variable name too. The update step then builds a new slot, and the hub starts from it.

## Start at login
Tick **Start at login** in the tray menu; it is off until you tick it. Untick it to remove the entry. If you move or rebuild the binary to another path, untick and tick it again.
- Windows: it adds the exe to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
- Linux: it writes `~/.config/autostart/ATLAS.desktop` (shown in System Settings → Autostart on KDE), which starts the binary at its build path. It needs no extra environment: the shell sets `GDK_BACKEND` itself.

## Logs
- The hub's output (stdout and stderr) is appended to `hub.log` in the per-OS state folder: `%LOCALAPPDATA%\ATLAS\hub.log` on Windows, `${XDG_DATA_HOME:-~/.local/share}/atlas/hub.log` on Linux (`ATLAS_STATE_DIR` replaces the folder; in runtime mode it is `desktop.json`'s `state`). It is never under the data root, so a missing drive can still be diagnosed and the shell never creates anything there. The shell adds its own lines too, prefixed `[atlas-desktop t=<unix seconds>]`: when it starts the hub, when the hub exits, the next retry delay, and how it stopped the hub (`SIGTERM, then exit status: 0`). In runtime mode also claude-sync (`claude-sync (at start)`, `(settings changed)`, `(every 10 minutes)`) and the update step: the command, their own output (the step's JSON line), and how each ended.
- Window state (the widget's position; the main window's position, size and maximised state): `%APPDATA%\dev.atlas.desktop\.window-state.json` on Windows, `~/.config/dev.atlas.desktop/.window-state.json` on Linux. Web view data (including the widget's capture draft): `%LOCALAPPDATA%\dev.atlas.desktop\` on Windows, `~/.local/share/dev.atlas.desktop/` on Linux (the tray icon also writes `$XDG_RUNTIME_DIR/tray-icon/`). These are the real folders even when the env overrides are set: after a manual test run, delete them if they weren't there before.

## Troubleshooting
- **Stuck on "STARTING THE HUB…".** Look at the end of `hub.log` in the state folder. "could not start the hub" means the `python` path in `desktop.json` is wrong: re-run `desktop-config`. A Python traceback means the hub failed at startup. No log at all means `desktop.json` is missing, so the shell only waits for a hub on port 8090.
- **The widget shows the wrong hub, or none.** The port in `desktop.json` must match the hub's `port` setting. Re-run `desktop-config` after changing it, then restart ATLAS (it reads the file only at start).
- **A console window flashes when the hub commits or files (Windows).** `python` in `desktop.json` is probably `pythonw.exe` (from an older `desktop-config`): re-run `desktop-config` and restart the shell.
- **A second hub won't start.** `port N on 127.0.0.1 is already in use` in its output means a hub (or something else) already listens there; the second one stops before doing anything.
- **Restart hub is greyed out.** The running hub was not started by the shell (for example, you started it in a terminal). Restart it there.
- **The window can't be resized or dragged.** Check `src-tauri/capabilities/widget.json`: the hub page needs `core:window:allow-set-size` and `core:window:allow-start-dragging` for `http://127.0.0.1:*/*` (and `allow-open-main` for its tiles to open the main window). The build generates `allow-open-main` from the app manifest in `build.rs` into `src-tauri/permissions/autogenerated/` (git-ignored).
- **The launcher icon or the taskbar entry doesn't group with the window (Linux).** `xprop WM_CLASS`, then click the main window: the entry's `StartupWMClass` must match one of the two names (the binary's name, `atlas-desktop`, by default).
- **The widget is behind other windows or in the taskbar (Linux).** Something set `GDK_BACKEND=wayland` for the shell; start it without it.
- **The build fails on linking.** Windows: the VS C++ build tools or Windows SDK are missing; install them with the Visual Studio Installer. Linux: one of the apt packages above is missing.
