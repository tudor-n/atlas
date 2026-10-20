# BrainHub — Design

Date: 2026-10-20
Status: approved by owner (2026-10-20)

## 1. Intent

One person, an engineering student, runs several things at once: uni, a three-person hackathon team (the owner plus two teammates they hand tasks to), a robotics project (a 6-DOF robot arm), their own small company, and a personal life. Things get lost when they switch context:

- a teammate's update arrives and never gets read;
- "check X next time on robotics" is forgotten by the time robotics comes round again;
- a plan for the company ("do X", "talk to Y") gets buried;
- homework is remembered at the last minute;
- personal things (a present for their girlfriend) are either forgotten or looped in their head all day so they won't be.

BrainHub is a personal, local "second brain". The owner writes freely about what they did and what they have to do. A Claude Code agent files it into a structured Markdown brain, pulls out tasks and dates, shows what it extracted, and only acts on it once the owner confirms. Later, the owner can ask about anything they wrote, and the answer must be accurate to what they said, however long ago.

Around that core sit glanceable surfaces: a floating desktop widget, a full PC app, and a workbench tablet. These show what's due, what's waiting, how much Claude plan usage is left, and which Claude Code agents are running or need attention.

It is also a project the owner wants to build: a closed, local ecosystem they own.

**Success:** the owner trusts it enough to stop holding things in their head, and capturing a thought is faster than paper.

## 2. Goals, non-goals, anti-goals

**Goals**
- Capturing a thought takes seconds, on the PC or the tablet.
- Everything written is kept and can be recalled accurately, with the source quoted.
- Nothing extracted becomes a task, reminder or date until the owner confirms it.
- One glance shows: due today, waiting on others, plan usage, and agent status.
- At the workbench, an agent's measurement request shows up on the tablet and the typed answers land on the PC.
- Everything runs locally on the owner's PC. Claude runs through the owner's Claude subscription via Claude Code. No API key.

**Non-goals (for now)**
- Voice input or speech recognition of any kind. The owner has built two such agents and found calibration frustrating.
- Live camera or any continuous "eyes".
- A conversational voice assistant.
- Reading WhatsApp or Discord messages automatically.
- Tracking claude.ai web sessions (Chrome) in the Agents panel. Claude Design in the desktop app is tracked only if it can be detected reliably (§7.1).
- Multi-user use. It's for one person.

**Anti-goals: it has failed even if it technically works when…**
- It is more of a chore than paper, memory or a plain Claude chat. **This is the owner's stated reason they would abandon it.**
- It is over-complicated, flashy or unintuitive. Every screen must beat paper on speed.
- It nags: noisy notifications, chatty voice, or animations without purpose.
- It ever answers a recall question with something the owner didn't say, or silently drops something they wrote.

## 3. Surfaces and what each holds

| Surface | Role |
|---|---|
| **PC full app** ("mothership") | Everything: Today, brain and projects, journal, confirm cards, To review, People, Deadlines, Tablet notes, Usage, Agents, settings. |
| **PC desktop widget** | A transparent, always-on-top, draggable floating widget. Collapsed: a one-line synopsis. Expanded: a grid of small app-like tiles, one per category. Clicking a tile pops up that category's essentials, with a link into the full app. |
| **Workbench tablet** | Samsung Galaxy Tab 3 Lite SM-T110 at the electronics/mechanical bench: Tablet notes, a Today glance, Agents + Usage glance, and a ROBOT mode that opens the existing arm control UI. |
| **Phone** | Low priority. Reminders arrive through Google Calendar (phase 5). Phase 6 adds offline capture that syncs only when on the home network (see §10). |

### 3.1 PC widget
- Collapsed synopsis, e.g. "3 due today · 1 waiting on Teammate A · 5h 62% (resets 14:20) · 2 agents running".
- Tiles: **Today, Usage, Agents, People, Deadlines, To review, Projects, Robot arm**.
  - There is no separate Inbox (my call): **To review** holds unconfirmed items and failed filings.
  - Phone captures (phase 6) arrive as ordinary entries and go through the same confirm card.
  - The Robot arm tile shows whether the arm server is reachable and opens its UI.
- Clicking a tile shows a small popup with the essentials. It is never a full page.
- A voice on/off toggle is reachable from the widget (§8).
- It must stay readable over any desktop background. Transparency must never cost legibility.

### 3.2 PC full app
- **Today** is the heart: due today and overdue, waiting on someone, "you said you'd do", and items awaiting confirmation.
- **Capture box** on every screen.
- **Confirm cards** (§5.3).
- **Projects:** one page per project area, showing notes, open items, "next time you open this" items and that project's journal entries.
- **People:** what was delegated to whom, since when, and status **waiting / to read / done** (my call). Nothing reads WhatsApp or Discord.
  - The owner captures "Teammate A sent the slides", and the confirm card proposes moving that delegation to **to read**.
  - On the phone (phase 6), sharing a message into capture does the same.
  - Follow-up nudge after N days with no update.
- **Deadlines:** dated items with lead-time reminders.
- **Journal:** everything ever written, by date, searchable and filterable by area.
- **Tablet notes:** everything typed on the tablet, newest first.
- **Ask:** a recall question box (§5.4).
- **Usage** and **Agents** in detail (§6, §7).

### 3.3 Workbench tablet
Hard constraints, taken from the existing arm project's tablet handoff (`D:\RobotArmControl\design_handoff_tablet_ui\README.md`):
- The device is 1024×600 CSS px landscape at DPR 1, running Android 4.2 old WebKit inside **Fully Kiosk Browser**, with 1 GB RAM.
- **ES5 only.** Fixed absolute layout. No CSS grid, gap, flex-wrap, CSS variables, shadows, gradients, blur, transparency or animations. Border-radius ≤ 4px. Hit targets ≥ 48px.
- **Visual language matches the arm UI ("ARM-6"):**
  - black background, white ink, greys `#8a8a8a / #5c5c5c / #262626 / #3a3a3a`;
  - green `#30d158` = live/healthy/done, amber `#ffb020` = attention, red `#d92d20` reserved;
  - Roboto / Droid Sans, with Droid Sans Mono for numbers.
- The tablet cannot record audio and should not be asked to.

**Home screen and modes (owner's requirement):**
- The tablet opens to a **home screen with two large, unmistakable choices: ROBOT and JOURNAL.** Switching modes must never need searching, and must never happen by accident.
- My calls to meet that:
  - Switching modes always goes through the home screen. There are no swipe gestures and no mode switch hidden in a tab row.
  - Leaving a mode takes a deliberate action (a long press or a double tap on a clearly labelled HOME control).
  - That HOME control is placed far from the arm UI's fixed STOP button (top right), so the two can't be confused.
- **ROBOT** opens the existing arm UI. Getting back needs a small "HOME" link added to the arm UI, which is now a **requirement**, not optional (§9).
- **JOURNAL** has tabs **TODAY · NOTES · AGENTS**.

Journal mode:
- **NOTES** (the reason the tablet exists):
  - When an agent has posted a measurement request (§7.3), it appears as a checklist with a big input per item, e.g. "ArUco ID 3 → arm base", "ID 3 → ID 7".
  - The owner types the value and unit together ("412 mm"); units are not locked.
  - There is also a free text box for anything else.
  - Everything submitted lands in the PC app under **Tablet notes**. **Agents do not consume it automatically.** The owner reads it and decides what to do.
- **TODAY:** a glanceable list.
- **AGENTS:** agent status and usage meters.
- **IGNORE** on each measurement request and each tablet note, for things that are out of context or no longer needed (owner's requirement).
  - It needs a **double tap to confirm**: the first tap turns the button amber with "TAP AGAIN", reverting after 2 seconds.
  - Ignored items are hidden, not deleted. They stay in the raw log and can be restored from the PC app.

Robot mode navigates to the existing arm server UI (`http://<pc>:8080`). The arm project is already finished and is not rebuilt; BrainHub only links to it.

## 4. The brain (data)

Code and data live apart, under `D:\BrainHub\`:
- `D:\BrainHub\app\`: the code.
- `D:\BrainHub\brain\`: the data. Plain Markdown that the owner and any Claude Code session can read directly.

Proposed brain layout (the builder may refine it, but the top-level split is the owner's):
```
brain/
  log/2026/10/2026-10-20.md   raw entries, verbatim, timestamped, append-only
  general/
  projects/<name>/             e.g. tracegreen, robotarm, vibedine, hackathon
  uni/
  personal/
  people/
  tablet-notes/
```

**The raw log is the source of truth.** Every entry is written to it **verbatim, before any agent runs**, and it is never rewritten. The organised files are derived from it, and every derived note links back to the raw entry it came from.

**Structured items** (tasks, to-dos, deadlines, reminders, waiting-on-person, next-time-on-project) need a state the UI can query (open / done / snoozed, due dates). The storage format is left to the builder. Requirements:
- A Claude Code session opened in `brain/` can read current items as plain text (e.g. a generated `items.md` per area).
- Every item links to its source entry.

**Who edits `brain/` (my call):**
- The hub owns item state. Items are ticked and edited in the UI, and the generated `items.md` views are read-only.
- The owner may hand-edit note files. The hub detects outside changes and commits them as they are, never overwriting them.
- Other Claude Code sessions only **read** `brain/` in v1.

**Next-time items (my call):** "check X next time on robotics" shows in that project's page and in Today under "Next time".
- When a Claude Code session starts in a folder mapped to that project (via the `SessionStart` hook's `cwd`, using a folder→project map in settings, e.g. `D:\RobotArmControl` → robotarm), the widget and Agents row flag "2 next-time notes for robotarm".
- They are **not** injected into the agent's context. The owner was wary of cluttering agent context.

**New projects/people (my call):** if filing finds an area, project or person the brain doesn't know yet, it is proposed in the confirm card like any item. The agent never creates one silently.

**Versioning (my call):** `brain/` is a git repository, and every filing and every confirm is a commit. A bad filing is then always reversible, and history is free.

## 5. The core loop

### 5.1 Capture
- A single free-text box on the **PC app and the widget**.
- Mixed content is fine ("did X on robotics, need to buy present before the 14th, Teammate A owes me the API").
- **Tablet text is not a capture** (owner's decision, §3.3): it goes to Tablet notes and is **not** filed by the agent. It is still written to the raw log, tagged `tablet`, so Recall can find it. No confirm card ever appears on the tablet.

### 5.2 Filing (agent)
- After saving to the raw log, the hub runs Claude Code headless (`claude -p`) with `brain/` as the working directory.
- The agent's job:
  - append or merge the content into the right Markdown files;
  - propose extracted items (kind, text, area/project, date/time, person, lead time).
- The agent must return the proposed items in a structured format the hub can render. The format is up to the builder.
- File edits may land immediately, since they're reversible via git. **Items stay "proposed" until confirmed.**
- Each entry uses a small amount of plan usage. The usage meter will show it, and that's accepted.

### 5.3 Confirm card
After each filing, a simple card shows:
- **Filed to:** the files it touched, plus a 1–2 line synopsis of what was recorded.
- **Extracted:** one row per item, stacked vertically, showing what, area/project, and date/time or "next session" or "waiting since".
- Per row: **✓** accepts, **✎** edits inline (text, date, project, kind), and dismiss.
- **Confirm all**, and a **"Tell it what's wrong"** text box (e.g. "the present is for the 12th"). That text re-runs filing with the correction and shows a fresh card.
- Items not confirmed stay in a **To review** pile, visible in Today and the widget. Nothing is lost if the owner walks away.

### 5.4 Recall
- The owner asks in plain words ("what did I say about the wrist servo?").
- The agent answers **only from what's written**.
- **Performance design (owner-approved):**
  1. **Instant local search first.** The hub keeps a full-text index (SQLite FTS5) of the raw log and the brain notes, updated on every write. The matching entries, with dates and highlighted terms, appear **immediately** (milliseconds), before Claude does anything.
  2. **Fast answer in one round.** The top matches go to Claude in a single `claude -p` call: no tools, no agent loop, streamed output, on a fast model for recall (the model is configurable in Settings). The target is a written answer within a few seconds.
  3. **"Dig deeper" button.** Only on request, it runs the full agentic search over `brain/` (Claude Code reading and searching files itself). It's slower and costs more usage.
  4. **Semantic search, added later.** A small local embedding model on the CPU (no API) indexes the same text, so that "wrist servo" finds "J5". Results merge with the full-text hits. It slots in behind step 1 without changing the rest.
- **Cost:** recall time is dominated by Claude, not search. Search stays fast for years of notes (tens of MB of text).
- **Every claim cites its source entry with date and quote**, and links to it.
- If nothing matches, the answer is "nothing on that". It never guesses or fills in.
- Accuracy must hold however large the brain grows or however old the entry. Raw-log quoting is the backstop for that.

## 6. Usage meter

The owner checks claude.ai → Usage repeatedly and wants it gone. Show only:
- the **5-hour window**: % used and reset time;
- the **weekly limit**: % used and reset time;
- **no Fable meter** (owner's decision): only a small "Fable usage ↗" link that opens claude.ai's usage page in the browser. The owner checks it rarely.

There's a warning state at 80% or more. It appears in the widget, the full app and the tablet AGENTS tab.

Data source: see §11 (recon result).

## 7. Agents panel

### 7.1 What's tracked
- Claude Code sessions on this PC: **terminal CLI, VS Code extension, and the desktop app's local Code sessions**.
- **Claude Design in the desktop app, if possible** (owner's note). It probably runs on claude.ai's servers rather than as a local Claude Code session, in which case hooks can't see it. Phase 2 checks this. If it can't be detected reliably, it stays excluded; it is never scraped.
- claude.ai web sessions (Chrome) are excluded; the owner watches those themselves.

### 7.2 Row contents
| Field | Source |
|---|---|
| Project | the session's repo/folder name (e.g. `robotarm`) |
| Part | the git branch name, simplified (`feat/ui-reskin` becomes "ui-reskin"); **one-click rename in the hub, and the rename sticks for that session** |
| Where | terminal / IDE / desktop app; "unknown" when it can't be told, editable |
| Status | **running / waiting for you / finished** |

Status comes from **Claude Code hooks** configured in the owner's user settings (§11). The hooks report to the local hub. They must be fire-and-forget with a short timeout: if the hub is down, Claude Code sessions must not slow down or show errors.

### 7.3 Measurement requests (agent → tablet)
A Claude Code agent working on something (e.g. arm calibration) can post a checklist request to the tablet's NOTES tab through a tiny local command or endpoint.
- The owner's user-level `CLAUDE.md` gets **one short line** describing it, kept short so it doesn't clutter agent context.
- Answers land in Tablet notes (§3.3), not back in the agent.
- Requests can be dismissed with the double-tap **IGNORE** (§3.3), which hides them rather than deleting them.

## 8. Notifications and voice

**Where notifications show:** the widget (badge/synopsis), the full app and the tablet while the laptop is on, and the phone through Google Calendar at all times (§8.1). Quiet hours apply to every audible channel.

### 8.1 Phone reminders via Google Calendar (owner's decision; low priority, phase 5)
The laptop and tablet come first; Google Calendar and the phone come after them. **Until phase 5, reminders only reach the owner while the laptop is on** (Today, widget, voice). This is accepted.
- When the owner **confirms** an item that has a date or time, the hub creates an event on a dedicated **"BrainHub"** Google Calendar, with an alert at the item's lead time.
- The phone's own calendar app fires the alert, even offline and even with the laptop off.
- Editing, snoozing or completing the item updates or removes the event. Changes made while offline are queued and synced when the laptop is next online.
- The hub uses its **own** Google Calendar API OAuth access, authorised once in the setup wizard. The claude.ai Google Calendar connector serves Claude chats, not the hub.
- **(My call)** The hub may also **read** the owner's other calendars, read-only, so existing schedule items (lectures, meetings) appear in Today. Configured in Settings.
- Unconfirmed items never reach the calendar.

**Voice (Kokoro TTS, local, offline):**
- Plays through the PC speakers.
- **Voice on/off toggle**; when it's off, nothing is ever spoken.
- It speaks only for:
  1. an agent **finished** or **needs the owner**;
  2. a **reminder is due**;
  3. a **readback of what was just typed on the tablet** ("ID 3 to ID 7, 412 millimetres. Saved.").

**Personality ("sass"), without AI:**
- Pre-written quotes per event, randomised, with no repeats until the pool has cycled.
- **At least 100 quotes in total** (owner's requirement), spread across the event types and written once when the feature is built.
- Templates filled with real values (project name, time running, days left) so they feel smart.
- The quotes live in a plain text file the owner edits.

Rules (confirmed by the owner):
- The information comes first and the quip after.
- Sass on readbacks is **configurable, and off by default**.
- Sass has its own toggle, separate from voice.
- Quiet hours (default 23:00–08:00, editable).

**Everything here is modifiable in Settings (owner's decision):** voice on/off, sass on/off, which events speak, Kokoro voice choice, speed and volume, quiet hours, and editing the quote pools. More generally, every default in this document that a person might want to change (lead times, nudge delay, the 80% usage warning, tile set) is exposed in Settings, not hardcoded.

## 9. Robot arm integration

The arm project lives at `D:\RobotArmControl`: Python with aiohttp, HTTP+WebSocket on port 8080, an ES5 tablet UI already redesigned for the SM-T110. It is finished, and BrainHub changes only one thing in it: a small **HOME** link back to the tablet home screen (§3.3).
- The link must be deliberate (long press or double tap) and sit far from STOP.
- It must not change any arm behaviour.
- The builder must check what the arm server does when its page is closed while the arm is LIVE (the WebSocket disconnects), and that leaving for HOME is safe. If it isn't, HOME refuses while the arm is LIVE.

BrainHub shows whether the arm server is reachable, and opens its UI.

## 10. Phone (phase 6, low priority, mechanism undecided)

- **Reminders:** handled by Google Calendar from phase 5 (§8.1). Nothing to build on the phone for them.
- **Capture:** the owner's idea is that the phone stores notes locally and feeds them into the hub only when it's on the same network as the PC.
- Suggested mechanism: a plain-text notes app plus Syncthing syncing a folder that the hub watches as an inbox. This depends on whether the phone is Android or iPhone, which isn't decided. Deferred by the owner.

## 11. Technical decisions

**Decided by the owner**
- The PC is the hub, and everything runs locally.
- Claude runs via Claude Code on the owner's subscription, with no API key.
- Brain data is `.md` files with a general / projects / personal / uni split.
- Extracted items need confirmation.
- The workbench tablet is the SM-T110.
- The robot arm UI is dropped in as is.
- Kokoro for voice.
- No listening and no live camera.
- Agent labels come from the git branch and are editable.
- Claude Design is tracked only if a desktop-app session can be detected reliably (§7.1); otherwise it is excluded.

**My calls (the owner may override)**
- **Backend:** Python + aiohttp, the same stack as the arm server and one the owner already uses.
- **The PC app is a local web app.**
- **The widget is a Tauri v2 shell** (owner asked for the best performance while staying good-looking; my call).
  - Tauri uses Windows' built-in WebView2, so it's typically tens of MB of RAM where Electron uses hundreds.
  - It supports transparent, frameless, always-on-top, draggable windows.
  - It renders the hub's own widget page from localhost, so it shares the same UI code and design.
  - It needs the Rust toolchain for building only, not at runtime.
- **The tablet page is a separate ES5 page** served by the hub on the LAN, not a responsive version of the PC app.
- `brain/` is git-versioned (§4).
- Recall: an SQLite FTS5 index plus a single-round Claude answer, with "dig deeper" on demand. Local semantic search is added later (§5.4).
- Hub hooks are fire-and-forget (§7.2).

**Usage data source (from recon of the Claude Code docs, 2026-10-20; installed version 2.1.304):**
- **What's available:** Claude Code's **status line** receives `rate_limits.five_hour.used_percentage` / `.resets_at` (Unix seconds) and `rate_limits.seven_day.used_percentage` / `.resets_at` on stdin, for Pro/Max subscribers, after the first response of a session.
  - A status line command in the owner's user settings can forward this to the hub on each refresh. The hub keeps the latest value and shows how old it is.
  - Each window may be missing independently.
- **Gaps:**
  - Values update only while some Claude Code session is active. When none is, the hub shows the last known value and its age, and still computes resets correctly from `resets_at`.
  - A per-model weekly limit (Fable) is not exposed anywhere documented. It is resolved by linking out (§6).
  - `/usage` is interactive only. Transcript files are internal and must not be parsed.
- **Hooks for agent status** (documented stdin fields; all events carry `session_id`, `cwd`, `transcript_path`, `hook_event_name`):
  - `SessionStart`: the row appears.
  - `UserPromptSubmit`: running.
  - `Notification` with `permission_prompt`, `idle_prompt` or `agent_needs_input`: waiting for you.
  - `Stop`: finished.
  - `SessionEnd`: the row is removed after a short delay.
  - `session_title` (set by `/rename`) is available in `SessionStart` and `UserPromptSubmit`. When present, it may be used as the part label instead of the branch.
  - The docs say hooks fire the same way in the terminal, IDE extensions and the desktop app. That user-level hooks fire in the VS Code extension and in desktop-app local sessions is implied but not stated outright, so **phase 2 must verify it on the owner's machine first.**
- **Headless filing:** `claude -p` uses the subscription login, as long as `ANTHROPIC_API_KEY` is not set in the hub's environment (if it is set, `-p` uses the API key instead) and `--bare` is not used.
  - Useful flags: `--output-format json`, `--json-schema` (structured item output), `--append-system-prompt-file`, `--allowedTools`, `--max-turns`, `--model`.
  - There is no `--cwd` flag; the hub sets the process working directory to `brain/`.
  - Whether `-p` runs count against plan limits is not explicitly documented. Assume they do.

## 12. Build order

1. **The brain:** the setup wizard and its Settings editor with the hierarchy view, then capture, raw log, filing, confirm card, recall, plus the Today view and journal in the PC app.
2. **Widget, Usage meter, Agents panel (hooks).**
3. **Workbench tablet:** one-time pairing; a home screen with ROBOT / JOURNAL; NOTES with measurement requests and IGNORE; TODAY; AGENTS; the HOME link in the arm UI.
4. **Kokoro voice and sass.**
5. **Google Calendar reminders** (low priority; makes reminders work with the laptop shut).
6. **Phone:** offline capture sync (low priority).

**Any time after phase 1:** local semantic search for recall (§5.4, step 4).

**Future:** snap a photo (table scribbles, whiteboard) and send it to To review. The owner called this phenomenal later, not needed now.

Each phase must be usable by itself. Phase 1 alone should already beat paper.

## 13. Setup, structure and access

### 13.1 Setup wizard ("calibration")
- **Runs on first opening.** The owner defines the brain's map:
  - areas (general, projects, uni, personal…);
  - projects (with which one is the company);
  - uni courses;
  - people (teammates, with the project they belong to);
  - the folder→project map for agent tracking (§4), e.g. `D:\RobotArmControl` → robotarm;
  - quiet hours.
- Nothing is hardcoded. This map is the filing agent's core context.
- **The same editor lives in Settings**, for adding or changing things later, with a **hierarchy view** showing the whole tree and what's filed under each node.

### 13.2 Graph view (to consider)
- The owner floated an Obsidian-style graph alongside the hierarchy.
- It's nearly free if brain files link to each other with `[[wiki-links]]` (project ↔ person ↔ note). Then the `brain/` folder opens in Obsidian as a vault with its graph view, and nothing needs building.
- **Requirement:** filing writes `[[wiki-links]]` for projects and people.
- **Optional, later:** an in-app graph.

### 13.3 LAN access (Wi-Fi network)
- Anything opened from **localhost on the PC** needs no login.
- Any **other device on the LAN** (tablet, later phone) must **pair once** with a passkey/PIN shown in the hub's Settings. It is then **remembered** (long-lived token or cookie), so the owner never types it again.
  - "Passkey" here means a simple code, not WebAuthn, which Android 4.2 can't do.
- Settings lists paired devices, and each can be revoked.
- Every endpoint reachable from the LAN requires pairing, reads included, because the brain holds personal data.
- The agent measurement-request command (§7.3) calls the hub over localhost, so it needs no pairing.

## 14. Running it day to day ("deployment")

**The machine is a laptop.** It is either shut down when the owner leaves or travels with them. It is not an always-on server. As a result:
- Hub, widget, agent tracking, tablet and voice work **only while the laptop is on**. That's fine, because agents run on the laptop and the tablet is only used at home.
- **Reminders that work with the laptop off** come through Google Calendar in phase 5 (§8.1). Until then, reminders only reach the owner while the laptop is on.

Settled:
- **No hosting, no cloud server.** Everything runs locally.
- **Autostart:** the hub and widget start at Windows login, with a tray icon (open, pause notifications, quit).
- **Self-healing:** if the hub process crashes, it restarts quietly.
- **Updates:** `git pull` and a restart. No installer.
- **Backup (owner's decision):** `brain/` is pushed to a **private GitHub repo**. The hub pushes after commits, batched (e.g. at most every few minutes), and retries silently when offline. A push failure shows as a quiet warning in Settings, never a popup.
- **Startup catch-up:** on boot, the hub processes anything missed while it was off, such as overdue items and phone captures once phase 6 exists.

## 15. Left to the builder
- Internal API between the hub, the agent runner, the widget and the tablet.
- Storage format for structured items (within §4's requirements).
- The agent's prompt and the structured output format for filing and recall.
- How "where it runs" is detected for agent sessions.
- Exact tile set and layout details. The visual design for the widget, PC app and tablet is being done separately in Claude Design. **Don't wait for it (my call):** phase 1 ships a plain, functional UI, and it gets reskinned when the mockups arrive.
- How follow-up nudges for People are timed (default suggestion: 3 days).
