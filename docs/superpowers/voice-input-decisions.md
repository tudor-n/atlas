# One input and ATLAS's replies: build-time decisions

The owner asked for this on 2026-10-25: "add some more personality to atlas". Their request had three parts:
- **One input.** "note down" and "retrieve/ask" become one, and its intent is analysed.
- **Spoken replies.** After something is extracted or found, ATLAS says a line, with sass on or off ("extracted possible tasks, look over them and pretend you're not gonna procrastinate them", "found the entry, look over it will you?"). The same voice settings govern these lines as every other voice line.
- **The dot.** On the combined screen, ATLAS is the logo's blue dot, without the ring and squares. It grows and shrinks while ATLAS talks.

The owner's picks:
- the ATLAS tab plus a smart capture bar;
- instant intent rules, with a switch to flip a wrong guess;
- the dot follows the voice's real loudness;
- each reply also shows as a caption.

These rulings amend the phase 4 design (`specs/2026-10-23-atlas-phase4-design.md`, §1 and §4.1: which events speak) and the phase 4.5 design (`specs/2026-10-24-atlas-phase4_5-design.md`, §2 Motion, §3 tabs, §4 Ask). Numbered VI1….

## The one input
- **VI1 Where it lives (owner).**
  - **The ATLAS screen.** The ASK tab is now the **ATLAS** screen: `#/atlas`, Alt+2. `#/ask` still lands there, so old links keep working.
    - Top: the dot and its caption.
    - Then one field with **NOTE | ASK** and a button that reads SAVE or ASK.
    - Below: what was done, then the result. A note shows its confirm card; a question shows the MATCHES rail, the cited ANSWER and DIG DEEPER, as Ask had them.
  - **The bottom capture bar** stays on every other view, in reduced mode too. It runs the same intent check:
    - a note saves as before;
    - a question opens ATLAS on it (`#/atlas?q=…`; the screen drops `q` from the address once it has asked, so a reload doesn't ask again).
  - **On the ATLAS screen the bar steps aside** (`.shell.one-input`), so only one field shows, and Alt+C focuses the screen's own field.
  - **The widget's field** runs the same check (see VI10 for how its questions reach the screen).
- **VI2 The intent (owner: instant rules plus a flip).** `web/js/intent-model.js` (pure, node-tested) has no model call and costs nothing. English only, like recall's stopwords.
  - **A question** is short: one line, at most 160 characters, at most two sentences. It also does one of these:
    - ends with `?`;
    - opens with a question word and an auxiliary ("what did", "when's", "how many");
    - opens with an auxiliary and a subject ("did I", "is there"). A noun subject counts only after auxiliaries an imperative can't start with, so "did the parcel come" asks but "do the dishes" doesn't;
    - opens with a recall verb aimed at what was written: "find my notes…", "search for everything…", "show me what…", "tell me about…", "list my…", "remind me what…".
  - **With two sentences, the last one decides:** "The servo buzzes. Did I order a spare?" asks.
  - **A task phrasing in any sentence makes it a note, even with a `?`:** "remind me to…", "need to…", "todo", "don't forget", "remember to", "note to self". So "Is it the MG996R? Need to order two more by Friday" is a note.
  - **A to-do that only starts like a search is a note:** "Find a better bearing for joint 3", "Search for cheaper servos", "List of parts…", "Tell me later, …".
  - **Anything longer is a note**, whatever it holds. A note is the safe side: it is in the log either way.
  - **NOTE | ASK follows the guess as you type.** A press overrides it until the field is emptied, including a press before typing.
  - **Keys:** Enter sends while it says ASK, and is a new line for a note. Ctrl+Enter always sends.
- **VI3 A wrong guess never loses anything.** The screen always names what it did and offers the other way:
  - **"Asked as a question" → SAVE AS NOTE INSTEAD.** It posts the text as an entry.
  - **"Saved as a note" → ASK INSTEAD.** It asks the same text.
    - The note stays in the log: the raw log is append-only (rule 1), and the line says so.
    - Once a text is in the log, asking it shows "It is in your log as a note too." instead of the save button.
  - **When a new note replaces it, a question is asked, or the owner leaves the screen,** the note on screen goes to the tray (the `atlas:tray` event), like a capture's card. Its filing and proposals stay one press away.
  - **Text typed but not sent goes into the capture bar** when the owner leaves the screen (the `atlas:draft` event), added to whatever the bar holds.
  - **A save still in flight when the owner leaves:** if it lands, its card goes to the tray; if it fails, the text goes into the capture bar with the error.
  - **Where the bar can't ask** (reduced mode, setup), a question stays in the bar with the reason, and NOTE saves it.
- **VI11 One primary.** The screen's SAVE/ASK is an outline until the field holds text, as the capture bar's is, so a note's CONFIRM ALL is the one white primary.

## The replies
- **VI4 A fifth voice event, `reply`.** The Settings switch is "Reply when I write something down or ask", on by default. Phase 4 §4.1 had four events; this adds one, as the owner asked. The kinds are:
  - **`tasks`:** "I pulled out two possible tasks." Every item proposal counts (task, deadline, reminder, waiting, next time). Other proposals add "And one more thing to confirm."
  - **`filed`:** "Filed it. Nothing to confirm." or "Filed it. Three things to confirm." (updates and new nodes only).
  - **`found`:** "I found three entries on that." The count is the hits.
  - **`nothing`:** "Nothing on that." That's recall's own words, used when there are no hits or the answer is exactly that.
  - **`failed`:** "That one didn't file. Your words are safe in the log."
  - **Only counts and fixed phrases are spoken.** No proposal text and no answer text are ever read aloud: an agent wrote them (V16 holds).
- **Which entries get a reply:**
  - **Notes get one when the owner is waiting on them:**
    - a capture posted with `reply: true` (the ATLAS screen, the bar, the widget);
    - **RETRY**;
    - **TELL IT WHAT'S WRONG**.
  - **Never a reply:**
    - a restart's requeue (`requeue_unfinished` never adds to `FilingWorker.replies`), so a start never recites old filings;
    - a tablet note (it isn't filed, and it has its readback);
    - dig deeper.
  - **Questions get one only from the ATLAS screen** (`GET /api/ask?…&reply=1`).
- **VI5 The owner's saved switches survive another build.** Their `settings.json` has the four keys from before `reply`, and `voice_events` must name every event. So `load_settings` fills keys added since from the defaults, and leaves out keys it doesn't know (a later build's), instead of the whole dict failing and resetting the switches. A save from Settings still sends exactly the full set.
  - The older builds can't be changed: one that reads the five-key dict resets every voice switch to its default, and a Settings save there writes those back. That happens only if an older build runs after this one has saved Settings (a rollback, or the other OS before its update). Windows is on hold, so in practice only a rollback.
- **VI6 Sass.** With `voice_sass` on, the info line gets a quip from `reply_tasks`, `reply_filed`, `reply_found` or `reply_nothing`. `failed` has no pool. The quips follow these rules:
  - **The picker and timing:** the same picker, cycle and 60 s quip cooldown as every line (phase 4 §5). They are sent at once, like readbacks, never merged.
  - **The voice:** the sarcastic friend only (V-S1). 42 quips are in the seed (12, 10, 10, 10), including the owner's two lines in the quote rules' form: "Look {them} over and pretend you're not gonna procrastinate {them}." and "Look over it, will you?".
  - **New placeholder `{them}`:** "it" for one task, "them" for more, so one task never gets "look them over".
  - **The owner's existing `brain/_voice/quotes.md`** gets the replies' section appended once at startup (`ensure_reply_pools`), in a commit of its own ("add the voice's reply quips"), LF.
    - It is never added when the file already has a `## reply_…` heading or the section's marker comment, and never to an unreadable file.
    - The owner asked for these lines, which is V-S2's say-so.
- **VI7 The caption.** `Announcer.reply` composes the line, quip included, once, and returns it whether or not it was spoken. The hub sends it two ways:
  - as an `atlas.reply` broadcast ({kind, text, entry_id}, quiet on the tablet);
  - for questions, also as the SSE `reply` event before `done`.

  The ATLAS screen shows the latest one beside the dot. So the personality shows while the voice is off, paused, in quiet hours or not downloaded, while the speech itself obeys every gate.
  - A quip shown only as a caption still counts for the cooldown.
  - A read-only root (no voice) captions the plain line.
  - A reply can never fail a save or an answer (V39).

## The dot
- **VI8 It follows the voice (owner).**
  - **What it is:** the logo's centre node alone, `--atlas` `#3ec7ff`. That is the one place the brand allows that blue. It is 88 px, or 64 px below 1100 px, with a radius of 18 of 64 at rest and up to 30.
  - **What the worker sends:** just before a line starts to sound, `voice_worker` sends `{"id","event":"playing","frame_ms":40,"env":[…]}`, the line's loudness. That is each 40 ms frame's RMS against the loudest frame, 0–100, taken after volume and boost. The final `{"id","ok"}` reply is unchanged.
  - **What the hub relays:** `Speaker` turns that into `voice.speaking` {env, frame_ms}, and into `voice.quiet` once the line ends however it ended (done, stopped, failed, worker gone). Both are quiet on the tablet.
  - **How the dot plays it:** `atlas-dot.js` plays the envelope with `requestAnimationFrame`. It takes the square root, so quieter syllables show, swells quickly (35 ms) and settles slowly (110 ms). It settles on `voice.quiet`, when the envelope runs out, or on `ws.close`.
  - **When it stays still:**
    - with `prefers-reduced-motion`;
    - for a line cut before it sounds;
    - at volume 0.
  - It moves for every line ATLAS speaks, not only replies: it is ATLAS talking.
  - **Measured** (headless Chrome, the real voice at 1 % volume): a rest of 18, up to 28.6 on stressed syllables, a dip to rest in the 0.4 s gap before the quip, and back to exactly 18 at the end.
- **VI9 The motion rule, amended.** Phase 4.5 §2 now reads: none apart from the switch knob and the ATLAS dot. `tests/test_web_design.py` enforces this:
  - only `js/components/atlas-dot.js` may run animation frames (the widget's one `requestAnimationFrame(fitWindow)` is ruled in, exactly once: it waits for layout, and moves nothing);
  - no Web Animations or SVG animation anywhere;
  - the dot sets no attribute but its static SVG setup and `r`.

  CSS `animation`, `@keyframes` and any transition but the knob's stay banned.

## The widget
- **VI10 The widget's question waits in the hub.**
  - **Why:** the shell's `open_main` takes a view name only (`valid_view` lets no query into a URL), so the widget can't send `#/atlas?q=…`.
  - **How:** the widget posts the question to `POST /api/atlas/handoff`. That is memory only: the latest question, kept 2 minutes, allowed on a read-only root. Then it opens `atlas`. Its field is cleared only once ATLAS has opened (the main window, or the browser through the hub); if neither opens, the text stays, with the reason.
  - **The screen** takes it with `POST /api/atlas/handoff/take` on arrival, and again when the window comes forward while ATLAS is already open.
  - Both endpoints are PC-only, outside `/api/tablet/`.

## Review fixes (2026-10-25)
An independent review of the branch found the following; each is fixed above:
- **Losing what was typed:** unsent text on leaving ATLAS, a save in flight on leaving, a question in the bar where it can't be asked, a widget question cleared before ATLAS opened (VI3, VI10).
- **A note out of reach:** a note hidden behind a question (VI3).
- **The intent rules:** to-dos that started like searches went to ASK (VI2).
- **The bar on a failed load:** it stayed hidden when the ATLAS view failed to load (now restored).
- **A later build's switch:** an unknown key reset the voice switches (VI5).
- **The guard:** it allowed any animation frame in the widget (VI9).

The review also checked these and found them sound: the `voice.quiet` guarantee, which entries get a reply, the Announcer's locking, the new endpoints against `local_only`, `readonly_guard` and `lan_guard`, the dot's loop, the tray and the quote-file append.

## Residuals
- **The widget's question waits in a single slot.** A second question sent within 2 minutes, before ATLAS took the first, replaces it. The widget clears a question only once ATLAS has opened on it, so in practice the first is taken first.
- **The intent rules are English** and won't catch every phrasing. NOTE | ASK shows the guess before sending, and VI3 makes a wrong one cheap. Collect the misses like filing's calibration cases.
- **Static files carry no `Cache-Control`.** During the check, a page that only changed its hash kept the old module until a real reload. This was already so, and isn't new here. After an update the hub restarts and the page reloads, but a heuristic cache could still serve an old module once.
- **Checked by hand on 2026-10-25** on Kubuntu, in headless Chrome at 1440×900 against a scratch hub. A fake `claude` filed and answered, so no plan usage was spent, and the real voice ran at 1 % volume with no ducking. Everything was deleted afterwards. The checks were:
  - the live NOTE | ASK;
  - a note filed with its spoken reply and caption ("I pulled out one possible task. Look them over…", before `{them}`);
  - a question answered with "I found one entry on that.";
  - "Nothing on that." with its quip;
  - the bar's question opening ATLAS from Today;
  - the widget's question reaching ATLAS through the handoff (`/api/open` stubbed in the page, so no browser opened);
  - the note moving to the tray on leaving.

  The console stayed clean throughout.
- **`tests/test_legacy.py::test_old_hooks_and_status_line_are_replaced` fails in this dev copy** because its path, `~/dev/BrainHub`, contains the old name. It fails the same way on master and passes in CI.
