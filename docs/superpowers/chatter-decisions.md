# Chatter: build-time decisions

The owner asked on 2026-10-26 for "a level of self awareness and chattiness" in ATLAS. The brainstorm, its tradeoffs and the owner's picks are in `plans/2026-10-26-atlas-chatter-brainstorm.md`. The design is in `specs/2026-10-26-atlas-chatter-design.md`. These are the rulings made while building it, numbered CH1….

## The owner's picks (2026-10-26)
- **No hearing, no API key, no local model.** The subscription (`claude -p`) is the only model engine.
- **The sarcastic friend** is the persona.
- **Chattiness levels:**
  - Mute / Low / Medium (the default, 4 unprompted lines an hour) / High, each configurable;
  - plus Very high (debug): no waiting, Try it now, origins in the captions, and it drops back by itself.
- **The subscription:** a master switch, with patterns A, B and C each switchable. B (model-worded briefings) is approved, which amends V16 narrowly.
- **Briefings are spoken.**
- **What else ATLAS does:**
  - constant reminders for things due today;
  - self-awareness about its own project and its settings;
  - playful teasing about its own open to-dos at High and up;
  - variety in the information lines themselves, not only the quips.

## Rulings
- **CH1 Wordings live in their own file,** `brain/_voice/lines.md`, not in `quotes.md`.
  - Every pool declares required and optional placeholders. A wording that drops or invents a fact is left out and reported in Settings like a quote problem.
  - The fixed builders in `voice_lines.py` are the fallback, so a missing or broken file plays exactly what ATLAS said before. The plumbing tests in `test_api_voice.py` remove the seeded file to keep checking the fixed lines.
- **CH2 `~ ` marks a wording that carries its own quip.** No separate quip follows it.
- **CH3 Other agents waiting are counted from the store's rows, not the Agents tracker's.** The tracker calls the voice under its own lock, so reading it back from inside the voice's lock could deadlock. Rows idle longer than `agents_stale_h` don't count.
- **CH4 Over budget, a line is captioned, not put in the tray.** Every line, spoken or not, goes out as `voice.line` and into a 20-line recent list (Settings → Voice at Very high). Mute keeps the captions.
- **CH5 Presence without idle time.**
  - KDE on Wayland answers `GetSessionIdleTime` with NotSupported. So presence is:
    - the lock screen (logind `LockedHint`, else `ScreenSaver.GetActive`);
    - plus what the hub sees the owner do: PC writes, the app's and widget's input ping, a prompt typed to an agent, an answer to a waiting agent;
    - plus `GetLastInputInfo` on Windows.
  - **Residual:** 20 minutes at the PC without touching ATLAS or Claude Code counts as away, so the next action gets a welcome back.
- **CH6 Pattern A writes a hub-owned file,** `brain/_voice/generated.md`, replaced per batch, instead of a section in the owner's `quotes.md`. Both loaders merge it. Its lines are validated against their pool's rules and phase 4's wording rules (`chatter_sub.wording_ok`), and they're never spoken unchecked.
- **CH7 "ATLAS's share of the usage meter" is shown as its runs this week.** The meter has no per-run share. The runs feed the meter through the runner, like filing.
- **CH8 Musings use the `self` switch and pool.**
- **CH9 Reactions aren't budgeted** but come at least a minute apart (none at Mute, corrections and flips only at Low). A pause's reaction is captioned only: a paused voice can't speak.
- **CH10 Due today:**
  - a date-only item counts all day;
  - a timed one stops repeating once its time has passed (Today's Overdue and the existing reminder cover it);
  - one due-today line per tick;
  - an approach slot that passed while ATLAS was off or quiet is skipped, never said late;
  - after the evening's last call only timed items repeat;
  - the first repeat is a cadence after the morning digest, which is the day's roll call.
- **CH11 ATLAS's project** is, in order:
  1. `voice_atlas_project`;
  2. the map project whose folders hold the root (the live copy) or the code's checkout;
  3. the slug `atlas`.
- **CH12 Very high is the one setting the hub changes by itself:** back to the previous level after 2 hours, and at every start. The previous level is kept in this OS's `voice.json`.
- **CH13 The honesty check is strict on purpose** (`chatter_sub.honest`). A failure only costs the template line. These are refused:
  - digits;
  - a number word that isn't one of the sheet's counts;
  - any capitalised word that isn't a name in the sheet or a common sentence opener;
  - braces, colons, dashes and brackets;
  - more than three sentences or 300 characters.
- **CH14 The patterns run `recall_model` with no tools at all,** in `data/chatter/`, which hooks ignore (rule 13). C's suggestions become tasks only on the owner's ✓ (`Reviewer.add_task`, rule 2).
- **CH15 Timing:**
  - A's first batch comes at the first away or quiet stretch, then at most every 20 h;
  - C's first review waits a week from the first start;
  - B words at most 6 briefings a day, only while away, and only when the facts changed.
- **CH16 "Confirm all" / "dismiss all"** means the call took the last pending proposals of the cards it touched, and at least two of them.
- **CH18 No "good morning" after midday.** The morning digest plays within 4 h of the end of quiet hours, or not at all that day. When it's skipped, the first due-today repeat is the day's roll call.
- **CH19 The widget's DEBUG CHATTER tag** reads `chattiness` from `GET /api/voice` (`Announcer.view`).
- **CH20 Hello and goodbye (owner, 2026-10-26).**
  - **The switch:** a seventh switch, `greet`.
  - **When a hello plays:** at a real start only. A start within 3 minutes of the last run's tick is a restart (an update, a crash, the drive coming back), and stays silent.
  - **When a goodbye plays:** when the hub stops for any reason except `EXIT_RESTART`. It waits up to 7 s for the line to finish, inside the shell's 10 s grace after SIGTERM.
  - **The gates:** both ignore quiet hours, because they answer the owner's own action. Mute captions them, and a pause or the voice switch silences them.
- **CH21 How the line is picked:**
  - **The base:** the time of day (early, morning, afternoon, evening, late).
  - **Back after two or more days:** always said.
  - **Back again within 3 hours:** half the time.
  - **Other specials, half the time:** Monday morning, Friday afternoon, the weekend, a run shorter than 15 minutes or longer than 6 hours.
  - **The wordings:** pools in `lines.md` (`greeting.*`, `goodbye.*`), with no quip after them: they are the joke.
- **CH17 Settings saved by another build** keep working as VI5 rules: the seven new `voice_events` keys start on.

## After the reviews (2026-10-26)
Four specialised reviews ran on the PRs: backend correctness, safety and the project's rules, UI and wording, and #22's ducking. Every finding below was fixed, and the confirmed ones have regression tests.
- **CH22 The model writes jokes, never facts (narrows the owner's approval of B and the design of A).**
  - **What the review showed:** the safety review got false and invented content past `honest()`:
    - "Nothing is waiting." while an agent waited;
    - "Rob" passing as a substring of "Robot";
    - "Your bank called.";
    - "a couple".

    It also got pattern A wordings to state facts ("All 12 tests failed.", "It's due tomorrow at 9 am."). A word check can't prove a sentence true.
  - **What changed:**
    - Every fact ATLAS speaks now comes from code.
    - **A** writes quips only, never info wordings.
    - **B** no longer words the briefing. The briefing is code's, and B writes only its closing joke, from counts alone: no names, no item text, no folder names.
    - Everything a model writes must pass `chatter_sub.fact_free`:
      - phase 4's wording rules, at most two sentences;
      - no digit, and no number, time, day, deadline or event word;
      - no capitalised word inside a sentence;
      - for B's joke, only common words may open a sentence.
  - **If the owner wants B to word whole briefings again,** that needs a different safety story, not a better word list.
- **CH23 The welcome back's facts agree in number.** Pools that took a bare `{count}` now take ready phrases (`{reminders}`, `{notes}`, `{suggestions}`, `{changes}`: "one reminder", "three reminders"), and waiting is `fact.waiting` / `fact.waiting_many`. No more "One reminders went by" or "Robot Arm and Atlas is waiting". `{text}` and `{closest}` always end a sentence, so they're never followed by a verb.
- **CH24 The subscription's tick is its own task.** Awaiting a 3-minute `claude -p` run inside the reminder tick delayed reminders and turned them into a false catch-up. Its locked state writes run off the loop.
- **CH25 One line per moment:**
  - a welcome back in the morning stands in for the morning digest;
  - an approach slot is skipped when the item's own reminder speaks in the same 5 minutes;
  - teasing and the stale nudge record an attempt whether it was spoken or captioned, so they wait a cadence instead of re-captioning every tick, and learned restraint holds;
  - reactions and settings remarks carry no quip.
- **CH26 Start and stop:**
  - the update and flushed-notes lines aren't budgeted, so they're spoken at a start, before anyone has been seen;
  - the goodbye goes first in shutdown and only within `GOODBYE_BY` (6 s) of shutdown starting;
  - `wait_quiet` can no longer return between taking a line off the queue and playing it.
- **CH27 Presence:**
  - only a request that succeeded counts, and never one from the tablet's LAN side;
  - `seen` (which may run the welcome back) runs off the event loop;
  - the pages ping every 15 s, so a 1-minute away at Very high never mistakes typing for absence;
  - `voice.line` is quiet on the tablet;
  - on Windows, idle time survives the tick counter's wrap.
- **CH28 ATLAS's week:**
  - ✓/✕ are serialised, so a double click makes one task;
  - the task's project and area must exist in the map;
  - a task from the week (a model wrote its text) is never teased or read aloud.
- **CH29 The UI:**
  - a NOTE/ASK flip is reported once, when the text is sent against the guess, and INSTEAD only when the guess decided;
  - a disabled lines-an-hour field never blocks a save;
  - ATLAS'S WEEK keeps an armed ✕;
  - the recent lines aren't re-read by screen readers.
- **CH30 Wording edits from the review:** the weakest lines were rewritten, and "Fine." and "Bold" were thinned out. Event names read naturally ("teases", "remarks about myself").
- **CH31 From the live test:**
  - the short form is only for the same kind of wait again (a permission after a finished turn says what it wants);
  - "N times today" comes at 3, 5 and 10 only;
  - one waiting nudge per project, however many of its sessions wait;
  - the evening digest names what's still due and says "nothing left hanging" only when it's true;
  - `fact_free` catches "today's" and allows more plain openers for B;
  - a B joke that fails the check shows in Settings (`last_error`).
- **CH32 A hello after any quit (owner, 2026-10-26: "greetings don't start").**
  - **The cause:** the owner quit and restarted ATLAS within seconds, three times. The 3-minute rule took each start for a restart and stayed quiet. It also turned out the installed app runs from a runtime slot, which isn't a git repo, so the update line could never fire.
  - **The quit mark:** a clean stop (the shell's SIGTERM, anything but EXIT_RESTART) leaves `quit_at` in `voice.json`. The next start always says hello, then clears the mark. A crash or a drive-back restart leaves no mark, so those stay quiet.
  - **The update line:** the running commit comes from the runtime's `current.json`, and commits are counted in the live copy (read-only git).
- **CH33 Smoother speech (owner, 2026-10-26: "lines go over one another non-fluidly", "the first word gets cut off").**
  - **Start padding:** every clip starts with silence, `voice_lead_in_ms` (default 350 ms, 0–1500, Settings → Voice → START PADDING). An output waking from PipeWire's suspend loses the first few hundred ms of a stream; now that's silence, not the first word. The dot's envelope starts with the same silence, so it waits too.
  - **One clip for lines that come together:** the Speaker waits `JOIN_S` (0.6 s) for a line's companions, then says every queued line with the same voice settings as one clip, up to 8 parts, with the usual 0.4 s gaps. The sample is never joined.
  - **ATLAS tasks:** several confirmed together get one line ("Two tasks about me. I'm flattered."). A second batch within 2 minutes is shown, not said again (`chat(speak=False)`).
- **CH34 Measurement requests are said aloud (owner, 2026-10-26: "if I'm working I might not see the tablet notification").**
  - **The event:** `request`, with its own switch, on by default.
  - **What's spoken:** the project (from the map, else its folder, as agent lines do) and how many: "Robot Arm wants three measurements. They're on the tablet." Never the request's title or labels: an agent wrote those (V16).
  - **The gates:** like the phase 4 events, it isn't budgeted, but it follows the gates and quiet hours.
  - **The nudge:** a request still open after 10 minutes gets one, said once, which counts against the budget.
- **CH35 New pools reach an older `lines.md`.** On a start, pools the file lacks are appended once, and their names are recorded on an `ADDED_MARK` comment line, so a pool the owner deletes stays deleted. The owner's live file, seeded by #23, gets the pools added since (the requests, several ATLAS tasks, the evening's still-due line, waiting_many).

## Residuals
- **CH5's idle blind spot:** a Wayland idle protocol client in the shell (`ext-idle-notify-v1`) would fix it, if it turns out to matter.
- **The level numbers are untested on the owner:** the per-level numbers and the cadences in spec §3–§4 are the approved proposal. Tune them from a week at Medium.
- **No real `claude -p` run of patterns A, B or C has been made yet.** The prompts are checked against a fake runner only, and the first real runs are the owner's check (`TODO.md` step 10).

## Checked by hand (2026-10-26)
- **The setup:** a scratch hub on port 8098, with:
  - a temp root, state and Claude folders;
  - stand-in voice files with the fake voice worker (`ATLAS_VOICE_FAKE`), so nothing played;
  - a fake `claude` on `PATH`, so no plan usage was spent.
- **The checks:**
  - setup seeded `lines.md`;
  - Very high announced itself;
  - every Try it now fired: briefing, the morning and evening digests, musing, self, react, and teasing once ATLAS had a to-do;
  - each line showed its origin (`_voice/lines.md:<n>` or fixed) and whether it was spoken;
  - pattern A wrote and committed `generated.md`;
  - pattern B's worded briefing passed the honesty check and was spoken;
  - pattern C wrote `data/chatter/week.json`;
  - ✓ on its suggestion made an open task in ATLAS's project, committed as "confirm: 1 item (ATLAS's week)".
- **What it caught:** Try it now's self line said "zero percent" with no usage reading (fixed).
- **Afterwards:** the hub (only its own PID) was stopped, and the folders deleted.
- **In a browser:** the UI was checked in headless Chrome by the subagent that built it.
- **Hello and goodbye, checked by hand (2026-10-26):** on a scratch hub with the fake voice worker, stopped with SIGTERM as the shell does:
  - a quit said "Go and eat something. A real meal.";
  - a restart within 3 minutes said nothing;
  - a start after an hour said "Back so soon. I'm flattered.";
  - its quit said "Bye. Everything's saved. Go relax.".
- **Live test (2026-10-26, after review).** A scratch hub on port 8097 with temp state, config, shared and bin folders, and the voice downloaded into the temp root. A stand-in `pw-play` saved each clip, and a do-nothing `pactl` with ducking at 100 % meant nothing sounded and no music was touched. The real Claude login was used with the real config folder.
  - **Hook events:**
    - real hook events gave a kind-specific line each (done, question, error), with others counted;
    - this run found the short-form and callback issues in CH31.
  - **Very high Try it now:** every kind of line fired, worded from `lines.md`.
  - **Clips:** 29 real ones, 1.7–7.5 s at −17 dBFS, none silent.
  - **The patterns, with real `claude -p` (Haiku):**
    - A wrote 37 fact-free quips in 11 s;
    - B's joke passed for one sheet and was refused for another (found the opener issue);
    - C wrote an accurate week with one sensible suggestion in 7 s.
  - **Quit and restart:**
    - SIGTERM: the goodbye played, and the hub exited in 0.9 s;
    - a restart after a faked two hours: Very high dropped back to Medium, and a hello played.
  - **Headless Chrome (a throwaway profile):** Settings → Voice at Very high and the ATLAS screen with ATLAS'S WEEK rendered correctly.
  - **Afterwards:**
    - `~/.claude/settings.json` hash unchanged;
    - no new `~/.claude/projects` folder;
    - the scratch folders deleted.
