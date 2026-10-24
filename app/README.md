# ATLAS (phases 1–4.5, on Kubuntu and Windows)

ATLAS is a local second brain. You type free text; a headless Claude Code agent files it into a git-versioned Markdown `brain/` and *proposes* tasks, which you confirm. Recall is instant local search plus a cited answer.

- **Capture:** raw entries go into an append-only log first, then the agent files them into notes and proposes items (next-time, deadline, reminder, waiting, to-read, task, new people/projects).
- **Review:** nothing becomes an item until you confirm it. Today, project, people and deadline views show the confirmed items.
- **Recall:** search hits appear at once; Claude answers from them with citations, or says `Nothing on that.`
- **Phase 2:** a usage meter (your Claude 5-hour and weekly limits), an Agents panel (your Claude Code sessions on this PC), a desktop widget, and a desktop shell with a tray icon, start at login and hub self-healing. See [Phase 2](#phase-2-usage-meter-agents-panel-widget-desktop-shell).
- **Dual boot:** the laptop runs Kubuntu and Windows, one at a time. Both run the same master commit against one shared root on `D:` (`D:\ATLAS` = `/mnt/data/ATLAS`), with the same features. A missing or read-only drive never starts an empty brain and never loses a capture ([Drive banner](#drive-banner-holding-inbox-and-recovery)). Claude Code memory, skills and settings are shared too ([claude-sync](#claude-code-sync-claude-sync)).

**The rename:** the app has a new name (was BrainHub until 2026-10-24; rename spec: `docs/superpowers/specs/2026-10-24-atlas-rename-design.md`). Its first start after the update moves the pre-rename state and config folders over (pre-rename: `~/.local/share/brainhub`, `~/.config/brainhub`, `%LOCALAPPDATA%\BrainHub`), follows your renamed data folder, and the hub replaces its own old Claude Code hooks, status line, `atlas-request` wrapper and `CLAUDE.md` line. Your steps are in `TODO.md` step 8; the shell's part is in [`desktop/README.md`](desktop/README.md#the-first-start-after-the-rename).

## Requirements
- Kubuntu 26.04 or Windows 11, with git, [uv](https://docs.astral.sh/uv/) (it installs the Python in `.python-version`, 3.14; the code needs >= 3.12) and `gh`, logged in (the update step reads CI results through it).
- The Claude Code CLI (`claude`) installed and **logged in** with your subscription. `claude_path` is a bare command name; each OS finds it on its own `PATH`, then in `~/.local/bin`.
- Only to build the desktop shell: Rust and the platform's build prerequisites, see [`desktop/README.md`](desktop/README.md).
- Only for the JS tests: Node 24 (via nvm on Kubuntu).

## Where data lives
**Shared: the root** (`D:\ATLAS` = `/mnt/data/ATLAS`). Everything here is the same on both OSes and holds no absolute paths or per-OS state.
- `app/` (with the rest of the repo): the **live copy** of the code. Master only, never edited by hand; each OS fast-forwards it before starting the hub ([Updates](#updates)).
- `brain/`: your Markdown notes, its own git repo. `log/` holds the verbatim raw entries, `_items/` the confirmed items (`items.json`) and the proposals still waiting for you (`proposals.json`), `map.json` the map (always LF). Every filing, confirmation and item edit is a commit (a filing is `file <entry id>: <synopsis>`); your own edits are committed as they are ("owner edits"). The agent never modifies `log/`, `_items/`, `map.json` or any dot-file.
- `data/hub.db`: the search index and filing state. It is a rebuildable index: if it is damaged, the next start moves it aside and rebuilds it from `brain/` (entries filed before are recognised from their `file <entry id>:` commits and are not filed again; pending proposals come back from `_items/proposals.json`; if more than 10 entries turn out never to have been filed, they wait in To review for you to press Retry instead of all being filed at once). Only real damage moves it: a locked or unopenable `hub.db` stops the start with a message and nothing is moved. Phase 2 adds its `agents` table (Agents panel rows, which expire on their own; each row records its OS).
- `data/settings.json`: your preferences and the backup URL. Keep it; it is not in `brain/`.
- `data/usage.json`: the last known usage figures (same account on both OSes).
- `data/root.json`: the root's marker (a random id, the brain format, the time zone it was made in), written by setup. `data/hub.lock`: which hub runs on this root.
- `data/worktrees/`: temporary agent checkouts, removed after each filing (a leftover from the other OS is removed at the next start).
- `data/voice/`: the voice's model and voices files (phase 4), downloaded once from Settings → Voice, about 121 MB (354 MB with the higher-quality model). Both OSes use the same copy.
- `brain/_voice/quotes.md`: the voice's quips, yours to edit (phase 4). The filing agent can't touch it, and Ask doesn't search it.

`brain/` and `data/` are git-ignored by the code repo.

**Per OS: the state folder**: `${XDG_DATA_HOME:-~/.local/share}/atlas/` on Kubuntu, `%LOCALAPPDATA%\ATLAS\` on Windows (`ATLAS_STATE_DIR` replaces it). It is on the local disk, so it works without `D:`.
- `claude_integration.json` and `statusline_passthrough.txt`: written while the Claude Code integration is installed on this OS; they remember the status line you had before, if any.
- `holding/`: captures held while the root wasn't writable ([Drive banner](#drive-banner-holding-inbox-and-recovery)).
- `hub.log`: output of a hub started by the desktop shell, plus the shell's own lines.
- `runtime/` (the two runtime slots and `current.json`), `update.json` (the last update step), `shell.json` (the running shell's source digest), `hook/hook_client.py` (the stable copy the hooks run).
- `claude-sync.json`, `claude-sync.log`, `claude-sync-last.json` and `claude-sync-backups/` ([claude-sync](#claude-code-sync-claude-sync)).
- `voice.json` (the reminder tick's last time, a pause, where each quote pool is up to), `voice-worker.log` (the voice worker's errors) and `voice/` (the clip being played) (phase 4).

**Per OS: the launcher** (phase 4.5, written by `desktop-entry install`): `~/.local/share/applications/dev.atlas.desktop.desktop` and its icon in `~/.local/share/icons/hicolor/256x256/apps/`.

**Per OS: the config folder**: `${XDG_CONFIG_HOME:-~/.config}/atlas/desktop.json` on Kubuntu, `%LOCALAPPDATA%\ATLAS\desktop.json` on Windows (`ATLAS_CONFIG_DIR` replaces the folder). Also per OS: the shell's window position and web view data (see `desktop/README.md`), and its start-at-login entry.

**Shared: the Claude store**: `D:\ClaudeShared` = `/mnt/data/ClaudeShared` (`ATLAS_SHARED_DIR` replaces it): Claude Code memory, skills and portable settings, its own git repo.

## Setup on each OS
Do Kubuntu first, then Windows. The setup wizard runs once, on the first OS; the second opens the same brain.

Run these commands from a **dev copy**, never from the live copy: a venv never lives on `D:` (each OS keeps its own). On Kubuntu that is `~/dev/BrainHub/app`; on Windows a clone on the local disk. Below, `bh` stands for `uv run --no-sync python -m atlas`, the live copy and the root are both `/mnt/data/ATLAS` (Kubuntu) or `D:\ATLAS` (Windows), and the store is `/mnt/data/ClaudeShared` or `D:\ClaudeShared`.

1. **Tools.**
   - Kubuntu: uv, git, `gh auth login`, then `gh auth setup-git` (the brain's backup push then uses HTTPS without prompts); Rust and the Tauri libraries ([`desktop/README.md`](desktop/README.md)).
   - Windows: `winget install astral-sh.uv`, git, `gh` (`winget install GitHub.cli`) and `gh auth login`; Rust and the Visual Studio C++ build tools.
2. **The dev copy's venv:** in its `app/`, `uv sync --locked` (add `--extra test` to run the tests).
3. **The live copy:** a clean checkout of `master` at the root, with `core.filemode` set to `false` in its git config (ntfs3 reports execute bits, so otherwise Linux git sees every file as changed). A clone made by Windows git already has it; check with `git config core.filemode`.
4. **The first runtime slot:** `bh runtime init --live <live copy>`. It builds only a commit on `master` that the local `origin/master` has, and the first venv sync may download Python (a few minutes). `bh runtime status` shows the result.
5. **The desktop shell:** build it from the live copy with a local `CARGO_TARGET_DIR` and `--locked` (in `<live copy>/app/desktop/src-tauri`):
   - Kubuntu: `CARGO_TARGET_DIR=~/.cache/atlas-target cargo build --release --locked`;
   - Windows: `set CARGO_TARGET_DIR=%LOCALAPPDATA%\atlas-target`, then `cargo build --release --locked`.
   - Without `--locked`, cargo may rewrite `Cargo.lock` in the live copy, and a dirty live copy holds back every update.
6. **Point the shell at the slot:** `bh desktop-config --runtime --live <live copy> --root <root>`. Then start `atlas-desktop` from the target folder and tick **Start at login** in the tray ([Desktop shell](#desktop-shell)).
7. **The Claude Code integration:** Settings → Claude Code integration → Install, or `bh integration install --root <root>`, after step 4 (the hooks then run the stable copy in the state folder; Settings says "Reinstall" when they don't).
8. **claude-sync:** once, on the first OS, create the store: `bh claude-sync init --shared <store>`, then add your private backup repo with `git -C <store> remote add origin <url>`. Then on each OS: `bh claude-sync --dry-run` to see what it would do, and `bh claude-sync` (it merges this OS's existing memory into the store). Label notes that are only true on one OS: "(Windows only)" / "(Linux only)". From then on the shell runs it.
9. **First OS only: the setup wizard** on the real root ([First run](#first-run)), filling in both OS columns for project folders where you can.
10. **Checks:** the same time zone on both OSes (Europe/Bucharest; otherwise a quiet warning says so). On Windows, Fast Startup and hibernation stay off (`powercfg /h off` as admin; a quiet warning appears if an update turns hibernation back on).

## Run
The desktop shell starts the hub for you. To run one by hand (development, on a temp root and a temp state folder, never the real root):
```
ATLAS_STATE_DIR=/tmp/bh-state CLAUDE_CONFIG_DIR=/tmp/bh-claude ATLAS_BIN_DIR=/tmp/bh-bin uv run --no-sync python -m atlas --root /tmp/bh-test
```
(Windows: `set ATLAS_STATE_DIR=%TEMP%\bh-state`, `set CLAUDE_CONFIG_DIR=%TEMP%\bh-claude` and `set ATLAS_BIN_DIR=%TEMP%\bh-bin`, then `uv run --no-sync python -m atlas --root %TEMP%\bh-test`.) The two extra folders keep a test hub's Settings → Claude Code install away from your real Claude settings, `CLAUDE.md` and `~/.local/bin`. Then open <http://127.0.0.1:8090>. `--port N` overrides the port (default 8090, setting `port`); the hub then uses that port everywhere (Open in ATLAS, the integration install). If the port is already taken, it stops at once with `port N on 127.0.0.1 is already in use`, before any startup work. A second hub on the same root on this OS is refused too, whatever its port. The hub never creates the root folder: point `--root` at an existing folder (an empty one to start a new brain).

## First run
The setup wizard asks for your map: areas, projects (and which are companies, with their folders on Windows and on Linux; "(this PC)" marks this OS's column), courses and people, plus quiet hours and an optional backup remote. It runs only on a fresh root: an existing folder with no brain yet (nothing in `brain/`, no `map.json`; the live copy's files beside it are fine). Finishing it creates the `brain/` git repo (commit `setup`) and the marker `data/root.json`. You can edit the map later in Settings.

## Drive banner, holding inbox and recovery
At every start, before touching anything, the hub checks the root:

| Root | What runs |
|---|---|
| ok, or fresh (setup can create a brain) | the full app |
| **missing**: the folder isn't there or can't be read, or (Kubuntu) its drive isn't mounted (Windows left NTFS unclean, say) | a reduced app: the banner and the capture box; everything else shows the banner |
| **read-only** | the full app, read-only: Today, journal and recall work; confirm and tick are disabled with the reason; other changes answer with the reason (the reduced app if `hub.db` can't be read read-only) |
| **foreign**: `brain/` has files but no `map.json`, or the marker is there and `map.json` isn't | the reduced app; the banner says to point it at the right folder or restore `brain/` |
| **newer**: made by a newer ATLAS (brain format or `hub.db` schema) | the reduced app until this OS updates |

- **The banner** sits at the top of the app and in the widget's synopsis. For an unmounted drive it reads "ATLAS's drive isn't available. Boot Windows once and shut it down normally; Windows checks and repairs the drive. Then come back." ATLAS never runs `ntfsfix` or any repair itself.
- **Captures are never lost.** While the root can't be written, each capture goes to this OS's holding inbox (`<state>/holding/`), on disk before the box says saved. The banner adds "N captures held on this OS". The next full start on that root moves them into the raw log with their original time (each in its own day's file), then files them as usual, so their confirm cards appear then. Held captures belong to the root they were typed for, so a dev hub on a temp root never takes them. They stay on the OS where they were typed: Windows can't see Kubuntu's held captures, and they arrive at the next Kubuntu boot.
- **Recovery.** A reduced or read-only hub looks at the root again every 15 s. Once it is usable, the hub stops with exit code 75 and the desktop shell starts it again at once, in full. A hub you started by hand prints that it stopped, so start it again.
- **One hub per root.** `data/hub.lock` refuses a second hub on this OS in this boot. A lock left by the other OS, an earlier boot or a dead hub is taken over.
- **Quiet warnings** (one line under the header, never a popup): this OS's time zone differs from the one the brain was made in; hibernation (and with it Fast Startup) is on again (Windows).
- **Clean-up at start:** stale git locks in `brain/.git` are removed, Agents rows from before this boot are ended, and leftover filing worktrees are removed.

## Updates
On the dual boot each OS runs the hub from one of two runtime slots in its state folder, and the shell runs an update step before every hub start. It fast-forwards the live copy (only a clean checkout of `master`, only to a commit whose CI passed on both OSes, never anything but `--ff-only`), builds the other slot when the live copy moved, and switches to it once its code and venv are both ready. If anything stops it (offline, a dirty or diverged live copy, CI pending or failed, a venv that can't sync), the hub starts on the slot it already has, and Settings' Runtime line says "update pending (reason)". **Restart hub** in the tray runs the step again. When the shell's own source changed, the tray and Settings say "widget update available": rebuild as in setup step 5. Details: [`desktop/README.md`](desktop/README.md) → Runtime mode.

## Backup
Create an **empty, private** GitHub repository, then paste its URL in Settings (backup remote). The hub pushes `brain/` shortly after commits (debounced, 5 minutes by default). Push status shows in the Settings view; "Push now" forces it. A push never asks for a password: git runs without a terminal prompt or credential-manager window and gives up after 120 s, so set up credentials on each OS before relying on it: Git Credential Manager on Windows, `gh auth setup-git` (HTTPS) on Kubuntu.

`brain/.gitattributes` (`* -text`) keeps line endings exactly as written, so a restore with `git clone` gives a readable raw log even where git converts line endings by default.

## Claude and `ANTHROPIC_API_KEY`
All Claude calls run `claude -p` on your subscription. `ANTHROPIC_API_KEY` is removed from the environment of every child process **on purpose**, and `--bare` is never used, so you are never billed per token by accident. Models are settings: filing `sonnet`, quick recall `haiku`, deeper recall `sonnet`.

The hub's agents run isolated from your own Claude Code setup: they load no settings files (`--setting-sources ""`), so none of your plugins, hooks or permission rules apply; no MCP servers (`--strict-mcp-config`); no skills or slash commands; no saved sessions; no auto memory (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, so filings leave nothing in `~/.claude/projects`); and only the built-in tools each job needs (`--tools`: none for quick recall, `Read Glob Grep` for dig deeper, `Read Write Edit Glob Grep` for filing). Your login still works, since it is not a setting. Because they load no settings, the hub's own agents never fire ATLAS's hooks, so they never show up in the Agents panel. A filing that would create a path Windows can't hold (a reserved name or character, a trailing dot or space, or two names differing only in case) fails into To review instead of reaching `brain/`.

## Security
The server binds `127.0.0.1`. With **Tablet over Wi-Fi** on (phase 3), it also listens at the one `tablet_address`, and only while the laptop has that address. A request arriving there may reach only the tablet's page and `/api/tablet/*`, from its own origin and Host, and only with a paired device's cookie. Everything else answers 403 there, even when paired. Writes require `Content-Type: application/json` and a same-origin `Origin`/`Host`, so cross-site requests from other web pages are blocked. The UI renders text with `textContent` only.

Phase 2 keeps the same model. Any local program can already reach the hub, so a program forging hook events could only change what the Agents panel shows. The hook client sends an allow-list of fields (event name, session id, cwd, source, reason, notification type, session title, and messages capped at 300 characters): never your prompts, tool inputs or outputs, or transcripts. `/api/open` only opens the app, the arm URL or the usage URL from your settings.

## Phase 2: usage meter, Agents panel, widget, desktop shell

### Claude Code integration
The usage meter and the Agents panel are fed by your own Claude Code: hooks report session events, and the status line reports your rate limits. Both run `hook_client.py`, a tiny standard-library script that posts to the hub and **never prints anything or fails visibly**: with the hub down it gives up after about 0.3 s and exits quietly.

Install it once on each OS (it writes your user settings, so only when you ask):
- **Settings view → "Claude Code integration" → Install**, or
- `uv run --no-sync python -m atlas integration install --root <root>`

Remove it with **Remove** in the same section, or `... integration uninstall --root <root>`. `... integration status --root <root>` prints what is installed. `--settings PATH` targets another settings file (for tests). The Settings section also shows the last hook event and the last usage update the hub received, whether the installed port matches, and "Reinstall" when the installed hooks point at an old Python or script.

What it changes in `~/.claude/settings.json` (Windows: `%USERPROFILE%\.claude\settings.json`; or `$CLAUDE_CONFIG_DIR/settings.json`):
- Under `hooks`, one entry for each of `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Notification`, `Stop`, `StopFailure` and `SessionEnd`. Each runs `<python> -I -S <hook_client.py> event --port <port>` directly (no shell), with a 5 s timeout. On the dual boot (once `runtime init` has run) `<python>` is the base Python the runtime venvs are made from and `<hook_client.py>` is the stable copy `<state>/hook/hook_client.py`, which no update moves; in a dev setup they are the venv's python and the checkout's `atlas/hook_client.py`. `UserPromptSubmit`, `Notification` and `PostToolUse` are async so they never hold you up; the others are synchronous so they are not lost when a session closes.
- `statusLine` runs `hook_client.py statusline --port <port>` and prints a compact line such as `Sonnet · 5h 62% ↻14:02 · 7d 31%` (on Kubuntu the command is quoted for `sh`). If you already had a status line, it is saved in the state folder (`claude_integration.json`, `statusline_passthrough.txt`), keeps running (its output is shown instead of ours), and is put back exactly on Remove.
- Each OS keeps its own: installing or removing on one OS never touches the other's settings, saved status line or hooks.
- Every other key, and every hook of your own, is left as it was. Before each write the file is copied to `settings.json.atlas-backup-YYYYmmdd-HHMMSS` next to it, and the write is atomic. A file that isn't valid JSON, or has an unexpected shape, is refused and left untouched.
- Install again after changing the hub's `port` (the Settings hint says so); install is idempotent. New Claude Code sessions pick the change up; restart sessions that were already open.

### Usage meter
The **Usage** view shows the 5-hour and weekly windows: the percentage used, "resets 14:20 (in 2 h 5 min)" (with the date when the reset isn't today), a warning style at or above `usage_warn_pct`, and "updated 3 min ago" in grey once the figures are more than 5 minutes old. After a reset passes it shows `?` and "reset at 14:20 · no data since" until new data arrives. "Fable usage ↗" opens `fable_usage_url`.

The figures come from Claude Code's status line, so they update only while a Claude Code session with the status line is running, and only on Pro/Max plans after the session's first answer. Each window keeps its last known value until a new one arrives.

### Agents panel
The **Agents** view lists your Claude Code sessions on this PC: project, part, where, status and the next-time flag.

| Status | When |
|---|---|
| ready | the session started, no prompt yet |
| running | you sent a prompt, or a tool ran |
| waiting for you | a permission prompt or a question, a missed Stop, or a turn that failed (`StopFailure`: the note says why, e.g. `rate_limit`) |
| finished | the turn ended; it's your turn |
| ended | the session closed (shown muted until it disappears; it no longer counts in the widget, even if it was closed mid-turn) |

- **Project:** the map project whose folders on this OS contain the session's folder (set project folders in Settings → map; matching ignores case on Windows and is exact on Linux), otherwise the git repository's folder name. **Part:** your rename, else the session title (`/rename`), else the branch without its prefix (`feat/wrist-servo` → `wrist-servo`). **Where:** terminal, IDE (VS Code), desktop app, or unknown (hover to see the raw value Claude Code reported, and the OS). **Next-time flag:** "2 next-time notes for Robot arm" (mapped projects only); it is shown to you only and never sent to the agent.
- **Link to a project:** an unmapped session whose folder has the same name as a project's folder on the other OS (`~/dev/RobotArmControl` and `D:\RobotArmControl`) shows "link to <name>"; one click adds the folder to that project for this OS. Nothing is mapped automatically.
- Click a part to rename it, change Where from its menu, or remove a stale row (✕). Edits stick for the session.
- A row disappears `agents_end_linger_s` seconds after its session ends, or after `agents_stale_h` hours without events (a crashed session). Rows from before this boot (a crash, or the other OS) are ended when the hub starts.
- `claude -p` and SDK runs (entrypoint `sdk-…`) and anything running inside the hub's own `data/` or `brain/` never appear.

### Widget
`http://127.0.0.1:<port>/widget` is a small always-dark panel; it works in any browser, and the desktop shell shows it in a frameless always-on-top window.
- **It is ATLAS minimised** (2026-10-24). The shell shows the widget only while the main window is minimised, or before it was ever opened (a login autostart); opening the main window hides it, and closing the main window (to the tray) hides both. The tray's Show/Hide widget overrides that until the main window next changes.
- **Collapsed:** one line saying what needs you, e.g. `1 agent needs you · 2 to review · 1 request · 5h 38%`, or `All clear · 5h 38%`. An amber square means something waits for you. With a drive banner, the line shows the banner instead.
- **Expanded** (the ▾ button): a capture box (Enter saves, with source `widget`; Esc collapses), then **NEEDS YOU**, one list, most urgent first: agents waiting for you, entries to review, tablet requests, overdue and due-today items, usage at or above the warning level, people to nudge. A row opens the app on its page. Past six rows the list scrolls inside itself, so the panel never runs off the screen. Under it a strip (`5H 38% · 7D 21% · 1 RUNNING · ARM OFF`) and **OPEN ATLAS**. The arrows move between the capture box and the rows.
- It refreshes on hub events and every 60 s; with the hub down it says `ATLAS offline · retrying`, and it reconnects by itself when the hub is back (the desktop shell doesn't reload it).
- What you type in the capture box is kept as a draft in the page's local storage until it's saved. If the page reloads, the widget opens expanded with your text back in the box.

### Desktop shell
`app/desktop/` is a Tauri v2 shell, "ATLAS", built on each OS (WebView2 on Windows, WebKitGTK under XWayland on Kubuntu): the widget window, the tray icon, start at login, and a supervisor that starts the hub (on Windows without console windows) if it isn't running and restarts it after a crash. It holds no logic of its own. Full instructions: [`desktop/README.md`](desktop/README.md). In short:
1. Build once per OS, from the live copy, into a local target folder (setup step 5): about 5 minutes the first time; the result is `<target dir>/release/atlas-desktop` (`.exe` on Windows).
2. Configure: `desktop-config --runtime --live <live copy> --root <root>` (setup step 6) writes `desktop.json` to the config folder: `port`, `root`, `live`, `state`. The shell then runs claude-sync once and the update step before starting the hub from the current runtime slot with `--port <port>`. (Without `--runtime` it writes the dev form, `python` and `app_dir`, and starts the hub straight from that checkout.) After changing the port, re-run it **and restart ATLAS**: the shell reads the file only at start.
3. Run `atlas-desktop`. Tray: Open ATLAS, Show/Hide widget (an override: the widget otherwise follows the main window), Start at login, Restart hub (only for a hub the shell started), Quit (stops that hub too).
4. **Start at login** (tray, off by default): the `HKCU\...\CurrentVersion\Run` key on Windows, `~/.config/autostart/` on Kubuntu (KDE lists it under Autostart); untick to remove.
5. Logs: `hub.log` in the state folder (hub output and the shell's `[atlas-desktop t=…]` lines).

### Phase 2 settings
All in the Settings view.

| Setting | Default | What it does |
|---|---|---|
| `usage_warn_pct` | 80 | Warning style and the amber dot at or above this % (1–100). |
| `fable_usage_url` | `https://claude.ai/settings/usage` | Opened by "Fable usage ↗" (`https://` only). |
| `agents_end_linger_s` | 60 | Seconds an ended session stays in the Agents panel (0–3600). |
| `agents_stale_h` | 12 | Hours without events before a row is dropped (1–168). |
| `arm_url` | `http://127.0.0.1:8080` | The robot arm (DUMMY) UI: the tablet's DUMMY tile opens it, and the widget's strip says whether it answers. |

## Phase 3: the workbench tablet
The Samsung SM-T110 at the bench, in Fully Kiosk Browser, is a separate ES5 page at `/tablet`. Its home screen has two tiles, each with its logo: **DUMMY**, which opens the arm UI, and **ATLAS**, the journal. The journal has three tabs:
- **TODAY:** a glance only.
- **NOTES:** measurement requests and free-text notes.
- **AGENTS:** Claude Code sessions and the usage meters.

All typing uses an in-page keyboard, so the Android keyboard never opens. On the PC, the same page opens on localhost without pairing: http://127.0.0.1:8090/tablet.

**Setting it up** (once per OS; design §11):
1. Reserve the laptop's address for its Wi-Fi MAC in the router (`192.168.0.200` for `24:b2:b9:cd:c5:69`). Both OSes use the same MAC, so they get the same address.
2. Let the port through the firewall. Kubuntu: `sudo ufw allow from 192.168.0.0/24 to any port 8090 proto tcp`. Windows, later: allow `python.exe` on private networks.
3. Settings → **Tablet**: set the address ("Use … (now)" fills in this laptop's), tick **Tablet over Wi-Fi**, then Save. The section says "Listening on http://192.168.0.200:8090/tablet".
4. In Fully Kiosk, set the start URL to `http://192.168.0.200:8090/tablet`. Press **Pair a device** in Settings and type the 6-digit code on the tablet. It's valid for 5 minutes and works once. The tablet is then remembered; Settings lists it, with **Revoke**.
5. Install the integration again (Settings → Claude Code integration → Install, or `integration install`). This adds the `atlas-request` command to `~/.local/bin` and one line about it to `~/.claude/CLAUDE.md`, after a backup.

**Measurement requests:** an agent runs `atlas-request "Arm calibration" "ArUco ID 3 → arm base" "ID 3 → ID 7"`. The checklist appears on the tablet's NOTES tab within about a second, and the widget shows "1 request".
- On the tablet you type each value with its unit (`412` then the **mm** chip), and **SET** moves to the next item. **SEND** writes one raw-log entry with every value.
- Answers go to **Tablet** on the PC, never back to the agent.

**Tablet notes** are kept verbatim in the raw log (`source=tablet`) and can be found by Ask. They are **not filed** until you press **File this** in the PC's **Tablet** view.

**IGNORE** on the tablet needs a second tap within 2 s. It hides a note or request without deleting it, and **Show ignored** on the PC brings it back.

**DUMMY and HOME:** DUMMY opens the arm UI with `?home=` pointing back to the tablet page. The arm UI's top-left corner then becomes **‹ HOME**: hold it, or tap it twice. HOME refuses while LIVE is on, was just switched on, or isn't known yet, and while another control is held, because the arm keeps moving when its page goes. (The arm's side is in its master since 2026-10-23.)

## Phase 4: voice and sass
ATLAS speaks through the PC speakers, in Atlas's voice (Kokoro, British English: 70 % `bm_george`, 30 % `bm_fable`, speed 0.95). Everything runs locally and offline, on the CPU. It speaks only for:
- **an agent that needs you** (a permission prompt, a question, an error stop);
- **an agent that finished a long run**: 2 minutes or more by default, measured from your prompt to the stop;
- **a reminder**: at its remind time and again at its due time. A date with no time speaks when quiet hours end (08:00 by default). After the hub was off, missed reminders become one line ("You have three reminders from while I was off"). Anything older than a day is left to Today;
- **a readback** of what you typed on the tablet ("Width of the desk. 1200 millimetres. Saved.").

It never reads text an agent wrote: only your project names and your own words. Nothing is spoken during quiet hours, and nothing silenced is saved for later.

**Sass:** after the information comes a quip from `brain/_voice/quotes.md`: 107 to start, in three styles (a dry butler, a sarcastic friend, a ship's computer). Each pool goes round without repeats. After a quip, lines within the next minute play without one. Readbacks get no quip unless you tick it. Edit the file any time (Settings → Voice → **Edit quotes**). Its header explains the format, and lines it can't use are listed in Settings.

**Setting it up:** Settings → **Voice** → **Download the voice** (about 121 MB, once, checked against pinned checksums), then **Play sample**. Settings also has the switches, which events speak, the voice blend, speed, volume and the model. **Pause** (an hour, or until morning) is there and in the tray menu. The widget's speaker icon switches the voice off and on.

**How it runs:** a child process (`python -m atlas voice-worker`) starts on the first line and exits after 10 minutes of silence, so its model's memory (about 300 MB) goes back. It plays with `pw-play`, `paplay` or `aplay` on Linux and `winsound` on Windows. If it fails (no audio device, a damaged voice file), the hub carries on and Settings shows the error. A read-only root has no voice.

## Phase 4.5: the native app and the instrument-panel look
**ATLAS is an app now, not a browser tab.** The desktop shell opens it in its own window:
- **Opening it:** from the tray's **Open ATLAS**, from the KDE launcher, or by clicking a widget tile, which jumps to that page.
- **The window:** it has a taskbar entry and an icon, appears in Alt+Tab, and remembers its size and maximised state. Closing it only hides it; the tray's **Quit** quits.
- **Fallback:** the tray still has **Open in browser**.

Add the launcher entry once with `python -m atlas desktop-entry install --exec <path to atlas-desktop> --live <live copy>`. Started from it, ATLAS updates itself (pulls master once CI passed, rebuilds the app when it changed). `desktop/README.md` has the details.

**The look is the arm UI's language,** taken from the robot arm's own control screen:
- a black ground split by 1px hairlines;
- small uppercase labels over white content, with every number in mono;
- outlined controls that invert to white when pressed, and one white primary button per panel;
- colour only when it means something: green is ok or live, amber is attention or a risky action. Risky buttons (dismiss, remove, revoke, ignore) need a second tap within 2 s.

**Where things are:**
- **Top bar:** the tabs TODAY · ASK · JOURNAL · BROWSE · AGENTS · TABLET, then a status cluster, then SETTINGS.
  - The status cluster holds attention chips, the review count, the 5-hour usage, the agents running and waiting, and the voice switch.
  - Keys: Alt+1…6 for the tabs, Alt+0 for Settings, Alt+C to jump to capture.
- **Capture** is always at the bottom: Ctrl+Enter saves. New filings show as compact rows just above it; open one with **REVIEW**.
- **Today:**
  - TO REVIEW is a queue; the selected entry's card opens below it. Use ↑/↓ to move through the queue.
  - Each proposal has ✓, EDIT and ✕. **CONFIRM ALL** confirms them all, and **TELL IT WHAT'S WRONG** re-files.
  - Items have a ring to tick them done and an inline **SNOOZE** (1 D / 3 D / 1 W).
- **Browse** has a rail: DEADLINES, PEOPLE, then your projects and courses.
- **Agents** shows the sessions table, with the usage panel beside it.
- **Settings** is a rail of sections, and each section saves on its own.

Fonts: Roboto and Roboto Mono (OFL) are bundled in `web/fonts/`.

## Claude Code sync (`claude-sync`)
`python -m atlas claude-sync` keeps your own Claude Code the same on both OSes through the shared store (`/mnt/data/ClaudeShared` = `D:\ClaudeShared`). Your login, sessions, history and plugin installs stay per OS.
- **Memory:** a home session (started in your home folder) loads your personal memory; a project session loads only that project's notes, on both OSes, from the same files. claude-sync merges this OS's existing notes into the store (after a backup to `<state>/claude-sync-backups/`; a note that clashes is kept under a second name, `<name>.<os>.md`), then points Claude Code there through `autoMemoryDirectory` in `~/.claude/settings.local.json` (home) and in each project's `.claude/settings.local.json` (git-ignored through the repo's `info/exclude`). A project is the same on both OSes when its folder has the same name in `~/dev` and on `D:\`; `pairs.json` in the store lists exceptions (`{"<name>": {"linux": "<folder>", "windows": "<folder>"}}`). The live copy is never pointed.
- **The header line:** every shared `MEMORY.md` starts with "Shared by Windows and Kubuntu. Label any note that is only true on one OS: (Windows only) / (Linux only)."
- **Skills:** your own skills in `~/.claude/skills/` move into the store and are linked back (plugin and claude.ai-synced skills are left alone).
- **Settings:** each key of `~/.claude/settings.json` is shared both ways (the later change wins; removals, like a disabled plugin, carry over), except the per-OS keys: `hooks`, `statusLine`, `permissions`, `env`, `defaultShell`, `apiKeyHelper`, `autoMemoryDirectory`, any value holding an absolute path, and the keys you add in Settings → Claude Code sync. `enabledPlugins` and `extraKnownMarketplaces` are shared, and Claude Code fetches missing plugins itself.
- **It never deletes a memory note or a skill.** It commits the store and pushes it to `origin` when one is set, without prompts.
- **When it runs:** in runtime mode the desktop shell runs it once at start (before the first hub), runs `--settings-only` within 5 s of a change to `settings.json`, and a full sync every 10 minutes, so notes written during sessions are committed in batches. By hand: `claude-sync [--dry-run]`. With the store missing (`D:` not mounted) it changes nothing.
- Problems go to `<state>/claude-sync.log`; Settings → Claude Code sync shows the last run as a quiet note.

## Verification on both OSes (dual-boot design §10)
Run after the rollout, with your machine and eyes. Drills run against a temp root or a scratch mount, never the real root.
- **CI** is green on both OSes for the commit you run.
- **Round trip:** capture on Kubuntu; reboot: on Windows it's in Today and the journal, and recall finds it; confirm an item and tick another; reboot: both show on Kubuntu. `git status` is clean in `brain/` and in the live copy on both OSes.
- **On each OS** (this includes the phase 2 checklist):
  1. Install the integration (setup step 7).
  2. A terminal session: a row with Where "terminal" appears, then goes running and finished.
  3. A VS Code extension session: does a row appear, and what does Where say? If it says "unknown", report the raw entrypoint from its tooltip (it fixes `WHERE_BY_ENTRYPOINT` in `agents.py`).
  4. On Windows, a desktop-app local Code session: same check. Claude Design in the desktop app: if no row appears, it stays excluded (spec §7.1). Nothing is scraped.
  5. The Usage view updates from a terminal session (and from VS Code or the desktop app, if the status line runs there; if it only runs in the terminal, that is acceptable, spec §11). The status line doesn't run under `claude -p`.
  6. The widget is transparent, on top, draggable, remembers its position and stays on screen; the tray works; with Start at login ticked, the hub and widget come up after a reboot.
- **Drills:**
  - **Drive not available:** a hub on a missing root (or, on Kubuntu, a root under an fstab mount point that isn't mounted) shows the banner, creates nothing, and holds a capture; once the root is back, the hub restarts (exit 75) and the capture reaches the raw log with its original time.
  - **Read-only root** (`chmod -R a-w` on a temp root): reading works, captures are held, confirm and tick are disabled with the reason, and `hub.db` is unchanged.
  - **Hub killed mid-filing, then the other OS boots on the same root:** the filing is recovered, no ghost agents remain, git is clean, and stale locks are cleared.
  - **A filing proposing a Windows-invalid or case-clashing path:** it is rejected into To review, and nothing is deleted on Windows.
  - **Fast Startup turned back on** (Windows): a quiet warning appears.
  - **A second hub on a root that already has one** (say, a dev copy started against the live root): on a temp root, start a hub, then another on the same root with another `--port`: refused.
  - **Integration installed on both OSes, then removed on one** (with `--settings <temp file>` on each): the other OS's status line and hooks are untouched.
- **Claude Code parity:** a note written in a home session on Kubuntu is in the next home session on Windows; the same for a project note; skills and plugins match on both; a Windows-only note is labelled.
- **Updates:** a trivial change merged on GitHub is picked up at the next start on each OS; an offline start runs the current code quietly; a dirty live copy is left alone, with a note in Settings; a commit without green CI on both OSes is not pulled; with `D:` unmounted the hub still starts from its runtime slot, in reduced mode; if one OS pulled a dependency change and the other boots offline, the other keeps its previous slot (and runs reduced if the data is already newer), and nothing crashes.

## Tests
From `app/` in a dev copy (Kubuntu `~/dev/BrainHub/app`):
```
uv sync --locked --extra test
uv run --no-sync python -m pytest -q
node --test web/js/components/*.test.mjs web/js/views/*.test.mjs web/js/*.test.mjs web/tablet/*.test.mjs
```
(On Kubuntu load nvm first: `source ~/.nvm/nvm.sh`.) From `app/desktop/src-tauri`, with the local target folder:
```
CARGO_TARGET_DIR=~/.cache/atlas-target cargo test
```
(Windows: `set CARGO_TARGET_DIR=%LOCALAPPDATA%\atlas-target`, then `cargo test`, with `%USERPROFILE%\.cargo\bin` on `PATH`.) CI runs the Python and JS tests on Ubuntu and Windows for every PR and push to master, and the Rust tests and a build when `app/desktop/` changes. A test that only makes sense on one OS is skipped on the other and runs in CI there.

The Python tests use a fake Claude runner; none of them call the real CLI. They point `CLAUDE_CONFIG_DIR`, `LOCALAPPDATA`, `ATLAS_STATE_DIR`, `ATLAS_CONFIG_DIR`, `ATLAS_SHARED_DIR` and `ATLAS_BIN_DIR` at temp folders, so they never touch your Claude settings or `CLAUDE.md`, your state or config folder, `desktop.json`, the Claude store or `~/.local/bin`. The tablet page can't run Android 4.2's browser in a test, so `tests/test_tablet_es5.py` refuses the syntax, DOM APIs and CSS that browser lacks.

## Known limitations
Deferred on purpose; none of them loses data.
- Voice (phase 4): a line waits up to 3 s for others of its kind, so two agents finishing together are one sentence. Windows plays through `winsound` and is untested by hand while Windows is on hold. The tray's Pause voice items don't show the pause's end: the widget's speaker and Settings do.
- Owner edits to notes are committed (and re-indexed for search) at the next hub commit or restart, not the moment the file is saved: there is no file watcher yet.
- `_items/proposals.json` and dismissals reach the backup with the next hub commit, not on their own.
- `map.json` is written in place, not via a temporary file; a crash mid-write needs a `git checkout map.json` in `brain/`.
- `slugify` drops letters that have no ASCII decomposition (`ł`, `ø`): "Łódź" becomes `odz`.
- A `#` line inside a fenced code block in a note counts as a heading for search sections.
- `_items/<group>.md` files for groups that no longer have items are left in place (stale, read-only views).
- `build_fts_query` neither caps nor de-duplicates question words.
- WebSocket broadcasts are awaited inline, so a very slow browser tab can delay an API answer.
- A setup that fails after validation (e.g. git missing) leaves `map.json` behind and the wizard then sees "setup already done".
- The Journal area filter and the project page's journal list only see the entries loaded so far (newest 50/500); load older entries to filter further back.
- A tolerant raw-log read resynchronises at the next record header; text inside a damaged record that itself looks like a complete record would be read as one (it is still reported).
- Folders named `log` or `_items` nested inside other folders are hidden from the notes viewer.
- If `claude_path` resolves to an npm `.cmd` shim, arguments pass through `cmd.exe` quoting.
- "Filed to" and the project page's journal list use the agent's own list of changed files, not the files its commit actually changed.
- Brain commit messages are coarse: a capture's log append is committed as "owner edits", and confirms can sweep real owner edits into "confirm: N items".
- Done (✓) in Today and dismiss (✕) on a card have no undo, and there is no view of done or dismissed items yet.
- Item rows don't link back to the entry they came from (the data is stored).
- The People page doesn't show "done" delegations or how long you've been waiting.
- After a correction's re-filing fails, pressing Retry may treat the reverted filing as still present and drop the correction text; re-submit the correction instead.
- If the filing agent both edits a protected file and creates a new hidden folder, the filing fails safely and needs a Retry.
- Whether the agents still read a user-level `CLAUDE.md` memory file under `--setting-sources ""` is not verified; keep yours free of instructions that would clash with filing.

Phase 2 (the full list, with the test gaps, is in [`phase2-decisions.md`](../docs/superpowers/phase2-decisions.md)):
- Whether hooks fire, and what Where says, in VS Code and desktop-app sessions is not verified yet, on either OS (verification items 3–4); whether the status line runs there is not documented (item 5).
- Usage only updates while a Claude Code session with the status line runs.
- After changing `port`: restart the hub, install the integration again, re-run `desktop-config` and restart ATLAS, on each OS. Until then a shell-started hub stays on the old port (the shell passes `--port`), so everything keeps working on the old one.
- An event after a session's `SessionEnd` (other than a new start) doesn't revive the row, so it still disappears after the linger. Ignorable events still bump a row's "last event" time, which can change the order.
- Backups of `settings.json` made by Install/Remove are never pruned; delete old ones by hand.
- If the state folder's `claude_integration.json` is unreadable when you install again, the saved previous status line is lost (it is still in the backups).
- The status line can take up to about 2.75 s (4.5 s worst case) when a saved previous status line command hangs.
- A desktop shell killed hard leaves the hub it started running; the next launch uses it, but "Restart hub" is then greyed out.
- `hub.log` in the state folder grows without bound.
- The widget is kept inside the screen's work area when it is built and whenever it resizes, not while you drag it: a widget dragged partly off-screen stays there until it next expands or collapses.
- The desktop exe keeps its web view data and window position in the real folders even when the env overrides are set (`%LOCALAPPDATA%\dev.atlas.desktop` and `%APPDATA%\dev.atlas.desktop\.window-state.json` on Windows; `~/.local/share/dev.atlas.desktop` and `~/.config/dev.atlas.desktop` on Kubuntu).
- The capture draft lives in the widget page's local storage, which belongs to `127.0.0.1:<port>`: after a port change an unsaved draft from before stays behind with the old port.

Dual boot (the full list is in [`dual-boot-decisions.md`](../docs/superpowers/dual-boot-decisions.md) → Residuals):
- The shell can't update itself: after "widget update available", rebuild it by hand (setup step 5).
- Held captures stay on the OS where they were typed until that OS boots again with the root usable.
- The read-only view doesn't read a `-wal` file left by a crash, so the newest index changes can be missing until the next full start (the raw log is complete either way).
- An integration installed before `runtime init` keeps the dev paths until it is installed again (Settings says "Reinstall").
- claude-sync doesn't share a user-level `CLAUDE.md`, and doesn't list plugins or MCP servers that need per-OS setup.

## Measured
### Phase 1
Real end-to-end run on a scratch root with the real Claude CLI 2.1.304 (2026-10-21), using the sample entry about the robot arm wrist servo, a present deadline and a teammate owing endpoints:

| Step | Model | Wall clock |
|---|---|---|
| Filing (capture to `done`) | sonnet | 18.1 s |
| Recall `what did I say about the wrist servo?`: search hits | local | instant (0.01 s) |
| Recall: first answer text | haiku | 4.4 s |
| Recall: done | haiku | 5.7 s |
| Recall `zebra` (no hits, no Claude call) | none | instant, `Nothing on that.` |

The filing time is the successful run (a retry of the same entry: the first attempt failed only because the scratch root had no `app/` folder; the hub now finds its prompts, schemas and UI next to the installed package, so `--root` can be any data folder). Times depend on load and model latency.

### Phase 2
Real end-to-end run on 2026-10-21 with Claude Code 2.1.306, on a scratch root (`%TEMP%\bh-e2e`, port 8095, deleted afterwards). Setup mapped project `robotarm` to `<scratch>\robotarm`, a git repo on branch `feat/wrist-servo`. The integration was installed into `<scratch>\claude\settings.json` with `integration install --settings`; the real `~/.claude/settings.json` was hashed before and after and never changed.

**Forced entrypoint.** Claude Code sets `CLAUDE_CODE_ENTRYPOINT` itself for hook processes, so under `claude -p` hooks see `sdk-cli` whatever the parent environment says, and the hub ignores `sdk-*` on purpose. For the Agents run, a copy of the installed settings wrapped each hook as `cmd.exe /c "set CLAUDE_CODE_ENTRYPOINT=cli&& <python.exe> -I -S <hook_client.py> event --port 8095"` (same timeouts and async flags). The unmodified installed hooks were checked separately by pointing their `--port` at a stdlib HTTP sink: under `-p` they delivered `SessionStart`, `UserPromptSubmit`, `Stop` and `SessionEnd`, each with `entrypoint: sdk-cli`, with only the allow-listed fields (no prompt text). The same `sdk-cli` event posted to the hub returned 204 and created no row.

`claude -p --model haiku --settings <forced copy> --no-session-persistence "Reply with just: ok"` in `<scratch>\robotarm`, without `ANTHROPIC_API_KEY`, stdin closed; `/api/agents` polled every 0.25 s:

| Time after launch | Agents panel |
|---|---|
| 0.8 s | new row: project `robotarm` (mapped, "Robot arm"), branch `feat/wrist-servo`, part `wrist-servo`, where `terminal`, status ready |
| 5.1 s | running |
| 7.2 s | finished (`claude` printed `ok` and exited at 8.1 s) |
| 7.5 s | `ended_at` set (SessionEnd) |
| 67.2 s | row gone (`agents_end_linger_s` = 60) |

With stdin left open, `claude -p` first waits 3 s for it ("no stdin data received in 3s"), which shifts everything by 3 s.

| Check | Result |
|---|---|
| Status line: a realistic status line JSON piped into the installed `statusLine.command` through Git Bash | 0.14 s; printed `Sonnet · 5h 62% ↻14:02 · 7d 31%`. `/api/usage` then had five_hour 62.4 % (resets 14:02) and seven_day 31 % (resets 10-24 12:57); the widget synopsis read `5h 62% (resets 14:02)`. |
| Status line with the hub down | 0.35 s, same line printed |
| Hook event with the hub down | 0.32 s, exit 0, no output |
| Uninstall on the scratch file | our entries removed (the file became `{}`, as it had nothing else), with a backup written first |
| Desktop shell (temp `LOCALAPPDATA`, `desktop-config --target`) | connected to the running hub; collapsed 650×55 px (520×44 at 125 %), expanded 650×310 px |
| Hub killed under the shell | the shell started its own `pythonw` hub (before the fix wave; now `python.exe`, Ruling 13); port answering again after 3.4 s; the widget came back with the usage and agent data |
| Start at login | not ticked; no `HKCU\...\Run` entry was added |

**Fix-wave re-check** (2026-10-21, after the final review; scratch root `%TEMP%\bh-fix-root`, temp `LOCALAPPDATA`, port 8099, all deleted afterwards):

| Check | Result |
|---|---|
| Hub started by the shell | `python.exe -m atlas --root <scratch> --port 8099` (console python, not pythonw), with a hidden `conhost` |
| Setup over HTTP (startup git commits), then two captures filed by the real `claude` | 3 brain commits and 2 filings; a 40 ms poll of visible top-level windows saw no console, Terminal, git or claude window at any point |
| Draft in the capture box, then the hub killed and restarted by the shell | the page was not reloaded (same `performance.timeOrigin`), still expanded, text intact |
| Forced page reload with a draft | came back expanded with the text; Enter saved it and cleared the draft |
| Widget moved below the work area, then expanded | moved up so its bottom sits on the work area's bottom edge |
| Settings port changed to 8098 without `desktop-config` | the restarted hub still listened on 8099 (the shell's `--port`) |
| `desktop-config` re-run, shell restarted | hub started with `--port 8098`, widget on `127.0.0.1:8098/widget` |
| A second hub by hand on the taken port | exited in 0.26 s with `port 8098 on 127.0.0.1 is already in use`, no startup work |

### Phase dual-boot (Linux)
Real end-to-end run on 2026-10-22 on Kubuntu with Claude Code 2.1.308 (plan Task 10), on a scratch root (`/tmp/bh-e2e-t10/root`, port 8099, a scratch `ATLAS_STATE_DIR`, all deleted afterwards). Setup mapped project `robotarm` to the Linux folder `<scratch>/robotarm` (`os_folders.linux`; not a git repo, so no branch). The scratch state folder was put in runtime mode by hand (a `runtime/current.json` and the stable `hook/hook_client.py`), so `integration install --settings <scratch>/claude/settings.json` wrote the runtime paths: the base interpreter `~/.local/share/uv/python/cpython-3.14-linux-x86_64-gnu/bin/python3.14` (uv's minor-version link) and `<state>/hook/hook_client.py`, in exec form, and the status line as one `sh`-quoted command. The hub's `/api/integration` showed it installed, port matching.

**Login.** A scratch `CLAUDE_CONFIG_DIR` has no login: `claude -p` there still fired SessionStart, UserPromptSubmit and SessionEnd through the installed hooks, then printed `Not logged in · Please run /login` and exited 1 after 0.8 s. The runs below used the real config folder with only `--settings <scratch file>` (no credentials copied). The real `~/.claude/settings.json` hashed `e9f21709…81de2b71` before and after, mtime unchanged. Even with `--no-session-persistence`, Claude Code created an empty `~/.claude/projects/-tmp-bh-e2e-t10-robotarm/memory/`, which was removed.

**Forced entrypoint**, as in phase 2. The unmodified installed hooks, pointed at a stdlib sink, delivered SessionStart, UserPromptSubmit, Stop and SessionEnd, each with `entrypoint: sdk-cli` and only the allow-listed fields. For the Agents run, a copy of the installed settings wrapped each hook as `{"command": "env", "args": ["CLAUDE_CODE_ENTRYPOINT=cli", <python>, "-I", "-S", <hook_client.py>, "event", "--port", "8099"]}` (same timeouts and async flags).

`claude -p --model haiku --settings <forced copy> --no-session-persistence "Reply with just: ok"` in `<scratch>/robotarm`, without `ANTHROPIC_API_KEY`, stdin closed; `/api/agents` polled every 0.25 s:

| Time after launch | Agents panel |
|---|---|
| 0.5 s | new row: project `robotarm` (mapped by its Linux folder, "Robot arm"), where `terminal`, status idle |
| 0.8 s | running |
| 2.6 s | finished |
| 2.8 s | ended (SessionEnd); `claude` printed `ok` and exited at 3.6 s |
| 62.6 s | row gone (`agents_end_linger_s` = 60) |

| Check | Result |
|---|---|
| Status line under `claude -p` | doesn't run: no usage POST reached the sink or the hub in either run |
| A realistic status line JSON piped into the installed `statusLine.command` through `sh -c` | 0.03 s; printed `Sonnet · 5h 62% ↻18:46 · 7d 31%`. `/api/usage` then had five_hour 62.4 % and seven_day 31 %; the widget synopsis read `5h 62% (resets 18:46)` |
| Status line with the hub down | 0.03 s, same line printed |
| Hook event with the hub down | 0.04 s, exit 0, no output |
| An `sdk-cli` event posted to the hub | 204, no row |
| Uninstall on the scratch file | our entries removed (the file became `{}`), with a backup written first; the update step's `hook/hook_client.py` kept |

### Test-drive follow-up (Linux, 2026-10-23)
The owner felt filing and recall were slow. This is a real run on Kubuntu with Claude Code 2.1.309, on a scratch root (port 8097, scratch state, config and shared folders, all deleted afterwards). A timing pass-through named `claude` was first on the hub's `PATH`; it logged when each stream event arrived. Three captures were filed one after another (the robot arm entry with a deadline and a teammate who owes endpoints, a paper to read, `buy coffee`), then two questions were asked. No status line was installed.

| Stage | Sonnet filing (two runs) | Haiku filing (one run) |
|---|---|---|
| Capture saved (`POST /api/entries`) | 0.002–0.004 s | same |
| `queued` → `running` | 0.1 s | same |
| `claude` start-up to `system/init` | 0.63 s (2.07 s for the very first run after boot) | same |
| Agent turns per filing | 4–6 | 3–6 |
| Model time (`duration_api_ms`) | 8.3–20.2 s | 5.2–13.1 s |
| Process exit after its `result` line | 0.5–0.7 s | same |
| The hub's own work (sweep, worktree, commit, cherry-pick, index) | about 0.2 s | same |
| **Capture → `done`** | **9.8–21.5 s** (robot arm 10.7 / 11.2, paper 11.4 / 21.5, coffee 9.8 / 12.4) | 14.6, 10.6, 7.2 s |

Recall (Haiku, `recall_top_n` 12): first text after 1.4–3.2 s, `done` after 2.5–4.2 s; about 0.9 s of that is the CLI exiting after its `result` line.

- **Where the time goes:** model turns. The hub's own work is about 0.2 s, and the 2 s first start-up after boot drops to 0.6 s.
- **Haiku for filing is no fix.** It was not reliably faster, and it filed worse: it added `(from e-…)` to every proposal text, and for `buy coffee` it wrote no note because "personal/ does not exist yet". Sonnet created `personal/errands.md`.
- **Usage meter (rulings 20–21):** with no status line at all, `/api/usage` read 7 % (5 h) and 9 % (weekly) from the hub's own runs, the filings' schema runs included. Each run's `rate_limit_event` agreed with the previous probes. No `~/.claude/projects/` folder was created.

**After the speed fixes** (same day, same three entries and two questions; rulings 22–23 in `phase2-decisions.md`):

| Stage | Before | After |
|---|---|---|
| `claude` start-up to `system/init` | 0.54–0.94 s | 0.22–0.26 s |
| Process exit after its `result` line | 0.5–0.9 s | 0.01–0.03 s |
| Capture → `done`, Sonnet (robot arm / paper / coffee) | 10.7–11.2 / 11.4–21.5 / 9.8–12.4 s | 21.2 / 8.1 / 8.2 s |
| Recall `done` | 2.5–4.2 s | 2.3–2.7 s |

- Every run saves about 1.2–1.6 s. The rest is the model, and it varies a lot: the robot arm entry spent 13 s between its last write and `wrapping up`, composing four proposals.
- **Live progress,** as a WebSocket client saw it for the robot arm entry: `filing.started` at 0.03 s, then `looking for projects/robotarm/**` (2.3 s), `looking for general/**` (2.8 s), `writing projects/robotarm/notes.md` (7.2 s), `writing general/andrei.md` (8.2 s), `wrapping up` (21.1 s), `filing.done` (21.2 s). `GET /api/filings/<id>` mid-filing carried `progress` (`started`, `step`), and `null` once done. The real tool names were `Glob`, `Write` and `StructuredOutput`.
- **In the page** (headless Chrome over the DevTools protocol, a scratch hub, and a fake `claude` playing slow Glob → Write → StructuredOutput steps): after Save, the tray card appeared at once and read `Filing… looking for projects/robotarm/** · 0 s`, then `… writing projects/robotarm/notes.md · 4 s`, then `… wrapping up · 6 s`. It then turned into the review card, with no console errors.
- The meter still updated from the runs (11 % and 10 %), so the env var doesn't hide `rate_limit_event`. No `~/.claude/projects/` folder was created.

### Phase 3 (Linux, 2026-10-23)
These checks ran on Kubuntu without the tablet itself. A scratch hub ran on port 8097 with **Tablet over Wi-Fi** on at this laptop's real address (`192.168.0.200`; ufw keeps the port closed to the rest of the network). Headless Chrome ran at 1024×600 with touch emulation, and a fake `claude` stood in for filing, so no plan usage was spent. Everything was deleted afterwards.

| Check | Result |
|---|---|
| The LAN site | `tablet over Wi-Fi: listening on 192.168.0.200:8097` within one check; unpaired, `/api/tablet/state` over the LAN answered 401 and `/api/today` 403 |
| `atlas-request "Arm calibration" "ArUco ID 3 → arm base" "ID 3 → ID 7"` (the real script, exit 0) | `Sent to the workbench tablet (r-…)`; the tablet's NOTES tab showed the card with a count chip |
| Pairing over the LAN | code typed on the page's keypad by touch, then the home screen; Settings listed "Workbench tablet" |
| A measurement answer typed on the in-page keyboard | `4` `1` `2` then the **mm** chip, **SET** (moved on to the next item), `9` `8`, **SEND** → one raw-log entry `Measurements: Arm calibration (…)` / `- ArUco ID 3 → arm base: 412 mm` / `- ID 3 → ID 7: 98`; the request closed |
| A free-text note | **⇧ SHIFT** then `bench ok`, then **SAVE** → `Bench ok` in Tablet notes, not filed |
| HOME | held 0.8 s → home screen |
| Console errors | none |
| The arm UI's HOME (`server.py --sim --no-camera` from a scratch copy, port 8098), after its review | no `?home=` → "ARM-6"; `?home=javascript:…` or a URL not ending in `/tablet` → refused; `?home=<tablet>` → "‹ HOME", and not remembered on the next plain visit; one tap → nothing; held while LIVE on → amber "LIVE ON", stayed; LIVE just asked for → "LIVE ON"; another control held → "LET GO"; LIVE off, two taps → left for the tablet page; server killed → "WAIT" at first, allowed after 5 s out of reach |

The first run of the arm check caught a real bug (phase 3 decisions T16): the HOME action had been named `home`, which is the arm UI's **Start pose** action.

### Phase 4 (Linux, 2026-10-23)
These checks used a scratch hub on port 8097 with a temp root and state, and the real voice files. A stand-in `pw-play` first on its PATH saved each clip instead of playing it, so nothing sounded during the checks; the owner hears the saved clips. The scratch folders were deleted afterwards.

| Check | Result |
|---|---|
| kokoro-onnx on Python 3.14, CPU | model load 0.5–0.6 s; a 5–6 s line in 0.7–0.9 s (int8) or 1.1–1.3 s (fp32) |
| Play sample (worker not yet running) | the clip was ready 1.8 s after the click (worker start, model load, synthesis); 5.1 s long |
| A permission prompt through a real hook post | "Robot Arm Control needs you. It's asking for permission." plus a quip, 4.2 s after the event: the 3 s merge window, then synthesis |
| A finished turn (threshold set to 0), a tablet note, a reminder 40 s ahead | each spoken once; the reminder at its minute |
| Pause for 5 minutes, then a tablet note | silent; Resume brings the voice back |
| SIGTERM, then SIGKILL, of the hub mid-sentence (a player that sleeps 30 s) | no worker and no player left behind, after the fix in phase 4 decisions V32 |
| The voice download | the network was slow that evening (curl measured 38 KB/s), so the files were copied in; the download itself is tested against a local server, and the pins are the spike's files |

## Not built yet
- **Phase 5:** Google Calendar (OAuth tokens in shared `data/`, so it is authorised once).
- **Phase 6:** phone.
- **Any time:** semantic search; a file watcher for owner edits (phase 1 residual).

Project folders are already stored per OS, so those phases need no data migration.
