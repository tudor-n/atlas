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
There are {tasks} in there.
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
There are {entries} on that.
{entries} on that.

## reply.nothing
Nothing on that.

## reply.failed
That one didn't file. Your words are safe in the log.
Filing failed. Your words are still in the log.
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


def load_lines(brain: Path) -> tuple[dict[str, list[str]], dict[str, list[int]], list[str]]:
    try:
        text = lines_path(brain).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {p: [] for p in POOLS}, {p: [] for p in POOLS}, []
    except (OSError, UnicodeDecodeError) as e:
        return {p: [] for p in POOLS}, {p: [] for p in POOLS}, [f"{REL_PATH} can't be read ({e})"]
    return parse(text)


def unfuse(wording: str) -> tuple[str, bool]:
    """(the wording as spoken, whether it carries its own quip)."""
    return (wording[len(FUSED):], True) if wording.startswith(FUSED) else (wording, False)
