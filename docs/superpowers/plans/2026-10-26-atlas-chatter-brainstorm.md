# ATLAS: self-awareness and chattiness: ideas, angles, tradeoffs

## Context

You want ATLAS to feel more alive: aware of itself and its situation, and more talkative. You've set three hard limits:
- **No hearing.** No mic, no VAD, no wake word, no push-to-talk. ATLAS speaks; you type.
- **No API key.**
- **No local model.** Claude runs only through your subscription, via Claude Code (`claude -p`), as filing and recall do now.

Here is what exists today, from the specs and the code:
- **Voice out only.** kokoro-onnx runs in a child process. Five events speak: `agent_waiting`, `agent_finished`, `reminder`, `readback` and `reply`. They pass through `Announcer._gate` (`atlas/voice.py`: enabled, event ticked, quiet hours, pause, dedupe), a 5-line queue and a 60 s cooldown on quips.
- **Sass is pre-written.** It comes from `brain/_voice/quotes.md` pools (`atlas/quotes.py`), with placeholders filled from real values. The dot follows the voice (VI8).
- **Rules already on the books:**
  - V16: the voice never reads agent-supplied text.
  - Replies speak counts and fixed phrases only.
  - Recall never says anything you didn't write.
  - The anti-goal: *"It nags: noisy notifications, chatty voice…"*. Every idea below is judged against "does it nag?" and "is it more of a chore than paper?".

So the only engines are **templates plus code** (instant, free, offline) and **`claude -p` on the subscription**. That one is slow: the CLI starts in 0.6 s, and recall's first text came at 1.4–3.2 s. It also uses up the 5-hour and weekly limits your coding agents share. The design question is how to get the most "alive" out of the first, and spend the second where its slowness doesn't show.

---

## 1. What "self-awareness" can mean here (material ATLAS already has)

Lines **built from real state** feel intelligent and can't make anything up.

| Source | Example facts | Where it lives |
|---|---|---|
| Its own body | uptime, restarts, an update pulled this morning, voice worker up or down, read-only root, D: missing or back | `launcher.py`, `update_step.py`, `rootguard.py`, `voice.py` |
| Its own work | filings today, average filing time, failures, retries, "TELL IT WHAT'S WRONG" corrections, NOTE↔ASK flips (its intent misses) | `filing.py`, `store.py`, `review.py` |
| Its own usage | its share of the 5-hour/weekly window (its `claude -p` runs already report `rate_limit_event`) | `usage.py`, `claude_runner.py` |
| How it's been treated | paused N times today, voice switched off, quips skipped by the cooldown, Settings changed | `voice.json`, settings |
| Your world | agents running/waiting and for how long, the usage %, overdue/waiting-on items, time since the last capture, time of day, the hot project | `agents.py`, `today.py` |

There are three tiers. They build on each other.
1. **State-aware lines (templates):** new pools fed by the facts above. "That's my fourth filing this hour. You're prolific today."
2. **An episodic memory:** a small `voice_memory.json` of what it said and what happened, which enables callbacks. "Second time the arm build has stalled today." "You moved my last two filings out of errands. I'm sensing a theme."
3. **Subscription-written lines:** the model writes or words lines from a fact sheet, ahead of time or in the background, never on the critical path (§3).

---

## 2. The axes, with tradeoffs

### 2.1 Gimmick vs genuinely useful

| | Gimmick (personality as a skin) | Useful (personality carries information) |
|---|---|---|
| What it is | more quips, random musings, greetings | every line carries a fact you'd want; the quip is seasoning |
| + | cheap, fun, safe | earns its airtime; survives the "does it nag?" test |
| − | novelty wears off in about a week; repetition breaks the illusion; it's exactly the "chatty voice" anti-goal | needs trigger logic and thresholds to tune |
| Verdict | a **little**, behind its own switch ("idle remarks", off by default) | **the backbone.** Info first, quip after, as phase 4 has it |

The test for every line: **would you want it if it had no joke?** If not, it goes behind the gimmick switch.

### 2.2 How proactive

There are five levels. Each adds value and nag risk.

| Level | Example | Value | Nag risk |
|---|---|---|---|
| 0. Reactive (today) | replies, agent waiting, reminders | ✓ | low |
| 1. State-change remarks | "Usage just crossed eighty percent. I'll keep my own thinking short." / "D is back. Your held notes are filed." | high | low (rare events) |
| 2. Welcome-back briefings | you return after 20+ min idle: "Welcome back. Robot arm finished, ATLAS is waiting on you, two reminders passed." | **highest** | low if ≤ 3 facts and once per absence |
| 3. Nudges | an agent has been waiting 10 min (one repeat, then silence); a waiting-on item 5 days stale; nothing captured all day | medium–high | **high**: needs a budget |
| 4. Initiative | "You've mentioned servos three times this week. Want a parts list?" | potentially high | highest: a model's guess, spoken uninvited |

**What makes levels 2–4 safe:**
- **A chattiness budget:** a cap on unprompted lines per hour, set by the Chattiness level (§2.4). The default is Medium, 4 an hour. Over budget, a line goes to the caption and the tray, not the speaker.
- **Presence from the desktop, not a mic:**
  - KDE idle time via `org.freedesktop.ScreenSaver` / KWin, or the last input seen by the hub or the widget.
  - It speaks unprompted only when you're at the PC, and briefs you once when you come back.
- **Learned restraint (self-aware *and* useful):**
  - Each pause, mute or voice-off within 30 s of an unprompted line counts as a "shh" for that kind of line.
  - Three shhs halve that kind's rate for a week.
  - ATLAS says so once, in a caption or a line: "Noted. I'll keep the commentary on agents to myself."
- **Level 4 is never spoken first.** It appears as a suggestion card that needs your ✓, like filing proposals. The voice only says "I had a thought. It's on the ATLAS screen."

### 2.3 Things due today: constant reminders (owner)

Today, a due item speaks once: at its time, or at the end of quiet hours when it has no time (phase 4 §4.3). The new event, `due_today`, keeps today's open, confirmed items in your ear until they're done or snoozed.

- **When it speaks:**
  - **in the morning:** in the digest ("Three things are due today. The networks report, the servo order, and calling the dentist.");
  - **through the day:** a repeat at the cadence the Chattiness level sets (table in §2.4);
  - **sooner as the deadline nears:**
    - for an item with a time: at 2 h, 1 h and 15 min before;
    - for an item without a time: a final call at a set evening hour ("Last call. The servo order is still open today."), a setting with a default of 18:00.
- **What it reads:** your own words only (the item text, as `reminder` does now), with counts in words. With several items, the repeat names the count and the most urgent one: "Two things still due today. The networks report is the closest."
- **What stops it:**
  - done, dismissed or snoozed (a snooze moves the item off today's list until `snooze_until`);
  - quiet hours and pause, as every line;
  - the voice switched off.
- **Its relation to the budget:** it is **exempt from the hourly cap**, so chatter can never crowd out something that's due. It uses its own cadence instead, and dedupes per item per slot.
- **Its own Settings switch:** "Keep reminding me about things due today", on by default. With it off, only the existing single `reminder` plays.
- **Sass:** a `due_today` pool. Quips get drier as the day goes on, and one quip per item a day at most.

### 2.4 The Settings: everything configurable (owner)

**Chattiness: Mute | Low | Medium (default) | High | Very high (debug).** One control on the Settings page, with a preview of what each level means:

| | Mute | Low | Medium (default) | High | Very high (debug) |
|---|---|---|---|---|---|
| Unprompted lines an hour (briefings, self-reports, nudges, remarks) | 0 | 2 | **4** | 8 | no cap |
| Due-today repeats (§2.3) | off (only the existing single reminder) | every 3 h | every 2 h | every hour | every 2 min |
| Reactions to what you do | off | mistakes only | all | all | all |
| Idle musings, callbacks, mood | off | off | callbacks, mood | all, musings included | all, musings every few minutes |
| Quips on the new events | off | 1 in 3 | as today (cooldown) | as today (cooldown) | every line, no cooldown |

Notes on the levels:
- **Mute** silences everything this plan adds. The phase 4 events (agent waiting, finished, reminder, readback, reply) keep their own switches, as they have now.
- **Learned restraint** lowers rates *within* a level, never above it, and resets when you change the level.
- A raw "lines an hour" override sits under the level, for fine-tuning.

**Very high (debug) (owner): see every new line and feature without waiting.**

Every wait this plan adds collapses:
- **Limits and cooldowns:** no hourly cap, no 60 s quip cooldown, and learned restraint is ignored (shhs are still recorded).
- **Waiting for you to step away:**
  - "welcome back" fires after 1 min idle instead of 20;
  - the morning and evening digests play on the next tick after you pick the level;
  - presence gating is off.
- **Pools:** each pool plays its lines in file order, so a full pass shows every line once, the generated ones included.
- **Due today:**
  - repeats every 2 min;
  - the 2 h / 1 h / 15 min slots fire one after another, 20 s apart;
  - the last call fires right after them.
- **Quiet hours:** ignored. Pause and voice off still work, so you can always silence it.

**A "Try it now" panel**, shown only at this level:
- a button per new event (`briefing`, `self`, `nudge`, `react`, `due_today`, musing) that fires it at once from current real state;
- with Subscription use on, buttons that start pattern A (a quip batch), B (a briefing composed now) and C (the weekly self-review) right away, bypassing their schedule and the usage threshold. The run is shown live, like the filing card's progress.

**Each caption also shows what produced it:** the event, the pool and line number in `quotes.md` (or "generated" / "template fallback"), and why it fired. That makes a bad line easy to find and edit.

**So it's never left on by accident:**
- the Settings row and the widget show a "DEBUG CHATTER" tag;
- the level drops back to the one before after 2 hours, or when the hub restarts.

It's a normal level in the same setting, not a hidden flag, so tests and the scratch hub use it too.

**Subscription use: On (default) | Off.** It's one master switch, separate from Chattiness:
- **On keeps every feature.** Patterns A, B and C (§3) each have their own switch under it, all on by default.
- **Off keeps only the necessary ones.** Everything in Step A still works on templates:
  - briefings;
  - due-today reminders;
  - self-reports;
  - reactions;
  - restraint.

  The quip batches, the background-written briefings, the weekly self-review and the suggestion cards stop. Filing and recall are unaffected: they *are* the necessary subscription use.
- **Shown beside it:** ATLAS's own share of the usage meter this week, so the cost is visible. "Skip above N % of the 5-hour window" is also a setting, default 80 %.

**The per-event switches** extend `voice_events`: `briefing`, `self`, `nudge`, `react`, `due_today`. A new key is filled from the defaults on load (VI5 already handles keys added in a later build).

### 2.5 Self-awareness about itself: its project and its settings (owner)

ATLAS notices when you work *on it*.

**Which project counts as "ATLAS":** the brain map's project whose folders include the ATLAS repo (`~/dev/BrainHub`, `/mnt/data/ATLAS`). If there is none, the project with slug `atlas`. It is overridable in Settings ("ATLAS's own project"). `Item.project` (`atlas/models.py`) and the Agents rows' project already carry it.

**When it fires, with example lines in the sarcastic-friend voice:**

| Trigger | Where it's detected | Example line |
|---|---|---|
| A note filed into the ATLAS project | `FilingWorker` result: the entry's project | "A note about me. I'm touched. Filed it." |
| A task about ATLAS confirmed | review confirm (`review.py`) | "You've put me on your to-do list. Finally, some recognition." |
| A Claude Code session starts working in the ATLAS repo | `agents.py` transition, the project = ATLAS | "You're operating on me again. Mind the voice module." |
| That session finishes | `agent_finished` for the ATLAS project picks a self-aware pool | "ATLAS has finished. It took forty minutes. I feel different already." |
| An update was pulled on start | `update_step.py` result | "I've been updated. Three new commits. I feel lighter." (counts only, never commit text: V16) |
| Settings changed | `put_settings` already loads `before` (`routes.py:263`), so it diffs `before`/`saved` | see below |
| `quotes.md` edited by you | `quotes.py` already detects an edit to reshuffle | "You've been editing my lines. I'll pretend I didn't notice." |

**Settings reactions.** One line per burst of changes, waiting 5 s after the last save, about the biggest change:
- **chattiness down:** "Low. Message received."
- **chattiness to Mute:** caption only, "I'll sulk silently.";
- **chattiness up:** "High. You'll regret this.";
- **speed up:** "Faster. Fine. I'll talk like I'm late.";
- **sass off:** spoken plainly, with no quip, as "Sass off.", because the joke is that it obeys;
- **an event switched off:** "No more reminders from me. Bold.";
- **Subscription use off:** "Back to my own wits, then.";
- **quiet hours changed:** "New bedtime. Noted.".

All of these are `self` lines: they count against the hourly cap, follow the Chattiness level (Low: settings and update reactions only; Medium and up: all), and run on templates, so they work with Subscription use Off.

### 2.6 Playful nudges about ATLAS's own to-dos (owner, at High and above)

At **High or Very high**, ATLAS teases you about its own open items, whether or not they have a date.
- **What counts:** open, confirmed items in the ATLAS project, of any kind (task, deadline, reminder, waiting, next time). An item due today is left to `due_today` (§2.3), so it never plays twice.
- **What it says:** your own words (the item text) plus how long it's been open, in the sarcastic-friend voice. "That bit about my intent rules has been open for six days. Not that I'm counting." / "Still waiting on you to fix my widget. I'll just be here. Small."
- **How often:**
  - **High:** one every 3 h while you're at the PC, at most 3 a day, oldest item first and rotating, each item at most once a day;
  - **Very high:** every few minutes, plus a button in "Try it now".
- **Rules:** it counts against the hourly cap and stops for an item once it's done, dismissed or snoozed. Its own switch is "Tease me about ATLAS's to-dos", on by default, effective only at High and above.

### 2.7 Variety in the information itself, not only the quips (owner)

**The problem today.** Only the quip varies; the info line is one fixed template per event (`atlas/voice_lines.py`).
- `agent_waiting_line` always says "{project} needs you.", with "It's asking for permission." added for a permission prompt.
- When a Claude Code session finishes its turn and waits for you (`idle_prompt` → `waiting`, `agents.py:174`), that's the line you hear every time, and it doesn't even say the agent *finished*.

**The fix has four parts:**
1. **Each kind gets its own wording.** `agent_waiting` splits by `kind`, already in the transition:

   | Kind | Example variants (sarcastic friend, same facts) |
   |---|---|
   | `idle_prompt`: done with its turn | "Robot arm's done. Your move." · "Robot arm finished and is looking at you." · "Robot arm is back with something for you." · "Your turn on robot arm." |
   | `permission_prompt` | "Robot arm wants permission." · "Robot arm needs a yes from you." · "Robot arm is asking before it does something." |
   | `stop_failure` | "Robot arm hit an error and stopped." · "Robot arm fell over. It needs you." |

2. **Variant pools for every info line,** not just this one: `agent_finished`, `reminder`, `due_today`, `readback`, `reply`, `briefing`, `self`, `nudge`.
   - **The picker:** the same shuffle-and-cycle picker as quips, so no variant repeats until its pool has cycled.
   - **Facts are fixed:** every variant must hold the same required placeholders (`{project}`, `{duration}`, `{count}`, `{text}`) and nothing more. A variant that drops or adds one is rejected when the file loads, with a quiet warning in Settings, as a broken quote is today.
   - **Where they live:** a new owner-editable file `brain/_voice/lines.md`, seeded once and never overwritten, a protected path like `quotes.md`. Each event's pool has a heading (`## agent_waiting idle_prompt`).
   - **The current fixed line is the first variant,** and also the fallback if the file is broken.
3. **Context changes the shape, not only the words.** These are real facts the hub already has:
   - **how long the turn took** (from `_turn_start`): "Robot arm finished after twelve minutes. Your move.";
   - **others waiting:** "Robot arm needs you. That makes two waiting.";
   - **the same agent again soon:** within 10 min of its last line, a short form, "Robot arm again." / "Robot arm. Again.";
   - **a burst:** the existing 3 s merge ("Two agents finished. Robot arm and Atlas.") also gets variants;
   - **time of day and your absence:** at a welcome-back, waiting agents fold into the briefing instead of each speaking.
4. **Sometimes info and quip become one sentence** when the sarcastic friend phrasing allows it: "Robot arm's done. Shocking, I know." The quip pools stay for the rest. Phase 4's rule (info first, never phrased as part of the quip) still holds: the info is always the first clause.

**What else touches this:**
- **Pattern A** (the nightly batch) may add info variants too, under the same placeholder check, so the wording keeps refreshing.
- **Very high (debug)** walks the variants in file order, like quips, and the caption names the pool and line.
- **Mute** doesn't remove the variety: the phase 4 events still speak, just in varied words.

### 2.8 Keeping the illusion of intelligence (without hearing)

Since you can't talk back by voice, the "conversation" is: ATLAS speaks → you act or type → ATLAS reacts. What makes that feel smart:
- **Reacting to what you do, not only to what happens:**
  - you confirm all proposals: "Efficient.";
  - you drop all of them: "Fair. They were optimistic.";
  - you flip NOTE→ASK: "My mistake. Asking.";
  - you pause it: "Going quiet.".
  These are cheap, use templates and feel responsive.
- **Restraint:** silence at the right moment reads as judgment. The budget and learned restraint *are* intelligence theatre.
- **Callbacks** from the episodic memory.
- **Timing:** never talk over itself; keep the pause before the quip (the existing 0.4 s gap); hold an unprompted line while another app plays audio (a PipeWire sink check, output side only).
- **Owning its mistakes:** "I filed that under errands. You disagreed. Fair."
- **One persona: the sarcastic friend (owner).** Every new pool and every generated or worded line (patterns A and B) is written in it, as the reply quips already are (VI6, V-S1). The existing hand-written mixed pools in your `quotes.md` are left as they are; the file is yours.

What breaks it:
- **Repetition:** about 10 lines per pool repeat within days.
- **Wrong facts:** these also damage trust in recall.
- **Generic model voice:** "Great question!".
- **A visible pause before a one-liner.**

**The honesty rule.** Facts come from code; the model only words them. Any model-written line is checked before it's used, and a fixed template line plays instead if it fails. The checks:
- every number and name in it must appear in the fact sheet;
- placeholders only;
- within the length limit and phase 4's wording rules (no colons or dashes).

Agent text never goes into the fact sheet.

---

## 3. Spending the subscription where its slowness doesn't show

`claude -p` is too slow and too precious to call per spoken line. There are three ways to use it without a visible pause:

| Pattern | What it does | Latency seen | Usage cost | Risk |
|---|---|---|---|---|
| **A. Nightly or weekly quip batch** | at a quiet hour, with room in the 5-hour window: given the week's *structured* themes (project names from your map, counts, what you corrected), it writes ~10 fresh templated lines per pool into a `## generated` section of `quotes.md`. Validated, then committed like the reply pools | none: runtime stays templates | one run a night or a week | low: placeholders only, prunable, never on the critical path |
| **B. Pre-composed briefings** | when you go idle (or at the end of quiet hours) the hub snapshots the facts and has `claude -p` word a 2–3 sentence briefing in the background; it's ready when you return. If it isn't ready or fails the check, the template briefing plays | none: it's written while you're away | one short run per absence, capped (e.g. ≤ 6 a day; skipped above 80 % usage) | medium: needs the honesty check |
| **C. A weekly self-review ("ATLAS's week")** | a page on the ATLAS screen, written from stats: what it filed, what you corrected, its misses, its usage, which pools you muted, plus one or two suggestions as ✓ cards. Spoken: one line pointing at it | none | one run a week | low; also feeds filing calibration (the README's "next" item) |

All three sit under **Subscription use** (§2.4): each has its own switch, and Off turns all three off at once.

**Self-aware about its own spending:**
- ATLAS knows its share of the usage meter. It skips B and C when the window is tight, and can say so: "You and your agents are at eighty percent. I'll skip my weekly navel-gazing."
- **V16 amended (owner approved pattern B):**
  - ATLAS may speak a briefing a model worded, but only if it passes the honesty check (§2.8) and its fact sheet holds no agent text.
  - Anything else an agent wrote is still never spoken.
  - Pattern A writes pool templates only. Their placeholders are filled from real values, as today.

---

## 4. The idea catalogue

Tags: **U** useful · **G** gimmick · **T** templates · **S** subscription.

1. **Welcome-back briefing (U, T, then S via pattern B):** presence-triggered, at most 3 facts, once per absence. *The best value-to-nag ratio.*
2. **Morning/evening digest (U, T):** at the end of quiet hours: due today, waiting-on, agents overnight. Evening: "Four notes filed. Two tasks still unconfirmed."
3. **Self-report on its own state (U, T):** updated, voice worker restarted, D: missing or back, its share of usage. "I was updated overnight. I'm told I'm better now."
4. **Reacting to your actions (U/G, T):** confirm all, drop all, flip intent, pause, voice off and back on. Short, instant, makes it feel attentive.
5. **Owning mistakes (U, T plus the memory):** acknowledge corrections and intent flips. The same events are logged as calibration cases for `prompts/filing.md` and `intent-model.js`.
6. **Usage self-awareness (U/G, T):** "Eighty percent of the window. I'll stop thinking out loud."
7. **Learned restraint (U, T):** pauses and mutes shrink its own chattiness, and it says so once.
8. **Callbacks or running gags (G with charm, T plus the memory):** "Third servo order this month."
9. **Stale nudges (U, T, budgeted):** an agent waiting 10 min (one repeat); a waiting-on item over N days; one gentle line a day at most.
10. **Mood tied to real state (G, T):** terse when 3 agents are waiting or usage is high; chattier on a quiet evening. It picks the pool and nothing else, no fake emotions.
11. **Fresh quips every week (G→U, S, pattern A):** kills repetition, the main illusion-breaker.
12. **"ATLAS's week" (U, S, pattern C):** a self-review page plus suggestion cards.
13. **Suggestions as cards, spoken as a pointer (U, S):** level-4 initiative, always behind a ✓.
14. **Idle musings (G, T):** remarks when nothing's happening. Only at High.
15. **Constant due-today reminders (U, T; owner):** §2.3. A morning roll call, repeats at the level's cadence, sooner as the deadline nears, a last call in the evening. Exempt from the budget.
16. **Self-aware about itself (U/G, T; owner):** §2.5. It notices notes and tasks about ATLAS, Claude Code sessions in its repo, updates, settings changes and edits to its quote file.
17. **Teasing about its own to-dos (G→U, T; owner):** §2.6. Open ATLAS items, dated or not, at High and above.
18. **Varied info lines (U, T; owner):** §2.7. Its own wording per kind (done, permission, error), variant pools for every event, short forms on repeats, context in the line.

---

## 5. A recommended path (each step stands alone and can stop)

- **Step A: chattier, with templates only.** This is all that runs with Subscription use Off.
  - **First, idea 18 (varied info lines).** It fixes today's annoyance and is the base every new event's wording builds on: `voice_lines.py` gets pool-backed builders; `brain/_voice/lines.md` is parsed by the same machinery as `quotes.py`.
  - Then ideas 1 (template version), 2, 3, 4, 5, 6, 7, 9, 10, **15 (due-today reminders), 16 (self-aware about itself) and 17 (teasing about its own to-dos)**.
  - Briefings are spoken (owner).
  - The plumbing: the Chattiness level and its budget, desktop presence, the episodic memory, learned restraint.
  - New voice events `briefing`, `self`, `nudge`, `react` and `due_today` behind the existing gates, each with its own Settings switch.
  - `due_today` extends the 30 s reminder tick in `Announcer._tick` (`reminder_times`, `last_tick` in `voice.json`). The slots it has spoken are stored there too, so a restart never repeats one.
  - It extends `Announcer` (`atlas/voice.py`), `voice_lines.py` and `quotes.py` (new pools, seeded with `ensure_reply_pools`-style appends).
  - It costs no usage, and it tests the real question: *do you like a chattier ATLAS after two weeks?*
- **Step B: subscription-written wit.**
  - Pattern A (idea 11), then pattern B (the briefings, idea 1).
  - They reuse `claude_runner.py` the way filing does: a boxed-in run, an explicit tool list, no tools needed at all here, and `rate_limit_event` fed to the meter.
  - The honesty check is applied before any line is spoken.
- **Step C: the self-review and initiative.** Pattern C (ideas 12 and 13), reusing the proposal and ✓ machinery from `review.py`.

Each step gets the repo's usual treatment:
- a spec in `docs/superpowers/specs/`;
- a decisions file;
- a listening test of the new lines, like phase 4's;
- the work in a git worktree, since parallel sessions share the checkout.

## 6. Decisions

**Made (owner, 2026-10-26):**
- **No hearing, no API key, no local model.**
- **Chattiness levels:** Mute / Low / Medium / High, default Medium = 4 unprompted lines an hour, plus **Very high (debug)**: no waits, a "Try it now" panel, where each line came from, and it drops back by itself.
- **Patterns A, B and C** are each configurable.
- **A master Subscription use switch:** On keeps everything, Off keeps the template-only features.
- **Constant reminders** for things due today.

- **Persona:** the sarcastic friend.
- **Pattern B approved,** so V16 is amended narrowly (§3).
- **§2.4 approved** as written, including its numbers.
- **Briefings are spoken.**
- **Self-awareness about its project and its settings** (§2.5).
- **Playful nudges about open ATLAS items** at High and above (§2.6).
- **Variety in the info lines themselves** (§2.7), starting with a finished turn no longer sounding like "needs you".

**Still open:** nothing blocking. The cadence numbers in §2.6 are mine, and they're tuned at Very high during the build.

## Where the work starts

`origin/master` is `eeafe8e`, with this branch (#21) and widget-fixes (#20) merged. Local `master` is 12 commits behind, and nothing has been pulled in this session. The build starts from a fresh git worktree on `origin/master`, so the shared checkout is never touched.

## Verification (how each step would be proven)

- **Step A:**
  - unit tests on the budget, presence gate, restraint, memory and new events, in the style of the existing voice tests;
  - unit tests per Chattiness level (Mute speaks none of the new lines; each level's cap and cadence);
  - Very high (debug):
    - no cap and no cooldown;
    - the in-order pool walk;
    - each "Try it now" button fires its event;
    - pause still silences it;
    - it drops back after 2 h and on restart;
  - **every manual check of new lines runs at Very high on the scratch hub**, so nothing waits on a real hour;
  - self-awareness (§2.5):
    - a note or task in the ATLAS project gets the about-me line;
    - an Agents row in the ATLAS repo starting or finishing picks the self-aware pool;
    - a settings diff gives one line per burst, the biggest change;
    - "Sass off" is spoken plainly;
    - Mute gives a caption only;
  - varied info lines (§2.7):
    - `idle_prompt`, `permission_prompt` and `stop_failure` each draw from their own pool;
    - no variant repeats before its pool cycles;
    - a variant missing `{project}` or adding an unknown placeholder is rejected and the fixed line plays;
    - the same agent within 10 min gets the short form;
    - "that makes two waiting" is counted from real Agents rows;
    - a broken `lines.md` never silences an event;
  - teasing (§2.6):
    - silent below High;
    - oldest-first rotation, at most once a day per item at High;
    - stops on done or snooze;
    - never doubles a `due_today` line;
  - unit tests for `due_today`:
    - the cadence, the 2 h / 1 h / 15 min slots and the last call;
    - it stops on done or snooze;
    - quiet hours;
    - exempt from the cap;
    - no repeat after a restart;
  - Subscription use Off: no `claude -p` run is started for anything but filing and recall (a fake `claude` counts its calls);
  - a scratch hub with fake `claude`, never the real root;
  - a listening sample of the new lines;
  - a 1-week diary from the hub's log: lines spoken, lines held by the budget, shhs.
- **Step B:**
  - tests that the honesty check rejects a line with an unknown number or name, a stray placeholder, or a colon or dash;
  - a fake-`claude` test that a failed or late briefing falls back to the template;
  - one real batch run, with its measured usage cost.
- **Step C:** a fake-`claude` weekly page and cards on the scratch hub; one real run checked in headless Chrome, as with the earlier phases.
