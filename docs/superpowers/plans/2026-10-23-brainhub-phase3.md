# BrainHub Phase 3 (workbench tablet): build plan

Date: 2026-10-23. Design: `docs/superpowers/specs/2026-10-23-brainhub-phase3-design.md` (approved by the owner the same day). Branch `phase-3` in the dev copy.

How it's built:
- Test first, one commit per task, plain messages (no trailers, rule 8).
- Build-time calls go into `docs/superpowers/phase3-decisions.md`.
- After the build, subagents review the whole branch and their findings are fixed (the owner's instruction).
- Nothing is pushed without asking.

## Tasks

1. **Settings.**
   - What: `tablet_lan` (bool, false), `tablet_address` (private IPv4 or ""), `tablet_units` (non-empty list of short strings), through `DEFAULT_SETTINGS`/`validate_setting`.
   - Files: `config.py`. Tests: `test_config.py`.
2. **Store schema v3.**
   - What: `devices`, `requests`, `request_answers`, `tablet_hidden`; migration 3.
   - Exports to `brain/_items/devices.json`, `requests.json` and `tablet.json`, plus restore-if-empty at startup (beside items and proposals).
   - Files: `store.py`, `server.py` (startup steps). Tests: `test_store.py`.
3. **`brainhub/tablet.py`: pairing, guard, LAN site, feed.**
   - Pairing codes: in memory; 6 digits, 5 min, one use, 5 tries.
   - Device tokens: SHA-256 in `devices`.
   - `lan_guard` middleware: classifies a request by the local socket it arrived on; the LAN site gets only the tablet surface and needs the cookie.
   - `LanSite`: holds the address (UDP-connect source check), starts and stops a `TCPSite` on `tablet_address` every 15 s or on a settings change.
   - `TabletFeed`: a version counter bumped by broadcasts, and a long-poll wait.
   - Files: `tablet.py`, `server.py`, `__main__.py` (the runner reaches the LAN site), `routes.py`. Tests: `test_tablet.py`, including a real second site on `127.0.0.2` standing in for the LAN.
4. **Measurement requests.**
   - API: `POST/GET /api/requests`, ignore and restore; project from `cwd` with this OS's map folders.
   - `brainhub/request_client.py`: stdlib, runs as a file.
   - The update step keeps it in `<state>/hook/`.
   - `integration install`/`uninstall`: the `~/.local/bin/brainhub-request` wrapper and the one `CLAUDE.md` line between markers, with a backup.
   - Tests: `test_requests.py`, `test_request_client.py` (real subprocess), `test_claude_integration.py` (temp files only), `test_update_step.py`.
5. **Tablet notes, answers, IGNORE, "File this", tablet state.**
   - Tablet API: `POST /api/tablet/notes` (raw log `source=tablet`, held on a read-only root), `/api/tablet/requests/<id>/answer` (one verbatim raw-log entry), `/api/tablet/ignore`, `GET /api/tablet/state`, `/api/tablet/wait`.
   - PC API: `GET /api/tablet-notes`, `/file`, `/ignore`, `/restore`.
   - Tests: `test_api_tablet.py`.
6. **The tablet page** `web/tablet/`.
   - Files: `index.html`, `tablet.css`, `tablet.js` and ES5 helper scripts (`kb.js` keyboard state, `logic.js` IGNORE/HOME timers, poll backoff, send queue). The helpers export via `module.exports` for node tests.
   - Screens: pairing, home, JOURNAL (TODAY, NOTES, AGENTS), the in-page keyboard (text and numeric with unit chips), offline queue and drafts.
   - Tests: an ES5/CSS lint test in Python (forbidden syntax, APIs Android 4.2 lacks, forbidden CSS), and node tests for the helpers.
7. **PC UI.**
   - The Tablet notes view (nav "Tablet").
   - Settings → Tablet: switch, address with "use this laptop's", pair code, devices with Revoke, units.
   - Widget: open requests count toward attention and the synopsis.
   - Files: `web/js/views/tablet.js`, `settings.js`, `app.js`, `widget.py`. Tests: node for any pure helper, plus `test_widget.py`.
8. **Arm UI HOME link** (repo `RobotArmControl`, branch `brainhub-home`, `index.html` only).
   - Behaviour: `?home=` stored in `localStorage`; the brand turns into "‹ HOME" (hold or double-tap); it refuses while LIVE.
   - Checked in headless Chrome against a stub; no arm hardware.
9. **Docs and e2e.**
   - Headless Chrome at 1024×600 against a scratch hub: pairing over a real LAN site on this laptop's address (port 8097), home, tabs, a request from `request_client.py` appearing, an answer typed on the in-page keyboard, the PC view, IGNORE, HOME.
   - Docs: `app/CLAUDE.md`, `app/README.md` (Tablet section, Measured), `NEXT-SESSION.md`, decisions.
10. **Reviews** by subagents (backend and security; tablet page; arm change), and the fixes.

## Rollout (owner, after the merge; design §11)
Router reservation → ufw rule → Settings switch and address → Fully Kiosk start URL and pairing → `integration install` (wrapper and `CLAUDE.md` line) → merge the arm branch and restart `server.py` → the ROBOT/HOME round trip.
