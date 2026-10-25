# ATLAS Phase 4.5: Native window and the instrument-panel UI (design)

Date: 2026-10-24
Status: **asked for by the owner (2026-10-24); their answers are marked (owner). The owner said "execute it".** Phases 5 (Google Calendar) and 6 (phone) are **on hold** until the phases so far prove useful (owner).
Branch `phase-4.5`, cut from `phase-4` (PR #5); rebased onto master once #5 merges.

## 1. Why and what
The owner: "currently it's quite bad". Everything they picked as a pain point (owner) gets fixed here:
- a generic, ugly look;
- navigation (nine pill links that wrap);
- cluttered Today and review cards;
- Settings as one endless scroll.

The phase:
1. **Native (owner):** the Tauri shell gets its own **main window** (taskbar entry, launcher icon, remembered size, Alt+Tab) and stops opening the system browser. The widget stays. The code is the same on Windows; checking it there waits until Windows comes off hold.
2. **Look: a faithful instrument panel (owner).**
   - It speaks the language of the owner's 6-DOF arm UI (`~/dev/RobotArmControl/index.html` and its `design_handoff_tablet_ui/README.md`): a pure black ground, hairline grey rules, small tracked uppercase labels, mono numbers, and outlined controls that invert to white.
   - Colour is used only for meaning: green = live or healthy, amber = attention or a risky action. Red is never used, because ATLAS has no STOP.
   - It is dark only. Body text is sized for comfortable reading.
3. **Scope (owner):** the full PC app, the desktop widget, and fixes to the tablet page where it drifted from the arm UI.

This is an Operate surface: a working app. The visual world is pinned by the owner, so there was no direction lottery and the build is code-led. The arm's grammar is adopted, not its fixed 1024×600 absolute layout; that rule exists for Android 4.2, and the PC app is a resizable desktop window. The tablet page keeps its ES5 and no-flex rules.

## 2. The arm language (tokens, from the arm UI)

**Colours** (hex only, no alpha):
| Token | Value | Use |
|---|---|---|
| ground | `#000` | the background |
| text | `#fff` | text, primary fills, selected fills |
| dim | `#8a8a8a` | labels, inactive tabs, links |
| faint | `#5c5c5c` | hints, units, empty states |
| line | `#262626` | dividers, panel borders, switch off |
| line2 | `#3a3a3a` | button borders, field borders, disabled text |
| sub | `#1a1a1a` | sub-dividers, pressed list row |
| key | `#141414` | key fill |
| key line | `#2a2a2a` | key border |
| pressed | `#b8b8b8` | primary button while pressed |
| pressed row | `#d0d0d0` | current row while pressed |
| green | `#30d158` | live, healthy, done |
| amber | `#ffb020` | attention, risky, armed |
| err | `#ff6a5c` | error text only |

**Type:** Roboto 400 15px/1.4 for the body. Every number is mono 500 (`.big` is 64px, letter-spacing −.02em).
| Element | Weight and size | Tracking | Case |
|---|---|---|---|
| Section label | 600, 11px, `#8a8a8a` | .14em | uppercase |
| Button | 600, 12px | .1em | uppercase |
| Tab | 600, 13px | .1em | uppercase |
| Brand | mono 600, 13px | .14em | |

Text conventions: a true minus `−`, `°` for units, `–` for an empty value. A readout is a dim letter followed by a white value.

**Shape:**
- Borders are solid 1px.
- Radius 3px on buttons, chips and fields; 4px on cards; 6px on keys. Switches and dots are round.
- No shadows, gradients, glows or translucency.
- Tab underline: 3px white. Progress bars are made of 4px segments.

**Components:**
- **Buttons.** `.b` is 48px with a `#3a3a3a` border. Primary (one per panel) is a white fill with black text. Pressed or on inverts to white. A pressed primary shows `#b8b8b8`.
- **Amber outline buttons** turn solid amber when armed. Risky actions fire on a second press within 2 s, and the first press shows "TAP AGAIN" (`twice()`).
- **Disabled:** `#3a3a3a` text on a `#262626` border.
- **Switch:** 56×30, green when on. The small `.s2` (48×26) turns white when on.
- **Chip:** a 28px amber outline, 11px uppercase, with a 6×6 amber square.
- **Status dot:** 8px, grey, green or amber, with mono 12 dim text.
- **List rows:** 52px. A current row is a white fill with black 600 text.
- **Segmented controls:** the selected cell is white.
- **Press feedback:** a class added on pointer-down, not `:active`.
- **Motion:** none, apart from a 120 ms slide of the switch knob, and the ATLAS dot on the ATLAS screen, which follows the voice's loudness while ATLAS speaks (owner, 2026-10-25; `voice-input-decisions.md` VI8, VI9).

Desktop additions the arm doesn't need:
- Hover lifts an outline to `#8a8a8a`.
- `:focus-visible` is a 2px white outline.
- Keyboard shortcuts.

## 3. Information architecture (PC app)
**The shell is a full-height grid:** the top bar (56px), the banner, `main#view` (the only scroll container), the tray, then the capture bar. The page itself never scrolls.

**Top bar**, from left to right:
1. `ATLAS` brand, which goes to Today.
2. Tabs: **TODAY · ASK · JOURNAL · BROWSE · AGENTS · TABLET**. (2026-10-25: ASK became **ATLAS**, the one write-or-ask screen; `#/ask` still lands there. `voice-input-decisions.md` VI1.)
3. A spacer.
4. The status cluster:
   - amber chips, shown only when they apply: `OFFLINE`, `READ-ONLY`, `DRIVE`, `N FAILED`;
   - readout `REVIEW n`, amber when above 0, links to Today;
   - readout `5H n%`, amber at the warn level, links to `#/usage`;
   - an agents dot with `2 RUNNING · 1 WAITING`;
   - `VOICE` with a `.s2` switch (hidden until the voice is installed).
5. `SETTINGS` in the arm's STOP slot: full height, with a rule on its left.

Below 1100px the labels drop and only the values stay.

**Routes.** The nine destinations map onto six tabs; the hash routes are an API, so every one of them still works:
- `usage` goes to AGENTS, with the usage panel focused.
- `people`, `deadlines` and `project/<slug>` go to BROWSE, with a rail.
- `browse` is new and opens the last-used item (default: deadlines).
- `settings/<section>` is new. The `tablet` and `voice` anchors still resolve.

**Keyboard:** Alt+1…6 for the tabs, Alt+0 for Settings, Alt+C for capture.

**Capture** is the arm's bottom action bar (72px):
- the `CAPTURE` label;
- a textarea, one line at rest, growing to six;
- a mono status line;
- `SAVE` as the primary button. Ctrl+Enter still saves.

It is always visible, reduced mode included.

**The tray** sits directly above capture:
- Each card is a compact 52px row: dot, synopsis, mono progress, then `REVIEW` and `×`.
- `REVIEW` expands it into the full confirm card.
- The tray scrolls inside itself, up to 45% of the height.

## 4. Screens
**Shared:**
- Panels are split by 1px rules.
- Section headers are 40px: a label and a mono count.
- Lists use 52px rows on `#1a1a1a` sub-dividers.
- An empty state is one faint row.
- Reading text (`.read`) is 16px/1.6, at most 72ch.

**Today.** (Amended after the owner's test drive, 2026-10-24: the review moved into a pop-up; decisions T-P1…T-P12.)
- Two columns at 1100px and wider.
- **Left column:** OVERDUE, DUE TODAY, UPCOMING, OPEN TASKS and OTHER. An empty section is left out; when all of them are, one faint row says "Nothing due, nothing open." Sections are 24px apart, each a ruled 40px head over its rows.
- **Right column:** NEXT TIME (by project), WAITING and TO READ, left out the same way ("Nothing waiting, nothing to read.").
- **What waits for confirmation is not on the page.** While there is any, the left column starts with one line: the amber square, "7 entries to review" (mono count) and `REVIEW`. With none, there is no line.
- **The review pop-up** (a modal `<dialog>`, Esc or × closes it, the focus stays inside):
  - an opaque ground over the whole window, so only the review shows; a hairline panel, 4px radius, `min(1120px, 100vw − 48px)` × `min(780px, 100vh − 48px)`;
  - a header `TO REVIEW n` with ×; the queue as a rail on the left (two-line rows: the synopsis, then `6 TO CONFIRM` or an amber `FAILED`, and the date; the selected row is inverted; ↑ and ↓ move); the selected entry's confirm card as the main area, its action bar (TELL IT WHAT'S WRONG, CONFIRM ALL) at the foot;
  - it opens by itself on Today when the queue holds an entry this viewer hasn't been shown in it yet (on arrival or on a live update), never while the owner is typing anywhere in the app, never on other views, and not on a read-only root; it never opens again for the same entries, also after a reload;
  - it also opens from the line's REVIEW, the top bar's `REVIEW n` (from anywhere: `#/today?review=1`) and the tray's REVIEW on Today (on that entry); it closes by itself once the last entry is handled.
- **Item rows:**
  - A hollow ring is the done button.
  - The meta line is mono, and overdue dates are amber.
  - `SNOOZE` opens an inline segmented control: 1 D, 3 D, 1 W. With a mouse it shows only on the row's hover or keyboard focus (it keeps its place, so the text never re-wraps).
  - "NUDGE?" is amber.

**Confirm card.** It does everything it did before, laid out differently:
- **Header:** the date, the synopsis, and a status (`FILED` with a green dot, `FILING · 12 S`, or a `FAILED` chip).
- **Filed to:** the files, in mono dim.
- **Proposal rows:**
  - a 104px kind label (TASK, DEADLINE, NEW PROJECT, UPDATE), then the text and a mono meta line;
  - warnings as amber lines, each with its square;
  - `✓`, `EDIT` and `✕`. `✕` (dismiss) uses `twice()`.
- **Edit:** a field grid, then `SAVE` and `CANCEL`. Esc cancels.
- **Footer:** `CONFIRM ALL` (primary) and `TELL IT WHAT'S WRONG`. The correction box stays folded until that button is pressed, or while a draft exists.
- **Failed:** the error text, "Your text is safe in the log", and `RETRY`.

**Ask.** (Replaced on 2026-10-25 by the ATLAS screen, which keeps all of this for a question: `voice-input-decisions.md` VI1–VI3.)
- A 56px query field with `ASK`.
- A 360px MATCHES rail and the ANSWER in `.read`. Citations are dim underlined links.
- `DIG DEEPER` is an amber outline, because it uses allowance.
- The note viewer replaces the answer pane, with `‹ BACK`.

**Journal.**
- A 240px rail: DAY, AREA (rows with counts) and FIND.
- Sticky day headers.
- Each entry has a 64px mono time column and its text in `.read`.
- A failed filing shows a `FILING FAILED` chip.
- `?entry=` highlights the entry with a white border.
- `LOAD OLDER` at the end.

**Agents and Usage.**
- A hairline table with status dots: green running, amber waiting, grey idle, a ring when ended. Removing a row uses `twice()`.
- A 360px USAGE panel. For each window: a `.big` 64px value, a 20-segment bar (amber at the warn level), and the reset time. `FABLE USAGE ↗` sits at the bottom.

**Tablet.**
- NOTES are blocks, each with `FILE THIS` (primary) and `IGNORE` (`twice()`).
- A 400px OPEN REQUESTS panel.
- `SHOW IGNORED` is a switch.

**Browse.**
- A 240px rail: DEADLINES, PEOPLE, then PROJECTS (projects and courses).
- A project page shows OPEN ITEMS, NOTES and JOURNAL.

**Settings.**
- A 240px rail of sections with status dots: BRAIN MAP, HIERARCHY, PREFERENCES, BACKUP, CLAUDE CODE, TABLET, VOICE, SYNC, ABOUT.
- Each section is its own route, `#/settings/<section>`. Only the selected one renders.
- Preferences are two-column rows. Each section has its own SAVE in an action bar.
- The map editor uses hairline rows.

**Setup.**
- A brand-only top bar.
- A six-step progress bar (4px segments).
- A 760px column.
- `BACK` and `NEXT`/`FINISH` in the action bar.

**Reduced, read-only and offline.**
- **Reduced:** an amber chip, and a notice panel with an amber outline and its square. Capture stays.
- **Read-only:** a `READ-ONLY` chip. Done and confirm are disabled, with the reason as their title.
- **Errors** use `#ff6a5c`.

## 5. Design system files
**Fonts:** variable Roboto and Roboto Mono, woff2, in latin and latin-ext (for Romanian ș ț ă î â).
- They live in `web/fonts/`, with their licence files and a sha256 for each in the decisions doc.
- Neither Kubuntu nor Windows ships Roboto, so bundling is the only way to be faithful.
- The weight 600 needs the variable font.

**CSS:** `web/css/tokens.css`, `base.css`, `components.css` and `app.css`, plus one file per view in `web/css/views/*.css`. `style.css` goes. The CSP stays as it is: no inline styles, nothing loaded from a CDN.

**JS:**
| File | What it holds | Tested |
|---|---|---|
| `press-model.js` | `Twice` | yes |
| `press.js` | the delegated `.prs` handling, and `twice()` | |
| `nav-model.js` | `parseHash`, the tabs, the aliases | yes |
| `status-model.js` | the status cluster's state | yes |
| `components/ui.js` | builders on `el()` | |

Views keep their logic and get new `el()` trees and classes. All pure models and their node tests stay.

**Guard test `tests/test_web_design.py`** checks that:
- colours are hex only, with no rgba;
- there are no shadows, gradients or filters, and no external URLs;
- `transition` appears only on the switch knob, and only the ATLAS dot animates from JS (VI9);
- there are no `style=` attributes in the HTML;
- every `/static/` link resolves;
- fonts are served as `font/woff2`.

## 6. The native shell
**The main window** (label `main`):
- 1440×900, minimum 900×600, decorated, resizable, in the taskbar, with a black background.
- It is built lazily on the first open.
- It loads `dist/main.html` (a black "STARTING THE HUB…" bar), then navigates to `http://127.0.0.1:{port}/` once the hub answers.
- Closing it hides it (Quit is in the tray). That keeps the WebSocket and drafts alive and makes reopening instant.
- It has **no IPC capability**.

**Opening it:**
- `show_main(view)` creates the window if needed, sets `location.hash` (no reload) once the window is on the hub page, then shows, unminimises and focuses it.
- **Tray:** "Open ATLAS" opens the main window; "Open in browser" stays as a fallback.
- **Single instance:** a second launch with `--open[=view]` shows the main window; with no flag it shows the widget, as today.
- The launcher passes `--open`. Autostart passes nothing, so login still shows only the widget.
- **Widget tiles** call a Rust command `open_main(view)` via `__TAURI__.core.invoke`. It's granted only to the widget capability (`allow-open-main`, through the app manifest) and only for the localhost remote. Without the shell, the widget falls back to `POST /api/open`, which opens the browser, so Python doesn't change.
- **Pure Rust helpers, all tested:** `valid_view`, `main_url`, `hash_script` and `wants_main`.

**Window state:** position, size and maximised for main. The widget keeps position only (`skip_initial_state("widget")`, then `restore_state(POSITION)`).

**Launcher:** a new `python -m atlas desktop-entry install|uninstall|status --exec <binary>`.
- It writes `~/.local/share/applications/dev.atlas.desktop.desktop` with `Exec="<bin>" --open`, `StartupWMClass` and the icon, and copies the icon to `hicolor/256x256`.
- Tests use temp folders only. `XDG_DATA_HOME` joins the temp env in conftest, and rule 12 gains those paths.
- On Windows it says "not yet" and exits.

**Freshness:** the shell's source digest now includes `dist/`, in Rust and in the update step, so a change to the starting pages also says "update available".

## 7. Widget and tablet
**Widget** (520px):
- An opaque black panel with a 1px `#3a3a3a` border and radius 4.
- The bar: grip slot, attention square, synopsis, voice glyph button, ▾.
- A flush 4-column tile grid on hairline rules. Each tile: a label, a 28px mono value, a caption. A warning makes the value amber and adds the square. An open tile is inverted.
- Popup rows and `OPEN IN ATLAS ›`.
- The projects tile goes to `browse`.

**Tablet** (ES5 and CSS lint stay green):
- `box-sizing: border-box`, with a sweep of every positioned box, so `.btn` is 48px and `.bar` 56px.
- The body is 15px/1.4, and mono is weight 500 with its fallbacks.
- Pressed and primary-pressed states, and disabled `#3a3a3a`.
- The arm's chip for the request count.
- **The keyboard keeps its 80px keys:** ATLAS's sheet has no suggestions row, and big keys suit typing notes. It only takes radius 6 and 22px letters.
- **HOME moves to the brand slot as `‹ HOME`,** where it sits on the arm UI too (hold, or double-tap).

## 8. Testing
- **Python:** the guard test, font serving, desktop-entry (temp folders), and the update step's digest with `dist/`.
- **Node:** press-model, nav-model, status-model, and the updated widget-model.
- **Rust:** `valid_view`, `main_url`, `hash_script`, `wants_main`, per-window navigation, and the digest with `dist/`.
- **Headless Chrome** against a scratch hub:
  - every view at 1280×800, 1600×1000 and 900×700, including review open, edit open, filing in progress and a failed filing;
  - reduced, read-only and setup;
  - the widget at 520px;
  - the tablet at 1024×600;
  - zero console errors and zero CSP violations.
- **The shell window itself is the owner's check,** in `TODO.md`. Running the real shell from automation would write the owner's real desktop folders.
