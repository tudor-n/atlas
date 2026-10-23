# Phase 3 build decisions

The build-time calls made while building phase 3 (the workbench tablet, 2026-10-23), where the design (`specs/2026-10-23-brainhub-phase3-design.md`) was silent, or where testing showed it should change. Each says why it was made and what it costs if wrong. Where a call here differs from the design, this file wins.

## Rulings

**Data**

- **T1 (amends design §10):** there is no `request_answers` table. A request carries its answer's raw-log entry id in an `answer_entry` column.
  - Why: one request has at most one answer, so a column is enough.
  - Cost if wrong: answering one request twice would need a table. Today a second answer is refused with 409.
- **T2 (amends design §4.2):** paired devices are **not** exported to `brain/_items/devices.json`. Their token hashes live only in `hub.db`.
  - Why: the brain is pushed to a GitHub backup. Even a SHA-256 of a random token doesn't belong in its history forever.
  - Cost if wrong: after a rare index rebuild the tablet shows its pairing screen and pairs again.
  - Requests and IGNOREs are exported (`requests.json`, `tablet.json`), as the design says.

**The LAN site and the guard**

- **T3:** the LAN site binds `tablet_address` only, and checks every 15 s whether the laptop holds that address.
  - The check is a UDP "connect" whose source address is read back, and it sends nothing. On Kubuntu, `.200` comes back as `.200`, while other addresses come back with the default route's source.
  - `PUT /api/settings` re-checks at once.
  - Why: on another network the hub stays on loopback, even with the switch on.
  - Cost if wrong: on a foreign network that also hands this laptop `192.168.0.200`, the hub listens there. Pairing still guards every request.
- **T4:** `lan_guard` judges a request by the **local socket** it arrived on, not by the peer address.
  - A connection on any address the LAN site has ever used still counts as LAN, because aiohttp's `site.stop()` leaves kept-alive connections open. Such a connection then gets 403, since its Host no longer matches a bound address.
  - Found by the test that switches LAN access off while a client is still connected.
  - Why: in tests the stand-in LAN address `127.0.0.2` is itself loopback. And a stopped site must never turn its old connections into "PC" requests.
- **T5:** the LAN surface is `/tablet`, five named page files, and `/api/tablet/*`.
  - The PC-only Settings endpoint is deliberately `/api/tablet-lan`, with a dash, so it falls outside that prefix.
  - The page files are served from a whitelist with `Cache-Control: no-cache` and an explicit `charset=utf-8`. The old WebKit would read UTF-8 text in a script as Latin-1 otherwise.

**Captures and requests**

- **T6:** before setup, tablet notes, answers, requests and the tablet state all answer 409. Nothing is written under a root before the wizard has run (the rule 14 spirit: a fresh root must stay fresh).
- **T7:** on a read-only root, notes and answers are **held** in this OS's holding inbox, as PC captures are.
  - A held answer leaves its request open: the read-only store can't mark it answered.
  - IGNORE answers 503 there.
- **T8:** held tablet captures that the startup flush puts into the raw log are **not** queued for filing (`Flushed.tablet`). Tablet notes are never filed automatically (owner).
- **T9:** an answer's raw-log text is `Measurements: <title> (<project>)`, then one `- <item>: <value>` line per answered item. Items left empty are named on a last `(not answered: …)` line, so nothing is silently missing.
- **T10:** `brainhub-request` is installed by `integration install`.
  - The wrapper goes in `osdirs.bin_dir()`: `$BRAINHUB_BIN_DIR`, else `~/.local/bin` on both OSes. It's a `.cmd` on Windows.
  - The `CLAUDE.md` it edits is the one **beside the target `settings.json`**, so `--settings <temp>` keeps tests away from the real one.
  - A wrapper of the same name that isn't BrainHub's is never replaced or removed.
  - The update step copies `request_client.py` beside the hook client. A slot from before phase 3 has none, and that's no error.

**The tablet page**

- **T11, touch:** keyboard keys fire on touch-down (the arm handoff's rule). Other buttons fire on a touch-up that didn't move by more than 10 px, so lists can still be dragged; there's still no 300 ms delay and no ghost click.
- **T12, the keyboard:**
  - SET on a measurement field moves on to the next empty item of the same checklist.
  - While the keyboard is open, a state update doesn't re-render NOTES: the list is never pulled from under the keyboard, and it's drawn once the keyboard closes.
  - Found in the e2e: `show()` set `display` to `''`, which fell back to the stylesheet's `display: none`, so the keyboard never appeared. It now opens and closes with a class.
- **T13, nothing typed is lost:**
  - A note the hub refuses (400) goes back into the note box.
  - An answer to a request answered or removed elsewhere (404/409) is re-queued as a plain note with the same text.
- **T14, connections:** a new long-poll aborts the previous one. Without that, each save would leave a long-poll open, and the old browser has only a few connections per host.
- **T15, ROBOT:** ROBOT doesn't navigate while the hub sees the arm server as offline; it says to start the arm server first. A dead page would leave the tablet with no way back.

**The arm UI**

- **T16:** the arm UI's HOME action is named `bhhome`, **never `home`**.
  - `home` is the arm UI's existing **Start pose** action (`send({type:'home'})`). A second `act('home')` would be overwritten by it, so tapping HOME would have moved the arm.
  - Found in the sim run (`server.py --sim --no-camera`, from a scratch copy).
  - `?home=` accepts only `http(s)://` URLs (never `javascript:`) and is kept in `localStorage`.

## Residuals (to check on the device or later)

- **On the SM-T110, with Fully Kiosk:**
  - that WebSocket really is absent (it doesn't matter: the page never uses it);
  - that the `bh_device` cookie survives a Fully Kiosk restart (if it doesn't, the tablet shows its pairing screen again);
  - that the glyphs `⌫ ↵ ⇧ ⌄ ‹ → ·` render in Roboto/Droid;
  - that a tab switch has no visible lag.
- **The ES5 guard is a pattern check over comments-and-strings-stripped code, not a parser.** It refuses the ES2015+ syntax and the DOM APIs Android 4.2 lacks that we know of; something newer it doesn't list would slip through. The device check closes that gap.
- **Only the first 12 of `tablet_units` get a chip on the keypad.** More can still be typed with ABC.
- **Windows (on hold):** binding the LAN address on Windows will raise a firewall prompt for `python.exe`, and the Windows rollout must allow port 8090 on the private profile. A Windows hub on a commit before schema 3 refuses this `hub.db` as newer, so both OSes update together.
- **The arm server itself** listens on `0.0.0.0:8080` with no authentication. That's the arm project's existing behaviour and outside BrainHub's scope; it is noted here because the tablet now opens it.
