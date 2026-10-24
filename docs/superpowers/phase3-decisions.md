# Phase 3 build decisions

The build-time calls made while building phase 3 (the workbench tablet, 2026-10-23), where the design (`specs/2026-10-23-atlas-phase3-design.md`) was silent, or where testing showed it should change. Each says why it was made and what it costs if wrong. Where a call here differs from the design, this file wins.

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
- **T10:** `atlas-request` is installed by `integration install`.
  - The wrapper goes in `osdirs.bin_dir()`: `$ATLAS_BIN_DIR`, else `~/.local/bin` on both OSes. It's a `.cmd` on Windows.
  - The `CLAUDE.md` it edits is the one **beside the target `settings.json`**, so `--settings <temp>` keeps tests away from the real one.
  - A wrapper of the same name that isn't ATLAS's is never replaced or removed.
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
  - `?home=` accepts only an `http(s)://` address ending in `/tablet` (never `javascript:`, and no open redirect), and it is **not** remembered: without it the page is unchanged.
  - The review (arm commit `d12853b`) widened "refused while LIVE" to cover every case where leaving would strand the arm:
    - **the state is unknown:** before the first state arrives, and in the first 5 s of losing the server ("WAIT"). Once the server has been out of reach for 5 s, leaving is allowed, since that page couldn't STOP it either;
    - **LIVE is on:** whether already confirmed, or a LIVE-on sent in the last 2 s that the state hasn't confirmed yet ("LIVE ON");
    - **another control is held:** "LET GO". Otherwise a held grip button would keep its target after the page goes, and spin the gripper at the next LIVE.
  - Two fingers on HOME count as one press, and leaving uses `location.replace`, which adds no history entries.

**From the reviews after the build (2026-10-23)**

- **T17, nothing typed is lost:**
  - What is being typed lives in memory first; `localStorage` is only its backup. A WebView with DOM storage off used to lose every value, and reading `window.localStorage` itself is now guarded.
  - NOTES re-renders while the keyboard is open only when requests or notes really changed. SET runs its field's action before any held-back re-render, so the next field it opens is the one on screen. The bug: after an agent event mid-typing, values went into a detached copy and the card showed blanks.
  - New units from Settings swap the chips in place. They used to replace the keyboard and drop the text.
  - Every key press clears a running ⌫ repeat. With two fingers on ⌫, a repeat used to erase the note.
  - A refused note is joined to whatever is in the note box, never written over it.
- **T18, no duplicate entries:** every send from the tablet carries a client id.
  - The hub records it with the entry (`tablet_sends`, part of schema 3, which is unreleased), so a resend after a lost response returns the first entry (`duplicate: true`) instead of writing again.
  - For an answer this check runs before "already answered", so a resend is never turned into a second note.
- **T19, the state's version is read before the data.** A change landing while the state is gathered then makes the next long-poll answer at once. Before, the tablet held a newer version with older data.
- **T20, answers:** answers run one at a time (`answer_lock`), so simultaneous answers land once and the rest get 409. Once an answer is in the raw log, the reply is 201 even if closing the request fails; a 500 would make the tablet resend and duplicate it.
- **T21, held answers:** an answer held on a read-only root carries its request id (`holding.hold(request=…)`). The startup flush links it to the request if that is still open.
- **T22, `CLAUDE.md`:**
  - Our block is found only as whole marker lines, and only as exactly one BEGIN followed by one END. Anything else is refused (`IntegrationError`, nothing changed). A missing END marker used to let a reinstall delete the owner's text between stray markers.
  - The block stays where it is, and the file keeps its own line endings (CRLF too: it's read as bytes).
- **T23, the wrapper:**
  - A foreign `atlas-request` is found before `settings.json` is touched.
  - On Windows an extensionless sh shim goes beside the `.cmd`, because Claude Code's Bash tool there is Git Bash, which never tries `.cmd` for a bare name. A `%` in a path is doubled for cmd.
- **T24, pairing:** every failure gives the same message, so it never tells a neighbour whether a code is live. Codes are compared as bytes (a non-ASCII code was a 500), and `redeem` holds a lock. The cookie carries `Expires` as well as `Max-Age`.
- **T25, the LAN guard:**
  - A request whose local socket can't be read is judged as LAN, the stricter side.
  - Paths with `/..` or `//` are refused outright.
  - On a read-only root, an unpaired LAN request learns nothing about the drive (a plain 503).
- **T26, robustness:**
  - The tablet exports are written atomically, and under the store lock.
  - `LanSite` stops a site whose bind failed; each retry used to leak one.
  - IGNORE, restore and File this need setup (409 before), like the rest.
- **T27, the page:**
  - JOURNAL shows "Can't reach ATLAS. Retrying…" and dims what it shows while the hub is out of reach (design §6.5).
  - ROBOT looks again before refusing on a stale "offline".
  - The page fits itself again on resize.
  - Ctrl, Alt and Meta combos are left to the browser.
  - An ignored request's drafts are dropped.
  - The tap highlight is transparent, `rgba(0,0,0,0)`, as in the arm UI. An opaque one would cover the press feedback; it's the lint's one allowed alpha.
  - The PC keeps a valid pairing code on screen across a Save or a revoke.
  - The ES5 lint now also refuses default parameters, destructuring, shorthand methods, computed keys, `NodeList.forEach`, `Element.matches` and listener option objects.

## Residuals (to check on the device or later)

- **On the SM-T110, with Fully Kiosk:**
  - that WebSocket really is absent (it doesn't matter: the page never uses it);
  - that the `bh_device` cookie survives a Fully Kiosk restart (if it doesn't, the tablet shows its pairing screen again);
  - that the glyphs `⌫ ↵ ⇧ ⇪ ⌄ ‹ → · − Ω` render in Roboto/Droid;
  - that DOM storage is on in Fully Kiosk: drafts and the send queue survive a reload only through it (without it they live in memory until the page reloads);
  - that a tab switch has no visible lag.
- **The ES5 guard is a pattern check over comments-and-strings-stripped code, not a parser.** It refuses the ES2015+ syntax and the DOM APIs Android 4.2 lacks that we know of; something newer it doesn't list would slip through. The device check closes that gap.
- **Only the first 12 of `tablet_units` get a chip on the keypad.** More can still be typed with ABC.
- **Windows (on hold):** binding the LAN address on Windows will raise a firewall prompt for `python.exe`, and the Windows rollout must allow port 8090 on the private profile. A Windows hub on a commit before schema 3 refuses this `hub.db` as newer, so both OSes update together.
- **No rate limit for a paired device:** a paired (or stolen) cookie can send unlimited notes, each up to 20,000 characters, into the append-only log. Revoke it in Settings.
- **The arm server itself** listens on `0.0.0.0:8080` with no authentication. That's the arm project's existing behaviour and outside ATLAS's scope; it is noted here because the tablet now opens it.
