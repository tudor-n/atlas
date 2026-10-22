# BrainHub Desktop

A thin Tauri v2 shell around the hub. It shows the hub's `/widget` page in a small frameless, transparent, always-on-top window. It owns the tray icon and the "Start at login" entry, and it starts and restarts the hub for you. All the widget's content comes from the hub; the shell only frames it.

## Prerequisites (Windows)
- **Rust** via [rustup](https://rustup.rs) (the MSVC toolchain, the default on Windows). `cargo` lives in `%USERPROFILE%\.cargo\bin`; add it to `PATH` if your shell doesn't have it.
- **Visual Studio 2022 C++ build tools** ("Desktop development with C++": MSVC and a Windows SDK).
- **WebView2 runtime.** It ships with Windows 11; on Windows 10 install the Evergreen runtime from Microsoft.
- The hub itself installed as in `app/README.md` (`.venv` with `pip install -e .`).

Node is not needed to build. It was used once, with `npx @tauri-apps/cli@2 init`, to generate `src-tauri/icons/`.

## Build
From `D:\BrainHub\app\desktop\src-tauri`:
```
cargo build --release
```
The first build takes about 5 minutes. The result is `target\release\brainhub-desktop.exe` (about 11 MB, no installer). `cargo test` runs the unit tests (backoff, config parsing, port check, supervisor).

## Configure: `desktop-config`
From `D:\BrainHub\app`:
```
.venv\Scripts\python -m brainhub desktop-config --root D:\BrainHub
```
This writes `%LOCALAPPDATA%\BrainHub\desktop.json` and prints its path:
```json
{"port": 8090, "root": "D:\\BrainHub", "python": "D:\\BrainHub\\app\\.venv\\Scripts\\python.exe", "app_dir": "D:\\BrainHub\\app"}
```
- `port` is the hub's `port` setting. The shell passes it to the hub it starts (`--port`) and reads the file only when it starts, so after you change the port in Settings: re-run the command, then **restart BrainHub Desktop** (tray → Quit, then start it again).
- `python` is the console `python.exe` that ran the command (if you run it with `pythonw.exe`, the `python.exe` beside it). The shell starts it without a window, which gives the hub a hidden console; the git, claude and taskkill processes the hub runs share that console, so none of them flashes a window. (With `pythonw.exe`, which has no console at all, each of them would open a visible one.)
- `--target PATH` writes the file elsewhere (used for tests).

Without the file, the shell assumes port 8090 and only connects: it never starts a hub.

## Run
Start `brainhub-desktop.exe`. The widget first shows "Starting BrainHub…" and switches to the hub's widget once the port answers.

- **Hub supervisor.** Every 2 s the shell checks for a TCP connection on `127.0.0.1:<port>`. If nothing answers and `desktop.json` says how, it starts `python -m brainhub --root <root> --port <port>` in `app_dir`, with `CREATE_NO_WINDOW` (a hidden console). If that hub exits, it is started again after 1, 2, 4 … up to 60 s; the delay resets after 5 minutes of uptime. A hub that is already running (say, one you started by hand) is used as it is and never stopped.
- **Window.** Drag the bar to move it; the position is remembered. When it is built and whenever it resizes (expand, a popup), it is moved back inside the screen's work area if it sticks out, so expanding never grows it off the bottom of the screen. Alt+F4 only hides it.
- **The widget page** is loaded once, when the hub first answers. If the hub restarts later, the page is not reloaded: it shows `BrainHub offline · retrying` and reconnects by itself, so text you were typing stays. (It also keeps an unsaved capture as a draft in its local storage.)
- **Tray:** Open BrainHub (the full app in your browser), Show/Hide widget, Start at login, Restart hub (only for a hub the shell started), Quit (stops the hub the shell started, then exits).
- Launching the exe a second time shows the widget of the running one.

## Start at login
Tick **Start at login** in the tray menu. It adds `brainhub-desktop.exe` to `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`; untick it to remove the entry. It is off until you tick it. If you move or rebuild the exe to another path, untick and tick it again.

## Logs
- The hub's output (stdout and stderr) is appended to `<root>\data\hub.log`. The shell adds its own lines there too, prefixed `[brainhub-desktop t=<unix seconds>]`: when it starts the hub, when the hub exits, and the next retry delay.
- Window position: `%APPDATA%\dev.brainhub.desktop\.window-state.json`. WebView2 data (including the widget's capture draft): `%LOCALAPPDATA%\dev.brainhub.desktop\`. Both are the real folders even when `LOCALAPPDATA` is overridden: after a manual test run, delete them if they weren't there before.

## Troubleshooting
- **Stuck on "Starting BrainHub…".** Look at the end of `<root>\data\hub.log`. "could not start the hub" means the `python` path in `desktop.json` is wrong: re-run `desktop-config`. A Python traceback means the hub failed at startup. No log at all means `desktop.json` is missing, so the shell only waits for a hub on port 8090.
- **The widget shows the wrong hub, or none.** The port in `desktop.json` must match the hub's `port` setting. Re-run `desktop-config` after changing it, then restart BrainHub Desktop (it reads the file only at start).
- **A console window flashes when the hub commits or files.** `python` in `desktop.json` is probably `pythonw.exe` (from an older `desktop-config`): re-run `desktop-config` and restart the shell.
- **A second hub won't start.** `port N on 127.0.0.1 is already in use` in its output means a hub (or something else) already listens there; the second one stops before doing anything.
- **Restart hub is greyed out.** The running hub was not started by the shell (for example, you started it in a terminal). Restart it there.
- **The window can't be resized or dragged.** Check `src-tauri/capabilities/widget.json`: the hub page needs `core:window:allow-set-size` and `core:window:allow-start-dragging` for `http://127.0.0.1:*/*`.
- **The build fails on linking.** The VS C++ build tools or Windows SDK are missing; install them with the Visual Studio Installer.
