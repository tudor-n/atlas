# Phase 4 (voice and sass): build-time decisions

Rulings made while building phase 4, as amendments to the plan (`plans/2026-10-23-brainhub-phase4.md`) and the design (`specs/2026-10-23-brainhub-phase4-design.md`). Numbered V1….

- **V1 (Task 1) `voice_speed` takes ints and floats.** The generic rule rejects a value whose type differs from its default. A slider or JSON can send `1` for `1.0`, so `voice_speed` and the blend weights accept any finite non-bool number. NaN and infinity are refused.
- **V2 (Task 1, superseded by V35) Voice ids are checked by shape only** (`bm_george`: two letters, `_`, a name). The voices file may not be downloaded yet when Settings saves, so an id that the file lacks is reported by the worker when it speaks ("unknown voice"), not refused by the settings.
- **V3 (Task 3) `_voice/` is skipped by the notes index too**, not only protected from the filing agent. Otherwise recall could quote the quips as if they were the owner's notes.
- **V4 (Task 3) The quote file is seeded twice:** in setup's own commit (a new brain), and as a startup step with its own commit, "add the voice quotes" (a brain set up before phase 4). It's never overwritten, whatever its state.
- **V5 (Task 3) The seed has 107 quotes** (30 agent_finished, 30 agent_waiting, 25 reminder, 12 readback, 10 catch_up), in a roughly even mix of the three styles, and they follow the wording rules. A test enforces at least 100, plus no colons or dashes.
- **V6 (Task 3) A new cycle never opens with the quote the last one ended on**, so a reshuffle can't repeat a quip back to back.
- **V7 (Task 4) The worker's stdout belongs to the protocol.** `main` duplicates the real stdout for the JSON lines and points file descriptor 1 at stderr. So anything onnxruntime, phonemizer or espeak prints can never corrupt a reply.
- **V8 (Task 4) "stop" cuts only the line playing now.** The Speaker (Task 5) empties its own queue first, so nothing is left behind it. Lines already handed to the worker are at most the one playing, because the Speaker sends one line at a time and waits for its reply.
- **V9 (Task 4) Windows plays with `winsound.PlaySound(…, SND_ASYNC)` and waits out the clip's length**, because a synchronous PlaySound can't be stopped from another thread. Stop is `PlaySound(None, 0)`. It's untested by hand while Windows is on hold, and CI covers only the player's selection.
- **V10 (Task 4) The fake boundary is `BRAINHUB_VOICE_FAKE=<file>`**: no model and no audio, just one second of silence per part, with each request appended to the file. Tests run the real worker process with it, like `fake_cli` for claude. A real-synthesis test runs when `BRAINHUB_VOICE_TEST_MODELS` points at the voice files; it passed on Kubuntu.
- **V11 (Task 5) "Finished" measures the whole turn**, from the prompt (idle or finished → running) to the stop. A permission prompt in the middle (running → waiting → running) doesn't restart the clock. A turn whose start the voice didn't see falls back to the row's `status_since`. A finish from idle (no running seen) is silent.
- **V12 (Task 5) A reminder speaks at its `remind_at` and again at its `due` time**, when they differ. A snoozed item speaks at its `snooze_until` only. The design said "a reminder is due"; a deadline with a lead time should be heard both ahead and on the day. Dedupe is per (item, raw time), so editing a date makes it speak again at the new time.
- **V13 (Task 5) Startup catch-up:** one missed reminder is spoken as itself; two or more become the single "You have N reminders from while I was off." line. The very first run ever only records the time, and so does a clock that went backwards.
- **V14 (Task 5) Merging delays a line by up to 3 s** (`MERGE_S`). That's the price of "Two agents need you" over two lines in a row. Readbacks and the catch-up are never merged and play at once. The gates are checked when a line arrives and again when it's sent, so switching off or pausing during the window silences it.
- **V15 (Task 5) The worker's stderr goes to `<state>/voice-worker.log`** (per OS, appended). The hub keeps only the last error for Settings.
- **V16 (Task 5) AgentTracker's `on_transition` gets the project's name and the notification type only**, never the session's message, title or branch: the voice can't read agent text even by mistake. A failing callback is swallowed, so the Agents panel never loses an event to the voice.
- **V17 (Task 6) "Installed" means the right size**, not a fresh sha256. The hash is checked once, while the file downloads (before the `.part` rename). Hashing 120–350 MB on every look at Settings would be slow, and a file damaged later shows up as the worker's error in Settings.
- **V18 (Task 6) The download uses stdlib `urllib`** (it follows GitHub's redirect to its file host), in its own thread, one at a time. The voices file is shared by both models, so switching to fp32 later fetches only the model.
- **V19 (Task 7) Only the full, writable app has a voice.** On a read-only root, `GET /api/voice` says it's unavailable and the POSTs answer 503. The reduced app already answers every other `/api/` with 503. Saying "saved" or tracking reminders is wrong where nothing can be written.
- **V20 (Task 7) The reminder tick runs whenever the full app's workers do**, from the first second (the startup catch-up), whether or not setup is done: before setup there are simply no items. It writes only the per-OS `voice.json`.
- **V21 (Task 7) Readbacks speak for held captures too.** A held note is saved, on this OS, and gets filed when the root is back. A resend that had already landed returns before the readback, so a line is never read twice.
- **V22 (Task 7) Every voice POST takes a JSON body**, `{}` included: stop and edit-quotes too. That's rule 5's CSRF stance, and the tray sends `{}`.
- **V23 (Task 7) Edit quotes opens the file with the OS** (`xdg-open`; `os.startfile` on Windows) in the owner's default app for `.md` files. Tests inject `app["file_opener"]`.
- **V24 (Task 7) `voice.changed` doesn't wake the tablet's long-poll** (`TabletFeed.QUIET`): the tablet shows nothing of the voice.
- Residual, fixed in review: `test_api_tablet.py::test_state_on_the_pc` failed while something listened on 127.0.0.1:8080 (the owner's own arm server). The test now stubs the arm probe.
- **V25 (Task 8) The `tablet_*` and `voice_*` keys left the generic Preferences list.** They have their own sections, and dict-valued keys (`voice_events`, `voice_blend`) can't be edited as text. Phase 3's tablet keys appeared there as plain text fields, `tablet_lan` included, and saving one of those would have sent a string.
- **V26 (Task 8) Live updates don't wipe unsaved edits.** Settings → Voice redraws its form only when its shape changes (installed, on/off, pause, quote problems, a download starting or ending). Otherwise only the status line follows `voice.changed`. Download progress updates just that line.
- **V27 (Task 8) The widget's speaker is hidden until the voice is installed.** A click switches the voice off or on, or ends a pause. It shows muted during quiet hours, and it re-reads the voice once a minute, because quiet hours and a pause running out send no event.
- **V28 (Task 8) The blend editor shows whole percentages**, and the saved weights are fractions rounded to three places. The worker normalises them anyway.
- **V29 (Task 9) The tray's Pause voice menu has fixed labels** ("For 1 hour", "Until morning", "Resume"), not the design's "Voice paused until 14:05" line. A live label would mean the shell polling the hub. The widget's speaker already shows the pause and its end on hover, and Settings shows it too.
- **V30 (Task 9) The tray POSTs with std `TcpStream`** (`src/voice.rs`): a hand-written HTTP/1.1 request with `Host: 127.0.0.1:<port>` and a JSON body, so `local_only` passes as it does for any client without an Origin. It runs on its own thread with 2 s and 5 s timeouts, so a stopped hub never freezes the menu. No new crate, and `Cargo.lock` is unchanged.
- **V31 (Task 9) The shell's source changed**, so after the merge Settings shows "widget update available" until the owner rebuilds the shell (`TODO.md`).
- **V32 (Task 11, found by hand) The player died with the worker only by luck: it didn't.** The worker started the player in a session of its own, so the hub's kill of the worker's process group missed it. A hub stopped mid-sentence left `pw-play` talking. Now:
  - the player stays in the worker's group, so killing the group takes the player too;
  - the worker stops the player itself the moment its stdin closes. That covers a hub that crashed (SIGKILL) as well as one that stopped.
  
  Its stderr goes to a temp file, not a pipe, so a player that leaves a child behind can't hold up the wait. Checked by hand: SIGTERM and SIGKILL of the hub mid-sentence both leave no process behind. There's a regression test.


## The reviews (Task 12)
Three subagents reviewed the branch: the backend, security and robustness, and the UI and tray. Every finding below is fixed and has a test where one fits.
- **V33 Stop wins over synthesis.** Found by both the backend and the security review.
  - **The bug:** a stop, pause or "voice off" that arrived while a line was still being synthesised was lost (`Player.play` cleared the stop flag), so the whole line played. On the first line, which also loads the model, that window is several seconds.
  - **The worker fix:** a stop counter (`_Stoppable`). Each "say" is stamped with the count when it arrives. A stop bumps the count, so the stamped line is skipped after synthesis, and the player is never started once the count has moved. The check and the start happen under the same lock that `stop()` takes after its bump. Closing stdin (a hub that died) is a stop too, which makes V32 true during synthesis as well.
  - **The hub twin:** a line already taken off the Speaker's queue but not yet written is dropped if a stop came in between (the Speaker's own counter, checked under the write lock).
- **V34 Answer readbacks read only the owner's values** ("412 millimetres. Saved.", or "First, 412 millimetres. Third, 74 centimetres. Saved."). The item labels are written by an agent (`brainhub-request`), and the design says the voice never reads agent text. This drops the parent spec's example, "ID 3 to ID 7, 412 millimetres", on purpose.
  - Residual: an unmapped project is still spoken by its folder name (design §4.1). It's filtered to letters, digits and spaces and capped at 40 characters. Mapping the project in the setup wizard replaces it with the owner's name.
- **V35 Blend voices must be one of the 28 English Kokoro voices** (`config.VOICE_IDS`), not any id-shaped string. The editor can show every saved value, and the worker never meets an unknown voice.
- **V36 Reminders.**
  - An expired snooze speaks again at a later `remind_at` or `due`.
  - A date that isn't plain local time is converted (an offset) or skipped (garbage, a non-string). It never stops the other items.
  - `last_tick` moves only after the items were read, so a locked store loses no window.
  - A tick more than 5 minutes late (the laptop slept) is a catch-up like a start, and a gap of more than 24 h counts as none, while running too.
- **V37 An error stop (StopFailure) ends the turn**: the next prompt's "finished" time starts fresh. A permission prompt mid-turn still doesn't restart it (V11).
- **V38 One session's lines merge into its latest one.** A quick succession of permission prompts is not "two agents".
- **V39 Readbacks can't fail a save**: a voice error after a note or answer was saved is logged, and the tablet gets its normal answer. Quip values from the owner's text are capped at 120 characters.
- **V40 Small ones.**
  - "Voice off" stops the voice only when it is switched off, not on every save while off, so a sample isn't cut.
  - A worker that finishes starting after `close()` is killed.
  - `voice-worker.log` rotates at 1 MB.
  - A clip left by a killed worker is removed by the next one.
  - The espeak data copy is redone after an `espeakng-loader` upgrade, and the long-path fallback is a private per-run temp folder, never a shared fixed one.
  - The tray logs a hub answer that isn't 2xx.
- **V41 Settings → Voice:**
  - After a failed download, the Download button comes back.
  - A download started elsewhere redraws the form.
  - A failed first load retries on the next event.
  - A change saved elsewhere (another tab, the widget) redraws the form, so its Save can't overwrite it.
  - The long-run threshold keeps its exact value (fractional minutes), and the volume slider steps by 1.
  - The status line is a live region, and the blend rows are labelled by number.
- **V42 The voice API tests switch quiet hours off.** They failed between 23:00 and 08:00, and CI runs at every hour, in UTC.
