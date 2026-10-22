# BrainHub (phases 1 and 2)

BrainHub is a local second brain. You type free text; a headless Claude Code agent files it into a git-versioned Markdown `brain/` and *proposes* tasks, which you confirm. Recall is instant local search plus a cited answer.

- **Capture:** raw entries go into an append-only log first, then the agent files them into notes and proposes items (next-time, deadline, reminder, waiting, to-read, task, new people/projects).
- **Review:** nothing becomes an item until you confirm it. Today, project, people and deadline views show the confirmed items.
- **Recall:** search hits appear at once; Claude answers from them with citations, or says `Nothing on that.`
- **Phase 2:** a usage meter (your Claude 5-hour and weekly limits), an Agents panel (your Claude Code sessions on this PC), a desktop widget, and a desktop shell with a tray icon, start at login and hub self-healing. See [Phase 2](#phase-2-usage-meter-agents-panel-widget-desktop-shell).

## Requirements
- Windows, Python >= 3.12, git
- The Claude Code CLI (`claude`) installed and **logged in** with your subscription
- Only to build the desktop shell: Rust (MSVC) and the Visual Studio 2022 C++ build tools, see [`desktop/README.md`](desktop/README.md)

## Install
From `D:\BrainHub\app`:

```
py -3 -m venv .venv
.venv/Scripts/python -m pip install -e ".[test]"
```

## Run
```
.venv/Scripts/python -m brainhub --root D:\BrainHub
```
Then open <http://127.0.0.1:8090>. `--port N` overrides the port (default 8090, setting `port`); the hub then uses that port everywhere (Open in BrainHub, the integration install). If the port is already taken (say, a second hub on the same root), it stops at once with `port N on 127.0.0.1 is already in use`, before any startup work. Once phase 2 is set up, the desktop shell starts the hub for you (see [Desktop shell](#desktop-shell)).

## First run
The setup wizard asks for your map: areas, projects (and which are companies, with their folders), courses and people, plus quiet hours and an optional backup remote. Finishing it creates the `brain/` git repo (commit `setup`). You can edit the map later in Settings.

## Where data lives
- `brain/`: your Markdown notes, its own git repo. `log/` holds the verbatim raw entries, `_items/` the confirmed items (`items.json`) and the proposals still waiting for you (`proposals.json`), `map.json` the map. Every filing, confirmation and item edit is a commit (a filing is `file <entry id>: <synopsis>`); your own edits are committed as they are ("owner edits"). The agent never modifies `log/`, `_items/`, `map.json` or any dot-file.
- `data/hub.db`: the search index and filing state. It is a rebuildable index: if it is lost or damaged, the next start rebuilds it from `brain/` (entries filed before are recognised from their `file <entry id>:` commits and are not filed again; pending proposals come back from `_items/proposals.json`; if more than 10 entries turn out never to have been filed, they wait in To review for you to press Retry instead of all being filed at once). Phase 2 adds its `agents` table (Agents panel rows, which expire on their own).
- `data/settings.json`: your preferences and the backup URL. Keep it; it is not in `brain/`.
- `data/worktrees/`: temporary agent checkouts, removed after each filing.
- Phase 2:
  - `data/usage.json`: the last known usage figures.
  - `data/claude_integration.json` and `data/statusline_passthrough.txt`: written while the Claude Code integration is installed; they remember the status line you had before, if any.
  - `data/hub.log`: output of a hub started by the desktop shell, plus the shell's own lines.
- Outside the root: `%LOCALAPPDATA%\BrainHub\desktop.json` (desktop shell config), and the shell's window position and WebView2 data (see `desktop/README.md`).

`brain/` and `data/` are git-ignored by the code repo.

## Backup
Create an **empty, private** GitHub repository, then paste its URL in Settings (backup remote). The hub pushes `brain/` shortly after commits (debounced, 5 minutes by default). Push status shows in the Settings view; "Push now" forces it. A push never asks for a password: git runs without a terminal prompt or credential-manager window and gives up after 120 s, so sign in to GitHub once with git (or use an SSH key without a passphrase prompt) before relying on it.

`brain/.gitattributes` (`* -text`) keeps line endings exactly as written, so a restore with `git clone` gives a readable raw log even where git converts line endings by default.

## Claude and `ANTHROPIC_API_KEY`
All Claude calls run `claude -p` on your subscription. `ANTHROPIC_API_KEY` is removed from the environment of every child process **on purpose**, and `--bare` is never used, so you are never billed per token by accident. Models are settings: filing `sonnet`, quick recall `haiku`, deeper recall `sonnet`.

The hub's agents run isolated from your own Claude Code setup: they load no settings files (`--setting-sources ""`), so none of your plugins, hooks or permission rules apply; no MCP servers (`--strict-mcp-config`); no skills or slash commands; no saved sessions; and only the built-in tools each job needs (`--tools`: none for quick recall, `Read Glob Grep` for dig deeper, `Read Write Edit Glob Grep` for filing). Your login still works, since it is not a setting. Because they load no settings, the hub's own agents never fire BrainHub's hooks, so they never show up in the Agents panel.

## Security
The server binds `127.0.0.1` only. Writes require `Content-Type: application/json` and a same-origin `Origin`/`Host`, so cross-site requests from other web pages are blocked. The UI renders text with `textContent` only.

Phase 2 keeps the same model. Any local program can already reach the hub, so a program forging hook events could only change what the Agents panel shows. The hook client sends an allow-list of fields (event name, session id, cwd, source, reason, notification type, session title, and messages capped at 300 characters): never your prompts, tool inputs or outputs, or transcripts. `/api/open` only opens the app, the arm URL or the usage URL from your settings.

## Phase 2: usage meter, Agents panel, widget, desktop shell

### Claude Code integration
The usage meter and the Agents panel are fed by your own Claude Code: hooks report session events, and the status line reports your rate limits. Both run `hook_client.py`, a tiny standard-library script that posts to the hub and **never prints anything or fails visibly**: with the hub down it gives up after about 0.3 s and exits quietly.

Install it once (it writes your user settings, so only when you ask):
- **Settings view → "Claude Code integration" → Install**, or
- `.venv\Scripts\python -m brainhub integration install --root D:\BrainHub`

Remove it with **Remove** in the same section, or `... integration uninstall --root D:\BrainHub`. `... integration status --root D:\BrainHub` prints what is installed. `--settings PATH` targets another settings file (for tests). The Settings section also shows the last hook event and the last usage update the hub received, and whether the installed port matches.

What it changes in `%USERPROFILE%\.claude\settings.json` (or `%CLAUDE_CONFIG_DIR%\settings.json`):
- Under `hooks`, one entry for each of `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Notification`, `Stop`, `StopFailure` and `SessionEnd`. Each runs `<app>\.venv\Scripts\python.exe -I -S <app>\brainhub\hook_client.py event --port <port>` directly (no shell), with a 5 s timeout. `UserPromptSubmit`, `Notification` and `PostToolUse` are async so they never hold you up; the others are synchronous so they are not lost when a session closes.
- `statusLine` runs `hook_client.py statusline --port <port>` and prints a compact line such as `Sonnet · 5h 62% ↻14:02 · 7d 31%`. If you already had a status line, it is saved in `data/claude_integration.json`, keeps running (its output is shown instead of ours), and is put back exactly on Remove.
- Every other key, and every hook of your own, is left as it was. Before each write the file is copied to `settings.json.brainhub-backup-YYYYmmdd-HHMMSS` next to it, and the write is atomic. A file that isn't valid JSON, or has an unexpected shape, is refused and left untouched.
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

- **Project:** the map project whose folders contain the session's folder (set project folders in Settings → map), otherwise the git repository's folder name. **Part:** your rename, else the session title (`/rename`), else the branch without its prefix (`feat/wrist-servo` → `wrist-servo`). **Where:** terminal, IDE (VS Code), desktop app, or unknown (hover to see the raw value Claude Code reported). **Next-time flag:** "2 next-time notes for Robot arm" (mapped projects only); it is shown to you only and never sent to the agent.
- Click a part to rename it, change Where from its menu, or remove a stale row (✕). Edits stick for the session.
- A row disappears `agents_end_linger_s` seconds after its session ends, or after `agents_stale_h` hours without events (a crashed session).
- `claude -p` and SDK runs (entrypoint `sdk-…`) and anything running inside the hub's own `data/` or `brain/` never appear.

### Widget
`http://127.0.0.1:<port>/widget` is a small always-dark panel; it works in any browser, and the desktop shell shows it in a frameless always-on-top window.
- **Collapsed:** one line, e.g. `3 due today · 1 waiting on Teammate A · 5h 62% (resets 14:20) · 2 agents running`, or `All clear`. An amber dot means something waits for you: proposals or failed filings to review, an agent waiting, or usage at or above the warning level.
- **Expanded** (the ▾ button): a capture box (Enter saves, with source `widget`; Esc collapses) and tiles: Today, Usage, Agents, People, Deadlines, To review, Projects, Robot arm (`widget_tiles` picks and orders them). A tile opens a popup with up to 6 rows and "Open in BrainHub ↗". The Robot arm tile says reachable or offline from `arm_url`.
- It refreshes on hub events and every 60 s; with the hub down it says `BrainHub offline · retrying`, and it reconnects by itself when the hub is back (the desktop shell doesn't reload it).
- What you type in the capture box is kept as a draft in the page's local storage until it's saved. If the page reloads, the widget opens expanded with your text back in the box.

### Desktop shell
`app/desktop/` is a Tauri v2 shell, "BrainHub Desktop": the widget window, the tray icon, start at login, and a supervisor that starts the hub (hidden, no console windows) if it isn't running and restarts it after a crash. It holds no logic of its own. Full instructions: [`desktop/README.md`](desktop/README.md). In short:
1. Build once: in `app\desktop\src-tauri`, `cargo build --release` (about 5 minutes the first time) gives `target\release\brainhub-desktop.exe`.
2. Configure: `.venv\Scripts\python -m brainhub desktop-config --root D:\BrainHub` writes `%LOCALAPPDATA%\BrainHub\desktop.json` (`port`, `root`, `python` = the venv's `python.exe`, `app_dir`). The shell starts the hub with `--port <port>` from this file and without a visible console (the hidden console it gets is shared by the git and claude processes the hub runs, so none of them flashes a window). After changing the port, re-run it **and restart BrainHub Desktop**: the shell reads the file only at start.
3. Run `brainhub-desktop.exe`. Tray: Open BrainHub, Show/Hide widget, Start at login, Restart hub (only for a hub the shell started), Quit (stops that hub too).
4. **Start at login** (tray, off by default) adds the exe to `HKCU\...\CurrentVersion\Run`; untick to remove.
5. Logs: `<root>\data\hub.log` (hub output and the shell's `[brainhub-desktop t=…]` lines).

### Phase 2 settings
All in the Settings view.

| Setting | Default | What it does |
|---|---|---|
| `usage_warn_pct` | 80 | Warning style and the amber dot at or above this % (1–100). |
| `fable_usage_url` | `https://claude.ai/settings/usage` | Opened by "Fable usage ↗" (`https://` only). |
| `agents_end_linger_s` | 60 | Seconds an ended session stays in the Agents panel (0–3600). |
| `agents_stale_h` | 12 | Hours without events before a row is dropped (1–168). |
| `arm_url` | `http://127.0.0.1:8080` | The robot arm UI: the widget's Robot arm tile checks and opens it. |
| `widget_tiles` | all eight, in order | Which tiles the expanded widget shows, and their order. |

### Owner verification checklist
These need your machine and eyes, after merging phase 2:
1. Run `python -m brainhub integration install --root D:\BrainHub`, or press Install in Settings.
2. Start a terminal session: a row with Where "terminal" should appear, then go running and finished.
3. Start a VS Code extension session. Does a row appear, and what does Where say? If it says "unknown", report the raw entrypoint from its tooltip.
4. Start a desktop app local Code session: same check.
5. Open Claude Design in the desktop app. If no row appears, it stays excluded (spec §7.1). Nothing is scraped.
6. Check whether the Usage view updates from VS Code or desktop sessions. The status line may only run in the terminal; if so, usage updates only while a terminal session is active, which is acceptable (spec §11).
7. Run `desktop-config`, launch `brainhub-desktop.exe`, tick "Start at login", reboot, and check that the widget and hub come up.

## Tests
From `D:\BrainHub\app`:
```
.venv/Scripts/python -m pytest -q
node --test web/js/components/*.test.mjs web/js/views/*.test.mjs web/js/*.test.mjs
```
From `D:\BrainHub\app\desktop\src-tauri` (with `%USERPROFILE%\.cargo\bin` on `PATH`):
```
cargo test
cargo build --release
```
The Python tests use a fake Claude runner; none of them call the real CLI. They point `CLAUDE_CONFIG_DIR` and `LOCALAPPDATA` at temp folders, so they never touch your Claude settings or `desktop.json`.

## Known limitations
Deferred on purpose; none of them loses data.
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
- Whether hooks fire, and what Where says, in VS Code and desktop-app sessions is not verified yet (checklist items 3–5); whether the status line runs there is not documented (item 6).
- Usage only updates while a Claude Code session with the status line runs.
- After changing `port`: restart the hub, install the integration again, re-run `desktop-config` and restart BrainHub Desktop. Until then a shell-started hub stays on the old port (the shell passes `--port`), so everything keeps working on the old one.
- An event after a session's `SessionEnd` (other than a new start) doesn't revive the row, so it still disappears after the linger. Ignorable events still bump a row's "last event" time, which can change the order.
- Backups of `settings.json` made by Install/Remove are never pruned; delete old ones by hand.
- If `data/claude_integration.json` is unreadable when you install again, the saved previous status line is lost (it is still in the backups).
- The status line can take up to about 2.75 s (4.5 s worst case) when a saved previous status line command hangs.
- A desktop shell killed hard leaves the hub it started running; the next launch uses it, but "Restart hub" is then greyed out.
- `data/hub.log` grows without bound.
- The widget is kept inside the screen's work area when it is built and whenever it resizes, not while you drag it: a widget dragged partly off-screen stays there until it next expands or collapses.
- The desktop exe keeps WebView2 data in `%LOCALAPPDATA%\dev.brainhub.desktop` and the window position in `%APPDATA%\dev.brainhub.desktop\.window-state.json` even when `LOCALAPPDATA` is overridden.
- The capture draft lives in the widget page's local storage, which belongs to `127.0.0.1:<port>`: after a port change an unsaved draft from before stays behind with the old port.

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
| Hub started by the shell | `python.exe -m brainhub --root <scratch> --port 8099` (console python, not pythonw), with a hidden `conhost` |
| Setup over HTTP (startup git commits), then two captures filed by the real `claude` | 3 brain commits and 2 filings; a 40 ms poll of visible top-level windows saw no console, Terminal, git or claude window at any point |
| Draft in the capture box, then the hub killed and restarted by the shell | the page was not reloaded (same `performance.timeOrigin`), still expanded, text intact |
| Forced page reload with a draft | came back expanded with the text; Enter saved it and cleared the draft |
| Widget moved below the work area, then expanded | moved up so its bottom sits on the work area's bottom edge |
| Settings port changed to 8098 without `desktop-config` | the restarted hub still listened on 8099 (the shell's `--port`) |
| `desktop-config` re-run, shell restarted | hub started with `--port 8098`, widget on `127.0.0.1:8098/widget` |
| A second hub by hand on the taken port | exited in 0.26 s with `port 8098 on 127.0.0.1 is already in use`, no startup work |

## Not in phase 2
- **Phase 3:** tablet (and its AGENTS tab), LAN binding and pairing, measurement requests.
- **Phase 4:** voice, and the tray's "pause notifications" with it.
- **Phase 5:** Google Calendar.
- **Phase 6:** phone.
- **Any time:** semantic search; a file watcher for owner edits (phase 1 residual).

Phase 1 already stores `quiet_hours` and project `folders` so those phases need no data migration.
