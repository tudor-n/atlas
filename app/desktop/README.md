# BrainHub Desktop

A thin Tauri v2 shell around the hub. It shows the hub's `/widget` page in a small frameless, transparent, always-on-top window. It owns the tray icon and the "Start at login" entry, and it starts and restarts the hub for you. All the widget's content comes from the hub; the shell only frames it. The same code builds on Windows (WebView2) and on Kubuntu/Linux (WebKitGTK).

## Prerequisites (Windows)
- **Rust** via [rustup](https://rustup.rs) (the MSVC toolchain, the default on Windows). `cargo` lives in `%USERPROFILE%\.cargo\bin`; add it to `PATH` if your shell doesn't have it.
- **Visual Studio 2022 C++ build tools** ("Desktop development with C++": MSVC and a Windows SDK).
- **WebView2 runtime.** It ships with Windows 11; on Windows 10 install the Evergreen runtime from Microsoft.
- The hub itself installed as in `app/README.md`.

## Prerequisites (Linux)
- **Rust** via [rustup](https://rustup.rs). `cargo` lives in `~/.cargo/bin`.
- **The Tauri libraries**, once:
  ```
  sudo apt install libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev
  ```
- The hub itself installed as in `app/README.md` (`uv sync` in `app/`, which makes `app/.venv`).

Node is not needed to build. It was used once, with `npx @tauri-apps/cli@2 init`, to generate `src-tauri/icons/`.

## Build
Build into a target folder on the local disk, never into `src-tauri/target`: on the dual boot the repo lives on the shared drive `D:`, and each OS keeps its own build output.

Windows, from `D:\BrainHub\app\desktop\src-tauri`:
```
set CARGO_TARGET_DIR=%LOCALAPPDATA%\brainhub-target
cargo build --release
```
Linux, from `app/desktop/src-tauri`:
```
CARGO_TARGET_DIR=~/.cache/brainhub-target cargo build --release
```
The first build takes about 5 minutes. The result is `<target dir>/release/brainhub-desktop.exe` on Windows (about 11 MB) and `<target dir>/release/brainhub-desktop` on Linux, with no installer. `cargo test` (same `CARGO_TARGET_DIR`) runs the unit tests (backoff, config and folder rules, port check, supervisor).

## Configure: `desktop-config`
From `app/` (Windows: `.venv\Scripts\python`, Linux: `.venv/bin/python` or `uv run python`):
```
python -m brainhub desktop-config --root <data root>
```
This writes `desktop.json` to the shell's config folder and prints its path:
- Windows: `%LOCALAPPDATA%\BrainHub\desktop.json`;
- Linux: `${XDG_CONFIG_HOME:-~/.config}/brainhub/desktop.json`;
- `BRAINHUB_CONFIG_DIR`, if set, replaces the folder on both OSes (tests and manual checks use it; the shell reads it too).

```json
{"port": 8090, "root": "D:\\BrainHub", "python": "D:\\BrainHub\\app\\.venv\\Scripts\\python.exe", "app_dir": "D:\\BrainHub\\app"}
```
- `port` is the hub's `port` setting. The shell passes it to the hub it starts (`--port`) and reads the file only when it starts, so after you change the port in Settings: re-run the command, then **restart BrainHub Desktop** (tray → Quit, then start it again).
- `python` is the console python that ran the command. On Windows that is `python.exe` (if you run it with `pythonw.exe`, the `python.exe` beside it). The shell starts it without a window, which gives the hub a hidden console; the git, claude and taskkill processes the hub runs share that console, so none of them flashes a window. (With `pythonw.exe`, which has no console at all, each of them would open a visible one.) On Linux it is the venv's `bin/python`, kept as it is (it's a symlink to the base interpreter, which would run without the venv).
- `--target PATH` writes the file elsewhere (used for tests).

Without the file, the shell assumes port 8090 and only connects: it never starts a hub.

## Runtime mode
The setup above (legacy mode) runs the hub straight from a checkout, which is right for development. On the dual boot each OS instead runs the hub from one of two **runtime slots** in its state folder (`runtime/slot-a`, `runtime/slot-b`: an exact copy of a live-copy commit's `app/` plus its own venv), and an update step keeps them in step with the live copy (`/mnt/data/BrainHub` = `D:\BrainHub`) before every hub start.

1. **Build the first slot**, once per OS (from `app/`; `BRAINHUB_STATE_DIR` moves the state folder, as everywhere):
   ```
   python -m brainhub runtime init --live <live copy>
   ```
   It builds only a live HEAD on branch `master` that the local `origin/master` has (at it or behind it), and says why not otherwise. The first venv sync may download Python and takes a few minutes. `python -m brainhub runtime status` shows the current slot (`runtime/current.json`) and what the last update step did (`update.json`).
2. **Point the shell at it:**
   ```
   python -m brainhub desktop-config --runtime --live <live copy> --root <data root>
   ```
   `desktop.json` is then `{"port", "root", "live", "state"}` (the state folder as resolved when you ran it). Restart the shell after re-running it.
3. **What the shell does.** At start it writes `shell.json` (its own source digest, below) to the state folder. Then, whenever it is about to start the hub:
   - once per shell start: `<slot python> -m brainhub claude-sync` in the slot's `app/` (shared Claude Code memory and settings). It gets 60 s, its output goes to `hub.log`, and a failure doesn't stop the hub;
   - the update step: `<slot python> -I -S <slot>/app/brainhub/update_step.py --live <live> --state <state>`. It fast-forwards the live copy (only a clean checkout of `master`, only to a commit CI passed on both OSes, never anything but `--ff-only`), builds the other slot when the live HEAD moved, and flips `current.json` once that slot is complete. It keeps to a 75 s budget; the shell kills it, with its process tree, after 90 s. Its one JSON line (`{"pulled", "slot", "commit", "pending"}`) goes to `hub.log`; why an update is held back ("CI pending", "dirty working tree", …) also shows in Settings;
   - then the hub, from the slot `current.json` names now: `<state>/runtime/slot-<x>/venv/bin/python -m brainhub --root <root> --port <port>` (`venv\Scripts\python.exe` on Windows), in `<state>/runtime/slot-<x>/app`.
   - All of it runs off the tray's thread: the tray menu keeps working meanwhile, and the widget shows "Starting BrainHub…" until the hub answers. A hub that exits with code 75 is started again at once, after another update step. Quit during the update step lets the step finish on its own (its budget bounds it).
   - **No slot yet** (`runtime init` never ran): the shell starts nothing and only connects to the port; `hub.log` and the tray say so. It notices a slot made later without a restart.
4. **Widget update available.** The shell can't update itself. Its build bakes in a digest of its own source (`build.rs`: `src/`, `capabilities/`, `Cargo.toml`, `Cargo.lock`, `build.rs`, `tauri.conf.json`), and each update step records the same digest of the live copy's `app/desktop/src-tauri`. When they differ, the tray says "widget update available (rebuild: see app/desktop/README.md)" (its tooltip on Windows; the first line of its menu on Linux, whose tray has no tooltip), and Settings' Runtime line says "widget update available". To update: build again from the **live copy** as in [Build](#build) (`<live copy>/app/desktop/src-tauri`, with the local `CARGO_TARGET_DIR`), then tray → Quit and start the shell again. Built from another checkout, the digest only matches when that checkout's shell source is the same.

## Run
Start `brainhub-desktop` (`.exe` on Windows). The widget first shows "Starting BrainHub…" and switches to the hub's widget once the port answers.

- **Hub supervisor.** Every 2 s the shell checks for a TCP connection on `127.0.0.1:<port>`. If nothing answers and `desktop.json` says how, it starts `python -m brainhub --root <root> --port <port>` in `app_dir` (on Windows with `CREATE_NO_WINDOW`, a hidden console); in runtime mode it starts it from the current slot, after the update step (see [Runtime mode](#runtime-mode)). If that hub exits, it is started again after 1, 2, 4 … up to 60 s; the delay resets after 5 minutes of uptime. A hub that exits with code 75 (its data folder became usable again after reduced mode) is started again at once, and that doesn't count as a failed attempt. A hub that is already running (say, one you started by hand) is used as it is and never stopped.
- **Stopping the hub** (tray → Quit, Restart hub, or the shell ending): on Linux the shell sends SIGTERM, so the hub shuts down cleanly, and kills it only if it is still running 10 s later. On Windows it kills it; the hub cleans up after its own children.
- **Window.** Drag the bar to move it; the position is remembered. When it is built and whenever it resizes (expand, a popup), it is moved back inside the screen's work area if it sticks out, so expanding never grows it off the bottom of the screen. Alt+F4 only hides it.
- **The widget page** is loaded once, when the hub first answers. If the hub restarts later, the page is not reloaded: it shows `BrainHub offline · retrying` and reconnects by itself, so text you were typing stays. (It also keeps an unsaved capture as a draft in its local storage.)
- **Tray:** Open BrainHub (the full app in your browser: `cmd /C start` on Windows, `xdg-open` on Linux), Show/Hide widget, Start at login, Restart hub (only for a hub the shell started), Quit (stops the hub the shell started, then exits).
- Launching the shell a second time shows the widget of the running one.

### On Linux (KDE Plasma on Wayland)
- **XWayland (ruling D1).** On Wayland an app can't keep itself above other windows, stay out of the taskbar or choose its position, so the shell runs itself under XWayland: it sets `GDK_BACKEND=x11` for itself before GTK starts, unless `GDK_BACKEND` is already set. The hub and the browser it opens don't inherit it.
- **Out of Alt+Tab.** The widget is a GTK "utility" window, which KWin leaves out of Alt+Tab, the taskbar and the pager. KWin still reports `skipSwitcher` as false for it: its Alt+Tab list only takes normal windows and dialogs, so the window type alone keeps it out.
- **Logout and Ctrl+C.** SIGTERM (sent at logout) and SIGINT (Ctrl+C in a terminal) quit the shell as tray → Quit does: the window position is saved and the hub is stopped. A second signal ends it at once.
- **Blank window?** Start it with `WEBKIT_DISABLE_DMABUF_RENDERER=1` (a WebKitGTK problem with some GPU drivers; not needed on the iGPU).
- The terminal shows `libayatana-appindicator is deprecated`; that warning is harmless.

## Start at login
Tick **Start at login** in the tray menu; it is off until you tick it. Untick it to remove the entry. If you move or rebuild the binary to another path, untick and tick it again.
- Windows: it adds the exe to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
- Linux: it writes `~/.config/autostart/BrainHub Desktop.desktop` (shown in System Settings → Autostart on KDE), which starts the binary at its build path. It needs no extra environment: the shell sets `GDK_BACKEND` itself.

## Logs
- The hub's output (stdout and stderr) is appended to `hub.log` in the per-OS state folder: `%LOCALAPPDATA%\BrainHub\hub.log` on Windows, `${XDG_DATA_HOME:-~/.local/share}/brainhub/hub.log` on Linux (`BRAINHUB_STATE_DIR` replaces the folder). It is never under the data root, so a missing drive can still be diagnosed and the shell never creates anything there. The shell adds its own lines too, prefixed `[brainhub-desktop t=<unix seconds>]`: when it starts the hub, when the hub exits, the next retry delay, and how it stopped the hub (`SIGTERM, then exit status: 0`). In runtime mode also claude-sync and the update step: the command, their own output (the step's JSON line), and how each ended.
- Window position: `%APPDATA%\dev.brainhub.desktop\.window-state.json` on Windows, `~/.config/dev.brainhub.desktop/.window-state.json` on Linux. Web view data (including the widget's capture draft): `%LOCALAPPDATA%\dev.brainhub.desktop\` on Windows, `~/.local/share/dev.brainhub.desktop/` on Linux (the tray icon also writes `$XDG_RUNTIME_DIR/tray-icon/`). These are the real folders even when the env overrides are set: after a manual test run, delete them if they weren't there before.

## Troubleshooting
- **Stuck on "Starting BrainHub…".** Look at the end of `hub.log` in the state folder. "could not start the hub" means the `python` path in `desktop.json` is wrong: re-run `desktop-config`. A Python traceback means the hub failed at startup. No log at all means `desktop.json` is missing, so the shell only waits for a hub on port 8090.
- **The widget shows the wrong hub, or none.** The port in `desktop.json` must match the hub's `port` setting. Re-run `desktop-config` after changing it, then restart BrainHub Desktop (it reads the file only at start).
- **A console window flashes when the hub commits or files (Windows).** `python` in `desktop.json` is probably `pythonw.exe` (from an older `desktop-config`): re-run `desktop-config` and restart the shell.
- **A second hub won't start.** `port N on 127.0.0.1 is already in use` in its output means a hub (or something else) already listens there; the second one stops before doing anything.
- **Restart hub is greyed out.** The running hub was not started by the shell (for example, you started it in a terminal). Restart it there.
- **The window can't be resized or dragged.** Check `src-tauri/capabilities/widget.json`: the hub page needs `core:window:allow-set-size` and `core:window:allow-start-dragging` for `http://127.0.0.1:*/*`.
- **The widget is behind other windows or in the taskbar (Linux).** Something set `GDK_BACKEND=wayland` for the shell; start it without it.
- **The build fails on linking.** Windows: the VS C++ build tools or Windows SDK are missing; install them with the Visual Studio Installer. Linux: one of the apt packages above is missing.
