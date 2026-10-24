# ATLAS Phase 2: Widget, Usage meter, Agents panel (design)

Date: 2026-10-21
Status: written by Claude during autonomous continuation. The owner asked to "continue on with the planned phase 2" and approved the parent spec on 2026-10-20.
Parent spec: `2026-10-20-atlas-design.md`. It is the authority; this document only makes phase 2 concrete. Where they disagree, the parent spec wins, unless a ruling below says why not, and every such ruling is marked **(my call)**.

## 1. Scope

Phase 2 (parent spec §12, item 2) delivers:
1. **Usage meter** (spec §6): 5-hour and weekly % with reset times, an 80% warning, and a "Fable usage ↗" link.
2. **Agents panel** (spec §7.1–7.2): Claude Code sessions on this PC, fed by user-level hooks.
   - Each row shows project, part, where, status and the next-time flag (spec §4).
3. **Desktop widget** (spec §3.1): a Tauri v2 shell around a hub-served page.
   - Collapsed: a synopsis line. Expanded: tiles with popups, plus a capture box.
4. **Autostart, tray and self-healing** (spec §14). The Tauri shell starts at login, owns the tray icon, and restarts the hub if it crashes.
5. **Claude Code integration installer:** adds and removes ATLAS's hooks and status line in the owner's `~/.claude/settings.json`, backed up and reversible.

Not in phase 2:
- voice and notifications (phase 4); the tray's "pause notifications" arrives with them;
- the tablet AGENTS tab (phase 3);
- measurement requests and their `CLAUDE.md` line (phase 3);
- Google Calendar (phase 5);
- semantic search (any time).

## 2. Verified facts (recon, 2026-10-21, Claude Code 2.1.306, Windows 11)

Measured with a throwaway spike: a local HTTP sink, plus `claude -p --settings <temp file>`. The owner's settings were never touched.

| Fact | Result |
|---|---|
| Hooks in a `--settings` file fire | Yes. This is how tests and the e2e try hooks without touching `~/.claude/settings.json`. |
| `--setting-sources ""` stops project hooks | Yes. A project `.claude/settings.json` with hooks did not fire. The hub's own `claude -p` runs therefore don't report. |
| `--settings` still loads user plugins' hooks | Yes (superpowers' SessionStart ran). This doesn't matter for us. |
| `type: "http"` hooks | Exist. They POST JSON with `Content-Type: application/json` and no `Origin`. Header env interpolation works via `allowedEnvVars`. |
| HTTP hook on SessionStart | **Did not fire**, while a command hook on SessionStart did. |
| HTTP hook with the hub down | `outcome: "error"` (`connect ECONNREFUSED`), and SessionEnd printed `SessionEnd hook [...] failed` to stderr. **That breaks spec §7.2 ("must not ... show errors"), so HTTP hooks are not used.** |
| Command hook, exec form (`command` = exe, `args` = list, no shell) | Works. With the hub down, the script swallowed the error, exited 0, printed nothing, and the outcome was `success`. |
| `"async": true` command hooks | Work and don't block. Under `-p`, an async Stop hook was `cancelled` at teardown. Interactive sessions don't tear down like that, but Stop and SessionEnd are made synchronous anyway (§4.2). |
| `CLAUDE_CODE_ENTRYPOINT` in hook processes | Present but **undocumented**. It's `cli` in the terminal and `sdk-cli` for `claude -p`. Values for VS Code and the desktop app are unknown; the guess is `claude-vscode` and `claude-desktop`. |
| User-level settings in VS Code and desktop | The docs say the terminal, the VS Code/JetBrains extensions and the desktop app "all read the same settings files", and "Hooks ... defined in settings apply to both". Whether the **status line** runs in VS Code or the desktop app is **not documented**. |
| Status line `rate_limits` | `five_hour` and `seven_day` each carry `used_percentage` (0–100) and `resets_at` (Unix seconds). They're present for Pro/Max after the first API response. Each window may be absent, and a window is dropped once its `resets_at` passes. |
| Status line invocation | It runs at session start, after each assistant message and on other triggers, debounced to 300 ms, and an in-flight run is cancelled by a newer one. No timeout is documented: "Slow scripts block the status line". Commands run through Git Bash (installed here), or PowerShell without it. |
| Notification types | `permission_prompt`, `idle_prompt`, `elicitation_dialog`, `agent_needs_input`, `agent_completed`, and others. In VS Code and the desktop app, `permission_prompt` fires about 6 s after the request. |
| `StopFailure` event | Exists (rate_limit, overloaded, authentication_failed, …). Its stdin fields aren't documented. |
| Toolchain | Rust 1.99.1 MSVC (installed 2026-10-21 by rustup via winget, in `%USERPROFILE%\.cargo\bin`), VS 2022 Community with C++ tools, Windows SDK 10.0.26100, WebView2 155, Node 24, `@tauri-apps/cli` 2.12.3 (run via npx). |

## 3. Architecture

```
Claude Code session (terminal / VS Code / desktop)
   │ hook (command, exec form) ──► .venv python -I -S atlas/hook_client.py event --port P
   │ status line (shell)       ──► .venv python -I -S atlas/hook_client.py statusline --port P
   ▼                                 │ POST (1 s timeout, all errors swallowed, exit 0)
ATLAS hub (aiohttp, 127.0.0.1:P) ◄┘
   ├─ /api/hooks/event → agents.py  (AgentTracker, SQLite table `agents` in hub.db)
   ├─ /api/usage       → usage.py   (UsageStore, data/usage.json)
   ├─ /api/widget      → widget.py  (one summary for the widget)
   ├─ /widget          → web/widget.html (+ js/widget.js, js/widget-model.js, widget.css)
   └─ WebSocket events: agents.changed, usage.changed (plus phase 1's items.changed …)
Tauri shell "ATLAS" (app/desktop/src-tauri)
   ├─ frameless, transparent, always-on-top window → http://127.0.0.1:P/widget
   ├─ tray: Open ATLAS · Show/Hide widget · Start at login · Restart hub · Quit
   └─ supervisor: starts the hub hidden if it isn't answering; restarts it after a crash (backoff)
```

One hub process, as in phase 1. The widget is just another page of it. The Tauri shell holds no business logic.

## 4. Claude Code integration

### 4.1 `atlas/hook_client.py`

It has a stdlib-only mode, run as a file: `python -I -S hook_client.py ...`. `-I -S` keeps startup fast and ignores the environment.
- **`event --port P`:** reads the hook JSON from stdin and keeps only an allow-list of fields:
  - `hook_event_name`, `session_id`, `cwd`, `source`, `reason`, `notification_type`, `session_title`;
  - `message`, `title`, `error`, `error_type`: strings capped at 300 characters.
  - It adds `entrypoint` from the `CLAUDE_CODE_ENTRYPOINT` env var, then POSTs to `http://127.0.0.1:P/api/hooks/event` with a 1 s timeout.
  - **Prompts, tool inputs, tool responses and transcripts never leave the process.** That's for privacy, and it keeps bodies tiny.
  - Every exception is swallowed. It always exits 0 and never writes to stdout or stderr: stdout on SessionStart and UserPromptSubmit would be injected into the agent's context.
- **`statusline --port P [--passthrough-file F]`:** reads the status line JSON and POSTs `{"rate_limits": …, "session_id": …}` to `/api/usage` with a 0.5 s timeout, swallowing every error.
  - It then prints one line: the output of the owner's previous status line command, if one was saved at install (run through the shell with the same stdin, 2 s timeout); otherwise its own compact line, e.g. `Sonnet · 5h 62% ↻14:20 · 7d 31%`.
  - It exits 0.

### 4.2 Hooks installed (user `~/.claude/settings.json`)

Each hook is a separate matcher group whose single hook is `{"type": "command", "command": "<python.exe>", "args": ["-I", "-S", "<hook_client.py>", "event", "--port", "<P>"], "timeout": 5}`.
- `"async": true` goes **only** on `PostToolUse`, which fires on every tool call.
- `SessionStart`, `UserPromptSubmit`, `Notification`, `Stop`, `StopFailure` and `SessionEnd` are synchronous. They're short, and Stop and SessionEnd must not be lost at teardown. A downed hub refuses the connection instantly, so they cost about 60 ms (Python start-up).
- `<python.exe>` is the venv interpreter that ran the install (`sys.executable` → `…\.venv\Scripts\python.exe`). Paths are absolute.
- Ours are recognised by `hook_client.py` appearing in `args` (hooks) or `command` (status line).

### 4.3 Status line

`{"type": "command", "command": "<python.exe> -I -S <hook_client.py> statusline --port <P> [--passthrough-file <F>]"}`:
- Paths are converted to forward slashes. They're quoted only when they contain a space.
- If the owner already has a `statusLine`, its full JSON is saved in `data/claude_integration.json`, and its command goes into the passthrough file `data/statusline_passthrough.txt`. Uninstall restores it exactly.

### 4.4 Installer: `atlas/claude_integration.py`
- `install(settings_path, python, script, port, data_dir)`, `uninstall(...)` and `status(...)`.
  - Reads the file with a JSON parser. A missing file means `{}`. **Unparsable JSON refuses**, and nothing is written.
  - Install backs up the existing file to `settings.json.atlas-backup-YYYYmmdd-HHMMSS` before writing, then writes atomically (tmp + `os.replace`) with indent 2, keeping every other key untouched.
- Install is **idempotent**: it removes our previous entries first, then adds the current ones, so a port change is "install again".
- Uninstall removes only our entries and drops event keys that end up empty. It restores a saved previous status line, or removes `statusLine` if it is ours.
- Status reports `{hooks_installed: bool, events: [...], statusline_installed: bool, port_matches: bool, settings_path}`.
- Entry points:
  - CLI: `python -m atlas integration install|uninstall|status --root <root>`;
  - API: `GET /api/integration`, `POST /api/integration/install`, `POST /api/integration/uninstall`;
  - Settings view: a "Claude Code integration" section with status, the last hook event received, the last usage update, and Install/Remove buttons.
- The settings path defaults to `%USERPROFILE%\.claude\settings.json`. The `CLAUDE_CONFIG_DIR` env var overrides the folder, as Claude Code does. Tests always pass an explicit temp path.
- **Never install into the real user settings from tests or the e2e.**

## 5. Agents panel

### 5.1 Event → status (my call where it refines spec §11)

| Event | Effect |
|---|---|
| `SessionStart` | The row is created, or revived if it existed. Status `idle` ("ready"), unless the source is `resume`, `clear`, `compact` or `fork` and the row exists, in which case the status is kept. Branch, project and where are (re)computed. |
| `UserPromptSubmit` | `running`. Branch and title are refreshed. |
| `PostToolUse` | `running`, which clears a "waiting" once a permission is granted. It only writes when the status changes or 30 s have passed, so it's cheap. |
| `Notification` `permission_prompt`, `elicitation_dialog`, `agent_needs_input` | `waiting` (for you), with `note` = the message. |
| `Notification` `idle_prompt` | `waiting` only if the status is `running`, i.e. a missed Stop. A `finished` row stays `finished`. **(my call)** Spec §11 maps idle_prompt straight to waiting. idle_prompt fires about 60 s after every finished turn, so following the spec would turn every finished row amber a minute later. That's noise, and the anti-goal forbids nagging. `finished` already means "your turn". |
| other `Notification` types | Ignored. |
| `Stop` | `finished`. |
| `StopFailure` | `waiting`, with `note` = the error type (e.g. `rate_limit`). **(my call)** The turn died, so the owner must act. The spec has only three statuses. |
| `SessionEnd` | `ended_at` is set. The row disappears after `agents_end_linger_s` (default 60). |
| no event for `agents_stale_h` hours (default 12) | Hidden and pruned. This covers crashes that never send SessionEnd. |

Statuses: `idle`, `running`, `waiting`, `finished`. The UI labels are "ready", "running", "waiting for you" and "finished".

**Ignored events** (never create rows):
- `entrypoint` starting with `sdk-` (`claude -p` and SDK runs: not interactive sessions; **(my call)**);
- `cwd` inside the hub's data root (`<root>/data`, `<root>/brain`): belt and braces for the hub's own agents;
- a missing `session_id` or `hook_event_name` (400).

### 5.2 Row fields
`session_id` (PK), `cwd`, `project`, `project_name`, `mapped` (bool), `branch`, `title`, `part_override`, `entrypoint`, `where_override`, `status`, `note`, `started_at`, `status_since`, `last_event_at`, `ended_at`.
- **Project:** the brain-map project whose `folders` contain `cwd`. Matching is the longest prefix, case-insensitive, with normalised separators. Then `mapped = true` and the project name comes from the map. Otherwise it's the folder name of the git top-level (the folder holding `.git`), else the cwd's basename.
- **Branch:** read from `.git/HEAD` by walking up from cwd. A `.git` *file* (worktree) is followed via `gitdir:`. `ref: refs/heads/feat/ui-reskin` gives `feat/ui-reskin`. A detached HEAD gives `detached`. No git spawn.
- **Part** (shown): `part_override`, else `title` (session_title from `/rename`), else the simplified branch (the text after the last `/`: `feat/ui-reskin` → `ui-reskin`), else `—`. The override sticks for the session (spec §7.2).
- **Where:** `where_override`, else `cli` → "terminal", `claude-vscode` → "IDE", `claude-desktop` → "desktop app", anything else → "unknown". The raw entrypoint shows as a tooltip, so the owner can tell us real values. The mapping table lives in `agents.py`.
- **Next-time flag:** for mapped rows, the count of open `next_time` items with `project == slug`, e.g. "2 next-time notes for robotarm". It is **never injected into the agent** (the hook prints nothing).

### 5.3 API
- `POST /api/hooks/event` → 204 (empty body, always, including for ignored events; 400 only for malformed JSON).
- `GET /api/agents` → rows (prunes first).
- `PATCH /api/agents/{session_id}` with `{part?: str|null, where?: "terminal"|"IDE"|"desktop app"|"unknown"|null}`. `null` clears an override.
- `DELETE /api/agents/{session_id}` removes a stale row by hand.
- WebSocket `agents.changed` is sent when a row is created or removed, or its status, part, where or note changes. A 30 s background prune sends it when something expires.

## 6. Usage meter
- `POST /api/usage` with `{rate_limits: {five_hour?: {used_percentage, resets_at}, seven_day?: {...}}, session_id?}` → 204.
  - Each window is validated (number 0–100, integer seconds) and **merged per window**: a window missing from the payload keeps its last known value, because a new session sends none until its first API response.
  - The result is saved atomically to `data/usage.json` together with `updated_at`.
  - `usage.changed` is broadcast only when a value changed.
- `GET /api/usage` returns:
  - `{five_hour: W|null, seven_day: W|null, updated_at, age_s, warn_pct, fable_url}`;
  - `W = {used: pct|null, resets_at: ISO local, reset_passed: bool, last_used: pct, warn: bool}`;
  - when `resets_at` has passed, `used` is null ("reset since; no data yet") and `last_used` keeps the old value.
- UI:
  - **Usage view:** two meters with % and "resets 14:20 (in 2 h 5 min)", plus "updated 3 min ago" in grey once the age exceeds 5 min.
  - Warning style at ≥ `usage_warn_pct`.
  - A small "Fable usage ↗" link opens `fable_usage_url` through `/api/open`.
  - The same data is used on the widget's Usage tile and in the synopsis.

## 7. Widget

### 7.1 Page (`/widget`, served by the hub; works in any browser too)
- **Collapsed** (default, about 520×44 logical px): a drag handle `⋮⋮`, then the synopsis, then an expand button.
  - The synopsis follows spec §3.1, e.g. `3 due today · 1 waiting on Teammate A · 5h 62% (resets 14:20) · 2 agents running`. Parts with zero are left out; with nothing to show it says `All clear`.
  - An amber dot marks items waiting for the owner: proposals or failed filings to review, agents waiting, or usage ≥ warn.
- **Expanded:**
  - a one-line capture input, saved with source `widget` (Enter saves; Esc collapses);
  - a grid of tiles from the `widget_tiles` setting, in the order of spec §3.1: Today, Usage, Agents, People, Deadlines, To review, Projects, Robot arm.
  - Each tile shows a big number or state plus a one-line caption.
- Clicking a tile opens a **popup** inside the widget with that category's essentials (at most 6 rows) and an "Open in ATLAS ↗" link, which opens the full app at the matching view via `/api/open`. It's never a full page.
- **Robot arm tile:** "reachable"/"offline", from a TCP connect to `arm_url` (1 s timeout, cached 15 s). It opens the arm UI via `/api/open`.
- **Legibility over any background:**
  - always dark, a near-opaque panel (`rgba(16,17,19,0.94)`), a 1 px light border, 10 px radius, ≥ 13 px text, contrast ≥ 7:1;
  - no animations beyond a 120 ms expand fade; no blur.
- **Data:** `GET /api/widget` (one call). It's refetched on WebSocket `items.changed`, `agents.changed`, `usage.changed`, `filing.done`, `filing.failed` and `map.changed` (debounced 300 ms), and every 60 s for clock-based text.
  - When the hub can't be reached, it shows `ATLAS offline · retrying` and retries every 5 s.
- **Tauri bridge:** when `window.__TAURI__` exists, expand and collapse resize the window to fit the content (`getCurrentWindow().setSize(new LogicalSize(w, h))`, measured from `document.body`). The handle carries `data-tauri-drag-region`. In a normal browser, both are no-ops.

### 7.2 `GET /api/widget` (computed in `atlas/widget.py`; tested in Python)
```
{ synopsis, attention: bool, tiles: [names],
  today: {overdue, due_today, top: [{text, due}]},
  usage: <GET /api/usage shape>,
  agents: {running, waiting, finished, idle, rows: [top 6: project_name, part, status, where, next_time]},
  people: {waiting: [{person, name, text, since, nudge}], to_read: n},
  deadlines: [{text, due, project}] (next 6 by date),
  review: {proposals, failed},
  projects: [{slug, name, open, next_time}],
  arm: {url, reachable} }
```

### 7.3 `POST /api/open`
- Body: `{target: "app" | "arm" | "fable_usage", view?: str}`.
- Only these targets exist. `view` must match `^[a-z]+(/[a-z0-9-]+)?$`, and the URL becomes `http://127.0.0.1:<port>/#/<view>`.
- `arm` and `fable_usage` come from settings, and only `http(s)://` URLs are opened.
- The URL is opened with `os.startfile` on Windows (`webbrowser.open` elsewhere), behind an injectable `app["opener"]`. Tests replace it.

## 8. Desktop shell (`app/desktop/`)
- A Tauri v2 Rust app (`src-tauri/`), built with `cargo build --release`. No installer (spec §14). The exe is `app/desktop/src-tauri/target/release/atlas-desktop.exe`.
- Crates: `tauri` (with `tray-icon`), `tauri-plugin-single-instance`, `tauri-plugin-autostart`, `tauri-plugin-window-state` (position only), `serde`, `serde_json`.
- **Config:** `%LOCALAPPDATA%\ATLAS\desktop.json`, `{port, root, python, app_dir}`, written by `python -m atlas desktop-config --root <root>`.
  - That command uses `pythonw.exe` next to `sys.executable`, so the hub runs without a console.
  - Without the file, the shell assumes port 8090 and only connects; it never starts a hub.
- **Supervisor** (a thread):
  - every 2 s it checks for a TCP connect to `127.0.0.1:port`;
  - if nothing answers and a config exists and no child is alive, it spawns `pythonw -m atlas --root <root>` in `app_dir` with `CREATE_NO_WINDOW`. stdout and stderr are appended to `<root>/data/hub.log`, which also stops `print()` from crashing under pythonw.
  - If the child exits, it restarts it with backoff 1, 2, 4 … 60 s. The backoff resets after 5 minutes of uptime.
  - It never kills a hub it didn't start.
- **Window:** label `widget`; frameless, transparent, always-on-top, skip-taskbar, not resizable, no shadow; starts at 520×44.
  - It first shows the bundled `dist/index.html` ("Starting ATLAS…"), then navigates to `http://127.0.0.1:port/widget` once the port answers.
  - The position is remembered by window-state.
  - A capability with `remote.urls: ["http://127.0.0.1:*/*"]` grants the page `core:window:allow-set-size` and `core:window:allow-start-dragging` only.
- **Tray:**
  - Open ATLAS (default browser at the hub URL);
  - Show/Hide widget;
  - Start at login (check item, via the autostart plugin's HKCU Run key, off by default; the owner ticks it once);
  - Restart hub (enabled only for a hub it started);
  - Quit (stops the child hub it started, then exits).
- Single instance: a second launch shows the widget.

## 9. Settings (all editable in the Settings view)
| Key | Default | Validation |
|---|---|---|
| `usage_warn_pct` | 80 | int 1–100 |
| `fable_usage_url` | `https://claude.ai/settings/usage` | `https://` URL |
| `agents_end_linger_s` | 60 | int 0–3600 |
| `agents_stale_h` | 12 | int 1–168 |
| `arm_url` | `http://127.0.0.1:8080` | `http(s)://host[:port][/path]` |
| `widget_tiles` | `["today","usage","agents","people","deadlines","review","projects","arm"]` | a non-empty list of distinct known tile names; order kept |

The Settings view gains:
- a checkbox list for `widget_tiles`;
- hints: `port` notes that "changing it needs Claude Code integration re-installed";
- the integration section (§4.4).

## 10. Security and privacy
- Every new endpoint sits behind phase 1's `local_only` middleware (Host and Origin checks). POSTs require `application/json`; the hook client sends it.
- **Threat model** (same as phase 1): any local process can already reach the hub. A local process forging hook events only affects the Agents panel display.
- **Allow-listed payloads:** no prompts or tool data are stored or sent. `note` is capped at 300 characters.
- Every agent-supplied string reaches the DOM via `textContent` only (rule 6). That covers cwd, title, message and branch.
- The installer only writes the owner's settings file when the owner asks (CLI or button). It backs up first and refuses unparsable JSON.

## 11. Testing
- **Python:** real SQLite, files and temp dirs, plus an injectable clock (`now`).
  - `hook_client.py` is tested as a subprocess against an aiohttp test server and against a closed port: exit 0, empty stdout/stderr, ≤ 1.5 s.
  - The installer is tested on temp settings files, including invalid JSON, an existing status line and other hooks preserved.
- **JS:** `node --test` for `widget-model.js` (synopsis, time formatting, tile summaries) and `views/agents-model.js`.
- **Rust:** `cargo test` for backoff and config parsing; `cargo build --release` must succeed.
- **E2E** (scratch root, never `D:\ATLAS`):
  - run the hub;
  - run the real `claude -p --settings <temp settings with our hooks>` (with `CLAUDE_CODE_ENTRYPOINT=cli` forced in its env so the `sdk-` filter doesn't drop it);
  - see the row appear, then finish, then end;
  - POST a captured status line payload and see usage;
  - launch the built desktop exe against the scratch hub and screenshot it.

## 12. Owner verification (needs the owner's machine and eyes, after merge)
1. Run `python -m atlas integration install --root D:\ATLAS`, or press Install in Settings.
2. Start a terminal session: a row "terminal" should appear, then running/finished.
3. Start a VS Code extension session. Does a row appear, and what does Where say? If it says "unknown", report the tooltip's raw entrypoint.
4. Start a desktop app local Code session: same check.
5. Open Claude Design in the desktop app. If no row appears, it stays excluded (spec §7.1). Nothing is scraped.
6. Check whether the Usage view updates from VS Code or desktop sessions. The status line may only run in the terminal. If so, usage updates only while a terminal session is active; that is acceptable (spec §11 gap).
7. Run `desktop-config`, launch `atlas-desktop.exe`, tick "Start at login", reboot, and check that the widget and hub come up.

## 13. Left to the builder
Tile visuals within §7.1's constraints, popup layout, exact synopsis wording beyond the example, and the tray icon art (reuse the generated Tauri icon set for now; it gets reskinned with Claude Design later, spec §15).
