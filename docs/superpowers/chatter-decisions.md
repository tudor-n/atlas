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
- **CH17 Settings saved by another build** keep working as VI5 rules: the six new `voice_events` keys start on.

## Residuals
- **CH5's idle blind spot:** a Wayland idle protocol client in the shell (`ext-idle-notify-v1`) would fix it, if it turns out to matter.
- **The level numbers are untested on the owner:** the per-level numbers and the cadences in spec §3–§4 are the approved proposal. Tune them from a week at Medium.
- **No real `claude -p` run of patterns A, B or C has been made yet.** The prompts are checked against a fake runner only, and the first real runs are the owner's check (`TODO.md` step 10).
