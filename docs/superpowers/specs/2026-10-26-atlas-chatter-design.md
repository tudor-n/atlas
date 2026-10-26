# ATLAS chatter: self-awareness, varied lines, chattiness levels

Approved by the owner on 2026-10-26. The brainstorm and its tradeoffs are in the session plan; this is the build's design. Decisions made while building are in `../chatter-decisions.md` (CH1…).

It amends the phase 4 design (`2026-10-23-atlas-phase4-design.md`, §4–§5, §7) and V16.

## 0. Hard limits (owner)
- **No hearing:** no mic, VAD, wake word or push-to-talk.
- **No API key and no local model.** Claude runs only via `claude -p` on the subscription, and only where its slowness doesn't show: in batches and in the background.
- **One persona: the sarcastic friend.** Every new line, seeded or generated, is written in it.
- **The phase 4 rules hold:**
  - info first, quip after;
  - phase 4's wording rules (short sentences that end in a full stop, no colons or dashes, numbers in words);
  - the gates (on, installed, event ticked, quiet hours, pause, dedupe);
  - agent text is never spoken, except for the one narrow exception in §7.

## 1. Varied info lines (`atlas/lines.py`, `brain/_voice/lines.md`)
The info line itself varies, not only the quip.
- **A new owner-editable file** `brain/_voice/lines.md`, in the same format as `quotes.md`: `## pool` headings and one variant per line. It is seeded once, never overwritten, and committed by the hub ("add the voice's info lines"). `_voice/` is already a protected path.
- **Each pool declares its placeholders:** *required* ones every variant must hold, and *optional* ones it may hold. A variant missing a required one, or using one outside the set, is rejected at load with a line-numbered problem (shown in Settings like the quote problems). The picker skips a variant whose optional placeholder has no value for this line.
- **A variant starting with `~ ` carries its own quip:** "~ {project}'s done. Shocking, I know." No separate quip is added after it.
- **The picker:** the same shuffle-and-cycle picker as quips (`QuotePicker`), in its own state (`voice.json` → `lines`), with no cooldown. At the Very high level it walks pools in file order.
- **The fallback:** the fixed builders in `voice_lines.py` stay as they are. They're used when `lines.md` is missing, the pool is empty, or no variant fits. So a broken file never silences an event.
- **The pools (required → optional):**

  | Pool | Placeholders |
  |---|---|
  | `agent_waiting.permission`, `.question` (elicitation_dialog), `.input` (agent_needs_input), `.error` (stop_failure) | project → – |
  | `agent_waiting.idle` | project → duration |
  | `agent_waiting.again` (the same session within 10 min) | project → – |
  | `agent_waiting.many` | count, projects → – |
  | `others_waiting` (appended when others wait too: "That makes two waiting.") | count → – |
  | `agent_finished` | project, duration → – |
  | `agent_finished.many` | count, projects → – |
  | `reminder` | text → – |
  | `reminder.due` | text, when → – |
  | `workout`, `workout.due` | as `reminder` |
  | `reminder.many` | count, texts → – |
  | `catch_up` | count → – |
  | `readback.note` | text → – |
  | `readback.answer` | values → – |
  | `reply.tasks` | tasks → – |
  | `reply.more` | things → – |
  | `reply.filed` | – → – |
  | `reply.filed_some` | things → – |
  | `reply.found` | entries → – |
  | `reply.nothing`, `reply.failed` | – → – |

  Plus the new events' pools (§3).

  Values are spoken forms: a project by `spoken_project`, durations and counts in words, `{text}` the owner's words capped, `{tasks}` = "two possible tasks".
- **Context shapes the line:**
  - **the same session again within 10 min** → `.again`;
  - **others waiting** → `others_waiting` appended, counted from the Agents rows;
  - **an idle prompt** carries how long the turn ran;
  - **a merge** uses `.many`.

## 2. Settings (shared `settings.json`, validated, VI5-style loading)
| Key | Default | What it does |
|---|---|---|
| `voice_chattiness` | `"medium"` | `mute` `low` `medium` `high` `debug` (§4) |
| `voice_chatter_per_hour` | `null` | overrides the level's cap (0–60) |
| `voice_subscription` | `true` | the master switch for §6 (On keeps everything, Off keeps template-only features) |
| `voice_sub_quips`, `voice_sub_briefings`, `voice_sub_review` | `true` | patterns A, B, C |
| `voice_sub_max_pct` | 80 | §6 runs are skipped above this % of the 5-hour window |
| `voice_last_call` | `"18:00"` | the evening digest and the due-today last call |
| `voice_atlas_project` | `""` | ATLAS's own project slug; empty = detect it (§3.5) |

`voice_events` gains `briefing`, `self`, `nudge`, `react`, `due_today` and `tease`, all on by default.

## 3. New events
Each one runs through the gates and has a seeded pool for its info lines and one for its quips.

| Event | Budgeted (§4) | Trigger |
|---|---|---|
| `briefing` | yes | coming back after being away (§5); the morning digest (first presence after the end of quiet hours); the evening digest at `voice_last_call` |
| `self` | yes | an update pulled since the last start; held captures flushed at start; usage crossing `usage_warn_pct`; settings changed; `quotes.md`/`lines.md` edited; ATLAS-project events (§3.5); learned restraint announced |
| `nudge` | yes | an agent still waiting after 10 min (once per stretch); a waiting-on item open ≥ `nudge_days` (once a day); nothing captured all day (at `voice_last_call`, Medium and up) |
| `react` | no (≤ 1 a minute) | confirm all, dismiss all, NOTE↔ASK flip, a correction (TELL IT WHAT'S WRONG), retry, pause |
| `due_today` | no (its own cadence) | §3.4 |
| `tease` | yes | §3.6, at High and up |
| musings (`self.musing`) | yes | High and up: present, nothing said for 30 min (Very high: every few minutes) |

### 3.4 Due today
The items it covers are open, confirmed items whose `due` is today (date or date-time).
- **The morning roll call:** in the morning digest.
- **Repeats** at the level's cadence while any are left. It names the count and the closest item: "Two things still due today. The networks report is the closest."
- **Approach slots** for a timed item: 2 h, 1 h and 15 min before.
- **The last call** for date-only items, at `voice_last_call`.
- **Dedupe** per item per slot, kept in `voice.json`, so a restart never repeats one.
- **What stops it:** done, dismissed or snoozed. It's never doubled with the existing `reminder` line in the same tick.

### 3.5 ATLAS's own project
ATLAS's project is `voice_atlas_project`. When that's empty, it's the map project whose folders include the running code's repo, or else the project with slug `atlas`. Self lines for:
- a note filed into it;
- a confirmed item in it;
- an agent session in it starting a turn (once per session per hour) or finishing;
- settings changes (one line per burst, 5 s after the last save, about the biggest change; "Sass off." spoken plainly; Mute gets the caption only).

### 3.6 Teasing about ATLAS's to-dos
It covers open, confirmed items in ATLAS's project, of any kind, except the ones due today.
- **High:** one every 3 h of presence, at most 3 a day, oldest first, each item at most once a day.
- **Very high:** every few minutes.
- **What's read:** the item's text (the owner's words) and how many days it's been open.

## 4. Chattiness levels
| | mute | low | medium | high | debug |
|---|---|---|---|---|---|
| budgeted lines/hour | 0 | 2 | 4 | 8 | ∞ |
| due-today repeat | off | 3 h | 2 h | 1 h | 2 min |
| reactions | off | corrections and flips only | all | all | all |
| callbacks, mood | off | off | on | on | on |
| musings | off | off | off | on | every 3 min |
| quips on new events | off | 1 in 3 | cooldown | cooldown | every line, no cooldown |

**Mute** silences all new events except `due_today`'s single existing reminder. The phase 4 events keep their own switches.

**Over budget,** a line is captioned, not spoken.

**Learned restraint:**
- A pause, a voice-off or a chattiness drop within 30 s of a budgeted line counts as a shh against that line's event.
- 3 shhs in 7 days halve that event's share: it is spoken only every other time it qualifies, and captioned otherwise.
- It's announced once, and it resets when the level changes.

**Debug (Very high):**
- **No waiting anywhere:** every wait collapses (away after 1 min, digests on the next tick, approach slots 20 s apart, no presence gate, no quiet hours, no cooldown, pools in file order).
- **A "Try it now" panel** fires each event from real state, and patterns A, B and C at once.
- **The caption names the pool and line** (or "fixed" / "generated").
- **It drops back by itself:** to the previous level after 2 h, or when the hub starts.

## 5. Presence (`atlas/presence.py`)
No mic and no idle API on KDE Wayland (`GetSessionIdleTime` isn't supported there), so presence comes from:
- **the lock state:** logind `LockedHint` or `org.freedesktop.ScreenSaver.GetActive` on Linux;
- **the owner's own activity seen by the hub:**
  - a `UserPromptSubmit` hook;
  - an agent going waiting → running (the owner answered);
  - PC write requests;
  - a throttled `POST /api/presence` ping from the app and the widget on keyboard or pointer input;
- **on Windows,** `GetLastInputInfo` as the real idle time.

The states:
- **away:** locked, or no activity for 20 min (1 min at debug);
- **present:** activity within the last 10 min;
- **in between:** idle.

Budgeted lines speak only while present, except at debug. The first activity after away fires the welcome-back briefing.

## 6. The subscription, where its slowness doesn't show (`atlas/chatter_sub.py`)
All of it is behind `voice_subscription` and its own switch. It's skipped above `voice_sub_max_pct`, except when pressed in Try it now. Every run uses the boxed-in `ClaudeRunner` with no tools, and feeds the usage meter.
- **A. Quip batch:** once a day, while the owner is away or at the start of quiet hours.
  - The prompt gets structured facts only: project names from the map, counts, corrections; never agent text.
  - The output is JSON pools, written to `brain/_voice/generated.md`, a hub-owned file replaced per batch and committed. Its pools are merged with `quotes.md` (quips) and `lines.md` (info variants).
  - Every line is validated against its pool's placeholders and the wording rules.
- **B. Background briefings:**
  - When the owner goes away, and again when the facts change while they're away (at most 6 runs a day), `claude -p` words the welcome-back briefing from a fact sheet.
  - On return, it's spoken only if its fact sheet still equals the current one and it passes the honesty check (§7). Otherwise the template plays.
- **C. ATLAS's week:** once a week (or from Try it now), a short self-review written from stats: filings, corrections, intent flips, muted events, its runs.
  - It's shown as a section on the ATLAS screen, with up to two suggestions, each a ✓ card. Confirming one creates an open task in ATLAS's project; dismissing drops it.
  - One spoken line points at it.

## 7. Honesty (V16 amended, owner)
A model-worded briefing (B) may be spoken only if:
- every number and every name in it appears in its fact sheet;
- it holds no `{` or `}`;
- no colon, dash or semicolon;
- at most 3 sentences and 300 characters.

Its fact sheet never holds agent text. Generated pool lines (A) are templates; their placeholders are filled from real values as usual.

## 8. Captions and debug
- **A new broadcast** `voice.line` {event, text, origin, spoken, why} for every composed line, quiet on the tablet.
- **The ATLAS screen's caption** shows the latest, spoken or not.
- **Settings → Voice** gets:
  - Chattiness;
  - the subscription switches;
  - the ATLAS-project field;
  - the last-call time;
  - "Edit info lines";
  - and at debug, Try it now plus the last 20 lines with their origin.

## 9. Testing
- **Unit tests:** per module (lines, presence, chatter levels and budget, due today, tease, self, sub patterns with a fake runner).
- **API tests:** for the new endpoints.
- **Node tests:** for the pure UI helpers.
- **Manual checks:** on a scratch hub at Very high with fake `claude`, never the real root (rule 9).
