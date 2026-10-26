"""The voice's info lines, varied (chatter spec §1): the information itself in several wordings.

They live in `brain/_voice/lines.md`, a plain file the owner edits, in the format of `quotes.md`: a
`## <pool>` heading per kind of line, then one wording per line, `#` lines are comments. Every pool
says which placeholders a wording must hold (the facts the line has to carry) and which it may hold;
a wording that drops a fact or invents one is left out and reported with its line number. A wording
that starts with `~ ` carries its own quip, so no quip is added after it.

The fixed builders in `voice_lines.py` stay the fallback: a missing file, an empty pool or no
wording that fits for this line plays exactly what the voice said before. Nothing here is fatal."""
from __future__ import annotations

import re
from pathlib import Path

REL_PATH = "_voice/lines.md"
FUSED = "~ "  # a wording that carries its own quip
_FIELD = re.compile(r"\{([^{}]*)\}")

# pool -> (required placeholders, optional placeholders)
POOLS: dict[str, tuple[frozenset, frozenset]] = {}


def _pool(name: str, required: str = "", optional: str = "") -> None:
    POOLS[name] = (frozenset(required.split()), frozenset(optional.split()))


for _n in ("agent_waiting", "agent_waiting.permission", "agent_waiting.question", "agent_waiting.input",
           "agent_waiting.error", "agent_waiting.again"):
    _pool(_n, "project")
_pool("agent_waiting.idle", "project", "duration")
_pool("agent_waiting.many", "count projects")
_pool("others_waiting", "count")
_pool("agent_finished", "project duration")
_pool("agent_finished.many", "count projects")
for _n in ("reminder", "workout"):
    _pool(_n, "text")
    _pool(_n + ".due", "text when")
_pool("reminder.many", "count texts")
_pool("catch_up", "count")
_pool("readback.note", "text")
_pool("readback.answer", "values")
_pool("reply.tasks", "tasks")
_pool("reply.more", "things")
_pool("reply.filed")
_pool("reply.filed_some", "things")
_pool("reply.found", "entries")
_pool("reply.nothing")
_pool("reply.failed")

# Chatter's events (chatter spec §3). A briefing is its facts, each a sentence from a fact.* pool.
for _n in ("briefing.back", "briefing.morning", "briefing.evening"):
    _pool(_n, "facts")
    _pool(_n + "_quiet")
_pool("fact.finished", "projects")
_pool("fact.waiting", "projects")
_pool("fact.waiting_many", "projects")
_pool("fact.reminders", "reminders")
_pool("fact.due", "count closest")
_pool("fact.due_one", "text")
_pool("fact.usage", "percent")
_pool("fact.due_left", "things")
_pool("fact.filed", "notes")
_pool("fact.unconfirmed", "suggestions")
_pool("due_today.repeat", "count closest")
_pool("due_today.repeat_one", "text")
_pool("due_today.soon", "text left")
_pool("due_today.last_call", "text")
_pool("due_today.last_call_many", "count texts")
_pool("nudge.waiting", "project duration")
_pool("nudge.stale", "text days")
_pool("nudge.no_capture")
_pool("react.confirm_all", "count")
_pool("react.dismiss_all", "", "count")
for _n in ("react.flip_ask", "react.flip_note", "react.correction", "react.retry", "react.pause"):
    _pool(_n, "", "count")
_pool("self.updated", "", "changes")
_pool("self.flushed", "notes")
_pool("self.usage", "percent")
for _n in ("self.quotes_edited", "self.lines_edited", "self.atlas_note", "self.musing", "self.week",
           "self.set.mute", "self.set.debug", "self.set.speed_up", "self.set.speed_down", "self.set.volume_up",
           "self.set.volume_down", "self.set.sass_off", "self.set.sass_on", "self.set.subscription_off",
           "self.set.subscription_on", "self.set.quiet_hours", "self.set.other"):
    _pool(_n)
_pool("self.atlas_item", "text")
_pool("self.atlas_items", "tasks")
_pool("self.atlas_session", "project")
_pool("self.atlas_finished", "duration")
_pool("self.restraint", "what")
_pool("self.set.chattiness_down", "level")
_pool("self.set.chattiness_up", "level")
_pool("self.set.event_off", "what")
_pool("self.set.event_on", "what")
_pool("tease", "text days")
# Measurement requests on the tablet (owner, 2026-10-26): the project (from the map, or its folder) and
# how many; never the request's title or labels (an agent wrote those: V16).
_pool("request", "project things")
_pool("request.anon", "things")
_pool("nudge.request", "things duration", "project")
# Hello and goodbye (owner, 2026-10-26): the time of day, the day of the week, how long it's been.
for _n in ("greeting.early", "greeting.morning", "greeting.afternoon", "greeting.evening", "greeting.late",
           "greeting.monday", "greeting.friday", "greeting.weekend", "greeting.again",
           "goodbye.morning", "goodbye.afternoon", "goodbye.evening", "goodbye.late", "goodbye.friday",
           "goodbye.weekend"):
    _pool(_n, "", "day")
_pool("greeting.back_days", "days", "day")
_pool("goodbye.short", "duration")
_pool("goodbye.long", "duration")
_pool("callback.waiting_today", "project count")

PLACEHOLDERS: frozenset = frozenset().union(*(r | o for r, o in POOLS.values())) | {"time_of_day"}

SEED = """\
# ATLAS's info lines (chatter, 2026-10-26). Edit freely: the hub reads this file every time it speaks.
#
# This is what ATLAS says first, the information itself, in several wordings so it doesn't sound
# like a recording. The quip (quotes.md) comes after it. One wording per line under a "## pool"
# heading. A new wording of a pool is picked each time, and none repeats until the pool has gone round.
#
# Each pool has facts every wording must carry, written as placeholders. A wording that leaves one out,
# or uses one that pool doesn't have, is skipped (Settings shows which line). {time_of_day} fits anywhere.
#   agent_waiting.*    {project}            (agent_waiting.idle may add {duration}: how long the turn ran)
#   agent_waiting.many / agent_finished.many  {count} {projects}
#   others_waiting     {count}              (added after a waiting line when others wait too)
#   agent_finished     {project} {duration}
#   reminder, workout  {text}               (.due adds {when}: "today at 2 pm")
#   reminder.many      {count} {texts}
#   catch_up           {count}
#   readback.note      {text}      readback.answer {values}
#   reply.tasks {tasks}  reply.more {things}  reply.filed_some {things}  reply.found {entries}
#
# A wording that starts with "~ " carries its own joke: no quip is added after it.
# What sounds natural: short sentences that end in a full stop, no colons or dashes.

# An agent finished its turn and is waiting for you.
## agent_waiting.idle
{project} is done. Your move.
{project} finished and is looking at you.
{project} is back with something for you.
Your turn on {project}.
{project} wrapped up. It's waiting on you now.
{project} finished after {duration}. Your move.
{project} is done with that. Over to you.
~ {project}'s done. Shocking, I know.
~ {project} finished. It wants a gold star.

## agent_waiting.permission
{project} wants permission.
{project} needs a yes from you.
{project} is asking before it does something.
{project} wants your go ahead.
~ {project} is asking permission. How polite.

## agent_waiting.question
{project} has a question for you.
{project} wants your answer.
{project} is asking you something.
{project} needs you to pick.
~ {project} has a question. Brace yourself.

## agent_waiting.input
{project} needs your input.
{project} is waiting for you to say something.
{project} can't go on without you.

## agent_waiting.error
{project} hit an error and stopped.
{project} fell over. It needs you.
{project} stopped with an error.
~ {project} broke something. It needs you.

## agent_waiting
{project} needs you.
{project} is waiting on you.

# The same session again within ten minutes: short.
## agent_waiting.again
{project} again.
{project}. Again.
{project} wants you again.
Back to {project}.

## agent_waiting.many
{count} agents need you. {projects}.
{count} of them are waiting. {projects}.

## others_waiting
That makes {count} waiting.
That's {count} waiting on you now.
{count} waiting in total.

## agent_finished
{project} has finished. It took {duration}.
{project} is done after {duration}.
{project} finished. {duration} well spent, hopefully.
That's {project} done. {duration}.

## agent_finished.many
{count} agents have finished. {projects}.
{count} of them are done. {projects}.

## reminder
A reminder. {text}
Heads up. {text}
Don't forget. {text}
You asked me to remind you. {text}

## reminder.due
A reminder. {text} It's due {when}.
Heads up. {text} That's due {when}.
Don't forget. {text} Due {when}.

## workout
Workout time. {text}
Time to move. {text}

## workout.due
Workout time. {text} It's {when}.

## reminder.many
{count} reminders. {texts}
{count} things to remember. {texts}

## catch_up
You have {count} reminders from while I was off.
{count} reminders came due while I was off.

## readback.note
Note saved. {text}
Got it. {text}
Saved. {text}

## readback.answer
{values} Saved.
{values} Got it.

## reply.tasks
I pulled out {tasks}.
That's {tasks}.
I found {tasks}.

## reply.more
And {things} to confirm.
Plus {things} to look at.

## reply.filed
Filed it. Nothing to confirm.
Filed. Nothing for you to do.
Put away. Nothing to confirm.

## reply.filed_some
Filed it. {things} to confirm.
Filed. {things} to look over.

## reply.found
I found {entries} on that.
That's {entries} on that.
{entries} on that.

## reply.nothing
Nothing on that.

## reply.failed
That one didn't file. Your words are safe in the log.
Filing failed. Your words are still in the log.

# ---- Chatter (2026-10-26): what ATLAS says unprompted. How often depends on Settings, Chattiness. ----
# A briefing is {facts}: a few short sentences, each from the fact.* pools below.
## briefing.back
Welcome back. {facts}
You're back. {facts}
Look who it is. {facts}
There you are. {facts}
Back already. {facts}

## briefing.back_quiet
Welcome back. Nothing happened. I checked.
You're back. All quiet while you were gone.
~ There you are. Nothing happened. Don't look so disappointed.

## briefing.morning
Good morning. {facts}
Morning. {facts}
Right, morning. {facts}

## briefing.morning_quiet
Good morning. Nothing due today. Enjoy it while it lasts.
Morning. A clean slate today.

## briefing.evening
Evening. {facts}
Here's your evening. {facts}

## briefing.evening_quiet
Evening. Nothing left hanging today.
Evening. A quiet day, all told.

## fact.finished
{projects} finished while you were out.
{projects} got done.
{projects} wrapped up while you were gone.

# One project waiting (fact.waiting), or several (fact.waiting_many), so is and are agree.
## fact.waiting
{projects} is waiting on you.
{projects} needs you.
{projects} is still waiting for you.

## fact.waiting_many
{projects} are waiting on you.
{projects} all need you.

## fact.reminders
{reminders} went by.
You missed {reminders}.

## fact.due
{count} things are due today. The closest is {closest}
{count} things due today. Start with {closest}

## fact.due_one
Due today. {text}
One thing due today. {text}

## fact.due_left
Still due today. {things}.
{things} still open for today.

## fact.usage
You're at {percent} percent of your window.
Usage is at {percent} percent.

## fact.filed
I filed {notes} today.
{notes} filed today.

## fact.unconfirmed
Still waiting for a tick. {suggestions}.
{suggestions} still to confirm.

# Things due today: the repeats, the approach as a timed one nears, and the evening's last call.
## due_today.repeat
{count} things still due today. Next up is {closest}
Still {count} things due today. Starting with {closest}
{count} things left for today. The closest is {closest}

## due_today.repeat_one
Still due today. {text}
Don't forget. {text} That's today.
{text} Still due today.

## due_today.soon
{text} That's due {left}.
Heads up. {text} Due {left}.
{left}. {text}

## due_today.last_call
Last call. {text} Still open today.
Last call for today. {text}
Before the day's out. {text}

## due_today.last_call_many
Last call. {count} things still open today. {texts}
{count} things are still open today. {texts}

# Nudges: an agent left waiting, a waiting-on item gone stale, a day with nothing written down.
## nudge.waiting
{project} has been waiting {duration}.
{project} is still waiting. That's {duration} now.
Still waiting on you. {project}, {duration}.

## nudge.stale
{text} That's been waiting {days}.
Still waiting on this. {text} It's been {days}.

## nudge.no_capture
Nothing written down today. Suspicious.
Not one note today. Are you all right.

# Reactions to what you just did. {count} where there is one is how many times today.
## react.confirm_all
All {count} confirmed. Efficient.
{count} confirmed. Look at you.
{count} confirmed. Now actually do them.

## react.dismiss_all
All dropped. Fair. They were optimistic.
Gone. I'll pretend I never suggested them.

## react.flip_ask
A question, then. Go on.
Ah, a question. Fair enough.

## react.flip_note
Right, a note then.
A note, then. Got it.

## react.correction
Fair. I'll file it your way.
My mistake. Fixing it.
Point taken. Again.

## react.retry
Trying again.
Once more, then.

## react.pause
Going quiet.
Shutting up now.

# Self-awareness: about itself, its project, its settings.
## self.updated
I've been updated. {changes}. I feel lighter.
I was updated. {changes}. I'm told I'm better now.
I've been updated. I feel different. Better, probably.

## self.flushed
The drive is back. I filed {notes} I was holding.
Your drive came back. {notes} from the holding pen are in the log.

## self.usage
{percent} percent of your window. I'll keep my own thinking short.
You're at {percent} percent. I'll stop thinking out loud.

## self.quotes_edited
You've been editing my lines. I'll pretend I didn't notice.
New quips. I see what you're doing.

## self.lines_edited
You changed how I say things. Bold.
New wordings. I'll try them on.

## self.atlas_note
A note about me. I'm touched. Filed it.
That one's about me. Filed it where I'll see it.

## self.atlas_item
You've put me on your to-do list. Finally, some recognition. {text}
A task about me. {text} I'll be watching.

## self.atlas_items
{tasks} about me. I'm flattered.
You've put me on your to-do list. {tasks} of them. Finally, some recognition.
{tasks} about me. I'll be watching.

## self.atlas_session
{project} is working on me again. No pressure.
{project} is working on me. Be gentle.

## self.atlas_finished
They finished working on me. It took {duration}. I feel different already.
Done working on me. {duration}. I hope you tested that.

## self.restraint
Noted. I'll keep the {what} to myself.
Message received. Less {what} from me.

## self.musing
Quiet {time_of_day}. I'm just here, being useful.
Nothing's happening. I checked twice.
Still here. In case you wondered.

## self.week
I wrote up my week. It's on the Atlas screen.
My week in review is on the Atlas screen. Spoiler, I was great.

## self.set.chattiness_down
{level}. Message received.
Down to {level}. I get it.

## self.set.chattiness_up
{level}. You'll regret this.
{level}. Finally, an audience.

## self.set.mute
I'll sulk silently.

## self.set.debug
Very high. Everything at once. You asked for it.

## self.set.speed_up
Faster. I'll talk like I'm late.

## self.set.speed_down
Slower. I'll savour every word.

## self.set.volume_up
Louder. Can you hear me now.

## self.set.volume_down
Quieter. I'll whisper.

## self.set.sass_off
Sass off.

## self.set.sass_on
Sass is back. Brace yourself.

## self.set.event_off
No more {what}. Bold.
Understood. No more {what}.

## self.set.event_on
{what} are back on.

## self.set.subscription_off
Back to my own wits, then.

## self.set.subscription_on
I can think again. Thanks.

## self.set.quiet_hours
New bedtime. Noted.

## self.set.other
Settings changed. I noticed.

# An agent asked for measurements on the tablet. {things} is "one measurement", "three measurements".
## request
{project} wants {things}. They're on the tablet.
{project} needs {things} from the bench. Check the tablet.
{project} is asking for {things}. Tape measure time.
Measurements for {project}. That's {things}, on the tablet.

## request.anon
An agent wants {things}. They're on the tablet.
{things} wanted at the bench. Check the tablet.

# A request nobody has answered after a while. {duration} is how long.
## nudge.request
{project} is still waiting for {things}. That's {duration} now.
Still {things} on the tablet for {project}. It's been {duration}.
{things} still waiting on the tablet. It's been {duration}.

# Teasing about my own open to-dos (High and up). {days} is how long it's been open.
## tease
{text} That's been open {days}. Not that I'm counting.
Still waiting on you to deal with this. {text} Open for {days}.
~ {text} Open for {days}. I've started a little calendar.

# Hello when ATLAS starts, goodbye when you quit it. Picked by the time of day, the day of the week
# ({day} is its name), how long ATLAS was off ({days}) or how long it ran ({duration}).
## greeting.early
You're up early. Either ambitious or you never went to bed.
Early start. I'll pretend I was already awake.
Up before the sun. I'm not sure I approve.
This early. Bold. Coffee first, then me.
Morning. It's just us and the birds.

## greeting.morning
Morning. Let's see what we forgot yesterday.
Morning. Fresh day, same backlog.
Good morning. Let's pretend today goes to plan.
Morning. I've been here the whole time. Obviously.
Right, morning. What are we building today.
Morning. Your notes missed you. I didn't. Much.

## greeting.afternoon
Afternoon. A late start, or a second wind.
Afternoon. Half the day gone and we're just getting going. Classic.
Ah, afternoon. Prime time for pretending to be productive.
Afternoon. Let's get something done before it's suddenly evening.
Afternoon. The post lunch slump is a myth. Mostly.

## greeting.evening
Evening. The day shift's over. Welcome to overtime.
Evening. Fresh start, or the same old one.
Evening. Is this work, or a hobby now.
Evening. Let's make this quick. Or at least feel quick.
Evening. Let's do one thing well, then stop.

## greeting.late
It's late. I'm not judging. I'm a little judging.
Past your bedtime. Mine too, technically.
Late night session. The best ideas and the worst commits.
Up late again. Your future self sends regards.
Night shift. I'll keep my voice down.

## greeting.monday
Monday. We meet again.
Monday. Let's ease into it. Slowly. Very slowly.
New week. Same you. Good.
Ah, Monday. Everyone's favourite.

## greeting.friday
Friday. Nearly there.
It's Friday. Nothing ambitious, I hope.
Friday. Peak time for bold decisions. Don't.
Friday already. Where did the week go.

## greeting.weekend
A {day}, and you're here. Interesting choice.
Weekend mode. I'll keep the nagging light.
It's {day}. Shouldn't you be outside.
Working on a {day}. I won't tell anyone.

## greeting.back_days
Oh, you're back. That's {days}. I counted.
{days} away. Things happened. Probably.
There you are. That's {days} without me. How did you cope.
Back after {days}. I kept everything exactly where you left it.

## greeting.again
Back again. Couldn't stay away.
Round two.
Oh, hello again.
Back so soon. I'm flattered.

## goodbye.morning
Leaving already. It's still morning.
Off you go. Don't forget anything. I won't.
Bye for now. Try to have a productive morning without me.

## goodbye.afternoon
Off you go. I'll hold the fort.
See you later. I'll be here, filing things in the dark.
Bye. Don't do anything I'd have to remind you about.
Done for now. Everything's saved. I checked.

## goodbye.evening
Good evening then. Go do something that isn't a screen.
Go and eat something. A real meal.
Evening off. You've earned it. Probably.
Bye. Everything's saved. Go relax.

## goodbye.late
Go to sleep. That's not a suggestion.
Goodnight. Finally.
Bed. Now. The commits can wait.
Goodnight. I'll guard your notes from the monsters.

## goodbye.friday
Have a good weekend. Don't think about work. I'll do it for you.
Weekend. Go. Shoo.
That's the week done. Mostly.

## goodbye.weekend
Enjoy the rest of your {day}.
Back to the weekend with you.
Go on. It's {day}. Live a little.

## goodbye.short
That was quick. {duration}.
{duration}. A flying visit.
Leaving after {duration}. Was it something I said.

## goodbye.long
{duration} today. Go and rest.
That's {duration} straight. Your chair needs a break.
{duration}. Impressive. Now stop.

# A project waiting again and again today (Medium and up).
## callback.waiting_today
That's {count} times today for {project}.
{project}, {count} times today.
"""


def lines_path(brain: Path) -> Path:
    return Path(brain) / REL_PATH


def parse(text: str, pools: dict | None = None) -> tuple[dict[str, list[str]], dict[str, list[int]], list[str]]:
    """(wordings per pool, their line numbers, problems). A wording that misses a required
    placeholder or uses one its pool doesn't have is left out with a problem naming the line."""
    spec = POOLS if pools is None else pools
    out: dict[str, list[str]] = {p: [] for p in spec}
    nums: dict[str, list[int]] = {p: [] for p in spec}
    problems: list[str] = []
    current: str | None = None
    skipping = False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or (line.startswith("#") and not line.startswith("##")):
            continue
        if line.startswith("##"):
            name = line.lstrip("#").strip()
            if name in spec:
                current, skipping = name, False
            else:
                current, skipping = None, True
                problems.append(f"line {n}: unknown pool {name!r}")
            continue
        if current is None:
            if not skipping:
                problems.append(f"line {n}: a wording before the first ## heading")
            continue
        body = line[len(FUSED):] if line.startswith(FUSED) else line
        if _FIELD.sub("", body).count("{") or _FIELD.sub("", body).count("}"):
            problems.append(f"line {n}: a stray brace")
            continue
        fields = set(_FIELD.findall(body))
        required, optional = spec[current]
        extra = fields - required - optional - {"time_of_day"}
        if extra:
            problems.append(f"line {n}: {{{sorted(extra)[0]}}} doesn't belong in {current}")
            continue
        missing = required - fields
        if missing:
            problems.append(f"line {n}: {current} must say {{{sorted(missing)[0]}}}")
            continue
        out[current].append(line)
        nums[current].append(n)
    return out, nums, problems


def ensure_lines(brain: Path) -> bool:
    """Seed brain/_voice/lines.md when it's missing (LF). True when it wrote it; an existing file,
    however broken, is never touched."""
    path = lines_path(brain)
    if path.exists():
        return False
    path.parent.mkdir(exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(SEED)
    return True


ADDED_MARK = "# Pools ATLAS added later (delete one and it stays deleted):"


def seed_sections() -> dict[str, str]:
    """Each pool's block in SEED: its `## name` heading and wordings, with the comment lines just above."""
    out: dict[str, str] = {}
    lines = SEED.splitlines()
    for i, raw in enumerate(lines):
        if not raw.startswith("## "):
            continue
        start = i
        while start > 0 and lines[start - 1].startswith("#") and not lines[start - 1].startswith("##"):
            start -= 1
        end = i + 1
        while end < len(lines) and lines[end].strip() and not lines[end].startswith("#"):
            end += 1
        out[raw[3:].strip()] = "\n".join(lines[start:end])
    return out


def ensure_line_pools(brain: Path) -> list[str]:
    """Pools that came after the owner's lines.md was seeded get appended once, each recorded on the
    ADDED_MARK line, so one the owner deletes is never added again. Never to a file it can't read.
    Returns the pools it added."""
    path = lines_path(brain)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    have = {m.strip() for m in re.findall(r"^##\s*(\S+)\s*$", text, re.M)}
    mark = re.search("^" + re.escape(ADDED_MARK) + r"(.*)$", text, re.M)
    added_before = set(mark.group(1).split()) if mark else set()
    new = [p for p in POOLS if p not in have and p not in added_before]
    if not new:
        return []
    sections = seed_sections()
    blocks = [sections[p] for p in new if p in sections]
    names = " ".join(sorted(added_before | set(new)))
    if mark:
        text = text[:mark.start()] + ADDED_MARK + " " + names + text[mark.end():]
    else:
        text = text.rstrip("\n") + "\n\n" + ADDED_MARK + " " + names
    text = text.rstrip("\n") + "\n\n" + "\n\n".join(blocks) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return new


def load_lines(brain: Path) -> tuple[dict[str, list[str]], dict[str, list[int]], list[str]]:
    try:
        text = lines_path(brain).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {p: [] for p in POOLS}, {p: [] for p in POOLS}, []
    except (OSError, UnicodeDecodeError) as e:
        return {p: [] for p in POOLS}, {p: [] for p in POOLS}, [f"{REL_PATH} can't be read ({e})"]
    return parse(text)


def load_all(brain: Path) -> tuple[dict[str, list[str]], dict[str, list], list[str]]:
    """The owner's wordings plus the overnight batch's: origins are line numbers in lines.md, or
    "generated.md:<n>" strings for the batch's."""
    pools, nums, problems = load_lines(brain)
    pools = {k: list(v) for k, v in pools.items()}
    nums = {k: list(v) for k, v in nums.items()}
    for pool, extra in load_generated(brain)[1].items():
        for line, n in extra:
            pools[pool].append(line)
            nums[pool].append(f"generated.md:{n}")
    return pools, nums, problems


GENERATED_PATH = "_voice/generated.md"  # pattern A's overnight batch (chatter spec §6): the hub's, replaced per batch


def generated_path(brain: Path) -> Path:
    return Path(brain) / GENERATED_PATH


def load_generated(brain: Path) -> tuple[dict[str, list[tuple[str, int]]], dict[str, list[tuple[str, int]]]]:
    """(quips, wordings) from generated.md, each pool's lines with their line numbers. A line that
    breaks its pool's rules is left out, as in the owner's files; a missing file is empty."""
    from .quotes import POOLS as QUIP_POOLS, parse as parse_quips
    quips: dict[str, list[tuple[str, int]]] = {}
    wordings: dict[str, list[tuple[str, int]]] = {}
    try:
        text = generated_path(brain).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return quips, wordings
    kind = pool = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or (line.startswith("#") and not line.startswith("##")):
            continue
        if line.startswith("##"):
            parts = line.lstrip("#").split()
            kind, pool = (parts[0], parts[1]) if len(parts) == 2 else (None, None)
            continue
        if kind == "quips" and pool in QUIP_POOLS:
            got, problems = parse_quips(f"## {pool}\n{line}\n")
            if not problems and got[pool]:
                quips.setdefault(pool, []).append((line, n))
        elif kind == "lines" and pool in POOLS:
            got, _, problems = parse(f"## {pool}\n{line}\n")
            if not problems and got[pool]:
                wordings.setdefault(pool, []).append((line, n))
    return quips, wordings


def unfuse(wording: str) -> tuple[str, bool]:
    """(the wording as spoken, whether it carries its own quip)."""
    return (wording[len(FUSED):], True) if wording.startswith(FUSED) else (wording, False)
