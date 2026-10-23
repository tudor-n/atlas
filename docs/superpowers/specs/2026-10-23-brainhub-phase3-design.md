# BrainHub Phase 3: Workbench tablet (design)

Date: 2026-10-23
Status: **draft, awaiting the owner's approval.** Brainstormed with the owner on 2026-10-23; their answers are marked **(owner)**.
Parent spec: `2026-10-20-brainhub-design.md` (§3.3, §7.3, §9, §12 item 3, §13.3). It is the authority; this document makes phase 3 concrete. The dual-boot design (`2026-10-21-brainhub-dual-boot-design.md` §8.3) adds the cross-OS requirements. Where this document departs from either, the departure is marked **(my call)**.

## 1. Scope

Phase 3 delivers the workbench tablet (Samsung Galaxy Tab 3 Lite SM-T110, Fully Kiosk Browser):
1. **LAN access with one-time pairing** (spec §13.3). It is off until switched on in Settings **(owner)**, and it listens only at the laptop's fixed home address.
2. **The tablet page** `/tablet`. It's plain ES5 with a fixed 1024×600 layout in the arm UI's visual language (spec §3.3). It has:
   - a **home screen** with two big choices, **ROBOT** and **JOURNAL**;
   - **JOURNAL** with tabs **TODAY · NOTES · AGENTS**;
   - an **in-page keyboard** for all typing **(owner)**: the Android keyboard never opens.
3. **Measurement requests** (spec §7.3): a tiny `brainhub-request` command an agent runs; the request appears as a checklist on NOTES; answers land in Tablet notes, never back in the agent.
4. **Tablet notes** on the PC (spec §3.2): everything typed on the tablet, newest first, with IGNORE/restore and a **"File this"** button. Tablet notes are **not filed automatically** **(owner)**.
5. **IGNORE** with double-tap confirm on each request and note (spec §3.3).
6. **The HOME link in the arm UI** (spec §9), in the arm project `tudor-n/6-dof-arm`.

Not in phase 3:
- voice (phase 4);
- Google Calendar (phase 5);
- the phone (phase 6);
- ticking items done from the tablet: TODAY is a glance only **(owner)**;
- Romanian diacritics on the in-page keyboard: not needed **(owner)**;
- Windows-side setup (the firewall rule, a check on Windows). **Windows is on hold (owner, 2026-10-23).** The code stays portable and CI keeps testing on both OSes; the Windows rollout gets its own short step when the owner comes back to it.

## 2. Verified facts (recon, 2026-10-23, Kubuntu)

| Fact | Result | Source |
|---|---|---|
| The laptop's home address | `192.168.0.200/24` on `wlp8s0` (DHCP), gateway `192.168.0.1` | `ip addr`, `ip route` |
| The Wi-Fi MAC | `24:b2:b9:cd:c5:69`, the permanent one: NetworkManager doesn't clone or randomise it for the home connection | `/sys/class/net`, `ethtool -P`, `nmcli` |
| Firewall | **ufw is active** (firewalld isn't). Port 8090 needs an allow rule for the home subnet; that takes `sudo`, so the owner runs it. | `systemctl is-active` |
| The hub today | binds `127.0.0.1` only (`server.DEFAULT_HOST`); `local_only` refuses any Host but `127.0.0.1`/`localhost` | `server.py`, `routes.py` |
| Raw log sources | `rawlog.SOURCES` already has `"tablet"`; `POST /api/entries` accepts only `pc` and `widget` | `rawlog.py`, `routes.py` |
| The arm server | `server.py` in `RobotArmControl` serves its UI on `0.0.0.0:8080` (`/`, `/ws`, `/poll` long-poll fallback) | `server.py` |
| **The arm when its page leaves** | When the last client disconnects, `client_gone` only clears `unlock` and `creep`. **LIVE stays on**: the servos stay powered and a move in progress runs to its target, with no STOP on any screen. So leaving for HOME is **not** safe while LIVE, and **HOME refuses while LIVE** (spec §9). | `server.py` `client_gone`, `control_loop` |
| The tablet's browser | Android 4.2 WebView (pre-Chromium WebKit): ES5, no CSS grid/gap/flex-wrap/variables, laggy `:active`, a ~300 ms click delay, and the system keyboard resizes the viewport | arm handoff `design_handoff_tablet_ui/README.md` §1, §5 |
| WebSocket on the tablet | **Assumed absent.** Android's stock WebView gained WebSocket only in 4.4, and the arm UI ships a long-poll fallback for this device. The tablet page uses long-polling only. | arm `index.html` transport; to confirm on the device |
| Store rebuild | `store.py`: "everything here is rebuildable from the brain/ folder". Items and proposals are exported to `brain/_items/*.json` for that. Tablet state must follow the same rule. | `store.py` |
| Startup | `FilingWorker.requeue_unfinished` marks a raw-log entry with no filing as failed ("not filed yet (found at startup)"). Tablet notes must be exempt, or every note would land in To review. | `filing.py` |

To confirm on the device during the build (§12): WebSocket really absent; the `Set-Cookie` token survives a Fully Kiosk restart; Roboto/Droid glyphs `◆ ← → − · ⌫ ↵`.

## 3. Architecture

```
SM-T110 (Fully Kiosk) ──Wi-Fi──► 192.168.0.200:8090  (LAN site, only while "Tablet over Wi-Fi" is on
                                     │                and the laptop holds that address)
                                     ├─ lan_guard: paired device cookie; only /tablet/* and /api/tablet/*
                                     ├─ /tablet            ES5 page (web/tablet/)
                                     └─ /api/tablet/*      state (long-poll), notes, answers, ignore, pair
PC browser / widget ──► 127.0.0.1:8090 (as today: local_only, no login)
                                     ├─ /api/tablet-notes, /api/devices, pairing code   (PC side)
agent ── brainhub-request ──► 127.0.0.1:8090 POST /api/requests   (localhost, no pairing)
tablet ROBOT ──► http://192.168.0.200:8080/?home=<tablet url>  (arm UI; its HOME link comes back)
```

- One aiohttp app, two sites: `127.0.0.1:<port>` always, and `<tablet_address>:<port>` when enabled. Binding the specific address (not `0.0.0.0`) avoids clashing with the loopback bind and never exposes the hub on Docker bridges or another network.
- **(my call)** The LAN site follows the network. Every 15 s (the root watch's interval) the hub checks whether the machine holds `tablet_address`. It starts the LAN site when the address appears and stops it when the address goes away. At uni or in a café the laptop has another address, so the hub listens on loopback only. Flipping the setting takes effect within the same 15 s, with no restart.

## 4. LAN access and pairing (spec §13.3)

### 4.1 Settings (shared `data/settings.json`)
| Key | Default | Rule |
|---|---|---|
| `tablet_lan` | `false` | bool. **Off until switched on (owner).** |
| `tablet_address` | `""` | an IPv4 literal in a private range (10/8, 172.16/12, 192.168/16), or empty. LAN stays off while it's empty. Settings offers "use 192.168.0.200 (this laptop now)". |
| `tablet_units` | `["mm","cm","m","°","g","kg","V","A","mA","Ω","s"]` | the unit chips on the measurement keypad (§6.3); a non-empty list of short strings |

Both OSes get the same address from the router reservation (same MAC), so these are shared values, not per-OS ones. The firewall rule is per OS and lives outside BrainHub (§11).

### 4.2 Pairing
- PC Settings → **Tablet** → **Pair a device** shows a 6-digit code. It is valid for 5 minutes, used once, and invalidated after 5 wrong tries.
- An unpaired tablet opening `/tablet` gets the pairing screen: the code, typed on the in-page keypad, and a device name (default "Workbench tablet").
- `POST /api/tablet/pair {code, name}` → a random 32-byte token in a `Set-Cookie: bh_device=…; HttpOnly; Max-Age=10 years; Path=/; SameSite=Strict`.
  - The hub stores only its SHA-256, with the name, paired-at and last-seen, in a new `devices` table (schema v3).
  - It also exports the devices to `brain/_items/devices.json` (hashes only), so a rebuilt index keeps them.
  - `hub.db` lives in shared `data/`, so the tablet pairs once for both OSes (dual-boot §8.3).
- Settings lists paired devices (name, paired, last seen) with **Revoke**. A revoked tablet falls back to the pairing screen at its next request.

### 4.3 The LAN guard (middleware, before `local_only`)
- Loopback peers (`127.0.0.0/8`, `::1`) go on to `local_only` as today: nothing changes for the PC.
- Any other peer:
  - must be on the LAN site;
  - may reach **only** `/tablet`, `/tablet/*` (static ES5 assets) and `/api/tablet/*` **(my call: least privilege; the spec only requires pairing for everything)**;
  - needs a valid device cookie for everything except the page shell, its assets and `POST /api/tablet/pair`;
  - gets 403 everywhere else, paired or not.
- Origin must be absent or equal `http://<Host>`. Posts still require `Content-Type: application/json` (rule 5).
- The hub never trusts `X-Forwarded-*`.
- Reduced mode (missing, foreign or newer root): no LAN site at all. The tablet can't be authenticated without `hub.db`, so its next poll fails and it shows "BrainHub can't be reached" (§6.5).
- Read-only root: the LAN site runs. Notes and answers are held in the holding inbox, like PC captures (rule 14); IGNORE answers 503.

## 5. Measurement requests (spec §7.3)

### 5.1 The command
`brainhub-request "<title>" "<item>" ["<item>" …]` is a stdlib-only script, like the hook client (rule 11's style). It lives at `brainhub/request_client.py`, runs as a file, and is kept in `<state>/hook/` by the update step.
- It reads `--port` from `<state>/claude_integration.json` (default 8090), then POSTs `{title, items, cwd}` to `127.0.0.1:<port>/api/requests`.
- On success it prints one line: `Sent to the workbench tablet (r-…). The owner's answers go to BrainHub's Tablet notes, not back to you.`
- If the hub is down, it prints `BrainHub isn't running; nothing was sent.` and exits 1. This is an explicit command, so the agent should know.
- Limits: title ≤ 80 characters, 1–20 items, each ≤ 120 characters, no control characters.
- `integration install` writes a wrapper `~/.local/bin/brainhub-request` (`sh`; it runs the base interpreter on `<state>/hook/request_client.py`). It also adds **one line** to the owner's `~/.claude/CLAUDE.md` between marker comments, with a backup first, as for `settings.json`. `uninstall` removes both.
- The line: `Need measurements from the owner's workbench (lengths, angles, IDs)? Run \`brainhub-request "<title>" "<item>"…\`; answers arrive later in BrainHub, not here.`

### 5.2 Storage and API
- A `requests` table: id `r-YYYYMMDD-HHMMSS-xxxx`, title, items (JSON), project (resolved from `cwd` with this OS's map folders, like Agents), created, status `open | answered | ignored`. It is exported to `brain/_items/requests.json`.
- `POST /api/requests` (localhost only) creates one. `GET /api/requests` lists them (PC). `POST /api/requests/<id>/ignore` and `…/restore` exist for both the PC and the tablet.
- Agent text (title, items, project) counts as agent text: `textContent` only, on the PC (rule 6) and on the tablet.

### 5.3 Answers
- On the tablet, each item gets a value field. The value and unit are typed together ("412 mm"); units aren't locked (spec §3.3).
- **SEND** posts `{answers: {item index: text}}` to `/api/tablet/requests/<id>/answer`. Empty items are allowed; at least one must be filled.
- The hub writes **one raw-log entry** (`source=tablet`, verbatim, before anything else, rule 1):
  ```
  Measurements: Arm calibration (robotarm)
  - ArUco ID 3 → arm base: 412 mm
  - ID 3 → ID 7: 98 mm
  ```
  It then marks the request `answered` and links the entry to it. The answer shows in Tablet notes. Nothing goes back to the agent (spec §7.3).

## 6. The tablet page (`/tablet`, `app/web/tablet/`)

### 6.1 Rules (arm handoff §1–§2, binding)
- **Code:** ES5 only, in `tablet.js` plus small helpers, with no modules or build step. `el()`-style text nodes only, never `innerHTML` with data (rule 6).
- **Layout:** fixed absolute layout at 1024×600 with literal px, plus a centred `-webkit-transform: scale()` fit for other screens (and desktop testing).
- **CSS:** no grid, gap, flex-wrap, variables, shadows, gradients, transparency or animations; border-radius ≤ 4 px.
- **Colours:** hex literals only: bg `#000`, ink `#fff`, dim `#8a8a8a`, faint `#5c5c5c`, lines `#262626`/`#3a3a3a`, green `#30d158`, amber `#ffb020`. Red is reserved for STOP, which isn't on these screens.
- **Fonts:** `Roboto, "Droid Sans", sans-serif`; numbers in `"Droid Sans Mono", monospace`.
- **Touch:**
  - hit targets ≥ 48 px;
  - actions fire on `touchstart` with `preventDefault()`, so there's no 300 ms delay and no ghost click;
  - press feedback is a class added on touchstart and removed on touchend/cancel, never `:active`;
  - mouse events are mapped to the same actions for desktop testing.
- **DOM writes:** they go through dirty-checking helpers (`txt`, `cls`, `show`), and a state change swaps a class, never inline styles.
- **No `<input>`, `<textarea>`, `contenteditable` or `focus()` anywhere,** so the Android keyboard never appears and the viewport never jumps.

### 6.2 Screens
- **Home:** two large blocks, ROBOT and JOURNAL. Each is about 440×360 with a 1 px `#3a3a3a` border, a 40 px label, and a one-line status under it: "arm server reachable" (green dot) or "arm server offline" (grey); "2 requests · 5 due today". A small Mono clock and "paired as Workbench tablet" sit in the corner.
- **Switching modes** always goes through home (spec §3.3).
  - In JOURNAL, the **HOME** control sits at the **bottom left**, 120×56, labelled "HOME (hold)". It needs a 600 ms long press or a double tap, and while held it fills amber with "RELEASE TO GO HOME". It's far from where the arm's STOP sits (top right).
  - There are no swipe gestures.
- **JOURNAL top bar** (0,0 → 1024×56): the brand "BRAINHUB", then tabs **TODAY · NOTES · AGENTS** (selected = white text with a 3 px white bottom bar, as in the arm UI), then a link dot (green = connected, amber = retrying).
  - NOTES shows a count chip when requests are open.
- **TODAY** (glance only, **owner**): overdue, due today, waiting on others, and the next deadlines. They're server-formatted lines in 17 px with Mono times, and a "+N more on the PC" line when they don't fit. No buttons.
- **NOTES:**
  - **Open measurement requests** first, newest first. Each is a card with the title, the project and the time.
    - Under that sit 52 px rows: the item label on the left, a value "field" on the right (a tappable `div`) that opens the keyboard in numeric mode.
    - Below the rows: **SEND** (primary) and **IGNORE** (double-tap).
  - **Free-text note:** a large tappable "field" ("Write a note…") that opens the keyboard in text mode, with **SAVE**.
  - **Recent tablet notes** (the last 10, IGNORE on each).
  - When the list overflows it scrolls with a custom drag scroller (touchmove with `preventDefault`) rather than page scroll, so the fixed layout never shifts.
- **AGENTS:** one 56 px row per session (project · part, status word and dot: green running, amber waiting for you, dim finished), plus the two usage meters (5 h and weekly: %, reset time, a 4 px bar; amber at ≥ `usage_warn_pct`).
- **IGNORE** (spec §3.3): the first tap turns it amber with "TAP AGAIN", and it reverts after 2 s; the second tap hides the item. Hidden items stay in the raw log and in the store, and can be restored from the PC's Tablet notes.

### 6.3 The in-page keyboard (owner: everywhere)
This ports the arm UI's detect keyboard (arm handoff §5a): its geometry, key styles and behaviour. It's built once at startup, with one delegated `touchstart` listener, and it's opened by toggling `display`.
- **Sheet:** `0,228 → 1024×372`, `#000` with a 1 px `#3a3a3a` top border. Above it, a header row shows the text being typed (17 px, with a caret).
  - The area above the sheet is solid black and taps close the keyboard, keeping the text.
  - Up to about 4 lines of a long note stay visible, and the header scrolls to keep the caret line in view.
- **Text mode:** QWERTY in lower case with **⇧** (one-shot; a double tap locks caps), **⌫** (repeats: 380 ms, then every 70 ms), **SPACE**, **↵** (a new line), **123** (switches to digits and symbols `1–0 - / : ; ( ) . , ? ! ' " @ # % & + =`), **HIDE ⌄**, and the action key (**SAVE** for a note, **SET** for a field).
  - No autocorrect and no diacritics (**owner**).
- **Numeric mode** (measurement fields):
  - a 3×4 keypad (1–9, `−`, 0, `.`) on the left;
  - the **unit chips** from `tablet_units` in the middle; a tap appends " mm" or replaces the unit already there;
  - ⌫, **ABC** (to text mode, for things like "ID 7" or "loose") and a tall **SET** on the right.
- **Physical keyboard** (desktop testing): letters, Backspace, Enter, Space and Esc (closes) map to the same actions.
- **Drafts:** the text being typed is kept in `localStorage`, per field, so a reload or a Fully Kiosk restart never loses it. It's cleared when sent.

### 6.4 Data flow
- `GET /api/tablet/state` returns everything the page shows as ready-made strings:
  - home status, TODAY lines, open requests, recent notes, agents rows and usage;
  - plus a `version` number that changes whenever any of it changes.
- `GET /api/tablet/wait?since=<version>` is a long-poll: it answers as soon as the version moves past `since`, or after 25 s with the same version. The page loops on it, so a request posted by an agent appears within about a second.
- After 3 failed polls in a row, the link dot turns amber and the poll backs off (2, 4, 8 … up to 30 s). Drafts are kept.
- **Writes** (each answers with the new state): `POST /api/tablet/notes {text}`, `/api/tablet/requests/<id>/answer`, `/api/tablet/ignore {kind: "note"|"request", id}`.
- **(my call)** The page renders at most 10 Hz, through the dirty-write helpers, and only the visible tab.

### 6.5 Errors and reduced states
- Hub unreachable: an amber "Can't reach BrainHub. Retrying…" line, and the last data dimmed (`#5c5c5c`). Typing still works; SAVE and SEND queue in `localStorage` and send in order once the hub is back. They are marked "waiting to send", so nothing typed is lost.
- Held on the hub's side (read-only root): "Saved · held until the drive is back", as on the PC.
- Not paired or revoked: the pairing screen.

## 7. Tablet notes on the PC

- **Capture:** a tablet note is a raw-log entry with `source=tablet`. It is written first (rule 1) and indexed for recall like any entry, so Ask finds it. It shows in the Journal too.
- **No automatic filing (owner):**
  - The hub creates no filing for a tablet entry.
  - `requeue_unfinished` skips `source=tablet` entries without a filing (§2).
- **"File this"** on a note queues a normal filing. From then on it is an ordinary entry, with a confirm card (proposals still need confirmation, rule 2).
- **The Tablet notes view** (new nav item "Tablet"):
  - open requests (title, items, project, time, IGNORE);
  - then notes and answers, newest first: time, text, the request they answer, **File this**, **IGNORE**;
  - a "Show ignored" toggle, with **Restore** on each.
- **IGNORE state** is stored in a `tablet_hidden` table (kind, id, hidden at) and exported to `brain/_items/tablet.json`. The raw log is never touched (rule 1).
- **Widget and PC attention:** open requests count toward the widget's amber attention dot. The Tablet tile isn't new; the synopsis gains "1 request" while one is open. **(my call)** An agent asked for something, so the owner should notice it at the PC as well.

## 8. The arm UI's HOME link (spec §9; arm repo `tudor-n/6-dof-arm`)

This change goes in the arm project, on a branch in `~/dev/RobotArmControl`, and touches `index.html` only. It adds **no `server.py` change and no new message types**.
- **Getting there:** the tablet's ROBOT button navigates to `http://<tablet_address>:<arm port>/?home=<encoded http://<tablet_address>:<port>/tablet>`. The arm port comes from `arm_url`, default 8080. The arm page stores `home` in `localStorage`, so later visits and reloads keep it.
- **The HOME control:**
  - The brand block "ARM-6" at the top left (x 0, as far from STOP at x 892 as the bar allows) becomes "‹ HOME" whenever a home URL is known. Without one it stays "ARM-6", so the PC-only arm UI is unchanged.
  - It needs a 600 ms long press or a double tap, with amber fill while held.
- **While LIVE is on:** HOME refuses. The block flashes amber with "LIVE IS ON: turn it off first" for 2 s and stays on the page (§2: the arm keeps its servos powered when the page leaves). With LIVE off it navigates to `home`.
- **Tests:** in the arm UI's style, with `?legacy=1` at 1024×600 in desktop Chrome, then on the device. That covers LIVE on → refused, LIVE off → home, no home URL → unchanged, and STOP untouched.

## 9. API summary

| Route | Who | Purpose |
|---|---|---|
| `GET /tablet`, `/tablet/*` | LAN + PC | the page and its ES5 assets (no data) |
| `POST /api/tablet/pair` | LAN | code → device cookie |
| `GET /api/tablet/state`, `GET /api/tablet/wait` | LAN (paired) + PC | everything the page shows; long-poll |
| `POST /api/tablet/notes`, `/api/tablet/requests/<id>/answer`, `/api/tablet/ignore` | LAN (paired) + PC | writes |
| `POST /api/requests`, `GET /api/requests`, `POST /api/requests/<id>/ignore|restore` | PC (localhost) | agent requests; the PC view |
| `GET /api/tablet-notes`, `POST /api/tablet-notes/<id>/file`, `…/ignore`, `…/restore` | PC | the Tablet notes view |
| `POST /api/devices/pairing-code`, `GET /api/devices`, `DELETE /api/devices/<id>` | PC | pairing and revoking |

The PC can open `/tablet` on localhost without pairing, which is how the desktop tests and the owner's preview work.

## 10. Data and versions
- `hub.db` schema v3 (`store.SCHEMA_VERSION` 2 → 3, with a migration) adds `devices`, `requests`, `request_answers` (request id → entry id) and `tablet_hidden`.
- New exports `brain/_items/requests.json`, `tablet.json` and `devices.json` let a rebuilt index restore them. They're protected paths, like the rest of `_items/`.
- The brain format is unchanged: raw-log entries with `source=tablet` were already valid.
- A Windows hub on an older commit would refuse the v3 `hub.db` as "newer" (the existing guard, design §5.2), so both OSes must update together. While Windows is on hold this can't bite; it's noted for the Windows return.

## 11. Rollout (with the owner, after the merge)
1. **Router:** reserve `192.168.0.200` for MAC `24:b2:b9:cd:c5:69` in the router's DHCP page **(owner)**.
2. **Firewall (owner, needs sudo):** `sudo ufw allow from 192.168.0.0/24 to any port 8090 proto tcp`.
3. **Settings:** Tablet → set the address and switch on "Tablet over Wi-Fi".
4. **Tablet:** in Fully Kiosk, set the start URL to `http://192.168.0.200:8090/tablet`. Pair with the code from Settings.
5. **Integration:** reinstall (`integration install`), so that `brainhub-request` and the `CLAUDE.md` line are added (the parent spec §7.3 asks for that line; it is shown to the owner before it is written).
6. **Arm:** merge the arm branch and restart `server.py`. ROBOT → arm → HOME round trip, with LIVE off, then LIVE on (refused).

## 12. Testing
- **Python (real SQLite, git and files, as today):**
  - pairing (code expiry, one use, 5 tries, cookie hash, revoke);
  - the LAN guard (loopback unchanged; a LAN peer reaches only the tablet surface; 403 elsewhere even when paired; Origin);
  - the LAN site following the address (a fake address source);
  - requests (limits, project resolution, answer → one verbatim raw-log entry, `answered`);
  - tablet notes never auto-filed (and `requeue_unfinished` skips them), and "File this" → a normal filing;
  - IGNORE/restore and their exports;
  - the v3 migration and rebuild from exports;
  - long-poll wake-ups and timeouts;
  - held notes on a read-only root;
  - `request_client.py` as a real subprocess (hub up and down, limits);
  - the installer's `CLAUDE.md` line and wrapper (temp files only, rule 12).
- **ES5 guard:** a test parses every file in `web/tablet/` as ECMAScript 5. A small pinned ES5 parser runs under node; the plan picks it, preferring no new dependency if a node built-in can do it. A CSS check refuses `grid`, `gap`, `var(`, `flex-wrap`, `box-shadow`, `transition`, `animation`, `rgba` and radii over 4 px in `tablet.css`.
- **JS (node, pure helpers):** keyboard state (shift, caps lock, ⌫, unit replace), the IGNORE double-tap timer, the long-press/double-tap HOME logic, the poll backoff, the offline send queue and draft storage.
- **Headless Chrome at 1024×600** (as for the filing card on 2026-10-23): pairing, home → JOURNAL → each tab, a fake agent request appearing within about 1 s, typing an answer on the in-page keyboard, SEND → the PC view, IGNORE, HOME. Screenshots, and no console errors.
- **On the device (owner):** the same round trip, plus the §2 items to confirm, render speed (no visible lag on tab switches) and the arm HOME round trip.

## 13. Left to the builder
- The exact long-poll and version mechanics (a counter bumped by the broadcasts the hub already makes).
- Whether TODAY's overflow is "+N more" or a two-page flip.
- The ES5 parser choice for the guard test.
- The internal split of `web/tablet/` (one file or a few).
