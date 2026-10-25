"""The voice's sass (phase 4 design §5): pre-written quips, no AI.

They live in one plain file the owner edits, `brain/_voice/quotes.md`: a `## <pool>` heading per
event, then one quote per line, `#` lines are comments. The hub seeds it once and never overwrites
it. Quips are picked in a shuffled cycle per pool, so nothing repeats until the pool has gone round;
the positions live in the caller's per-OS state. A broken file is never fatal: the info line still
plays, and the problems are reported with their line numbers."""
from __future__ import annotations

import hashlib
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

POOLS: tuple[str, ...] = ("agent_finished", "agent_waiting", "reminder", "workout", "readback", "catch_up",
                          "reply_tasks", "reply_filed", "reply_found", "reply_nothing")
PLACEHOLDERS: tuple[str, ...] = ("project", "duration", "count", "text", "time_of_day", "them")
COOLDOWN = timedelta(seconds=60)  # after a quip, lines within this play without one
REL_PATH = "_voice/quotes.md"
_FIELD = re.compile(r"\{([^{}]*)\}")

SEED = """\
# ATLAS's voice quotes (phase 4). Edit freely: the hub reads this file every time it speaks.
#
# Each "## name" heading starts a pool, one per kind of line:
#   agent_finished, agent_waiting, reminder, workout (reminders that look like a workout; when it's
#   empty they use reminder), readback (sass on readbacks is off by default), catch_up, and the
#   replies after you write or ask: reply_tasks, reply_filed, reply_found, reply_nothing
# Then one quote per line. A quote is spoken right after the information, as its own short clip.
# Lines starting with # are comments. Blank lines don't matter.
#
# Placeholders, filled with real values: {project} {duration} {count} {text} {time_of_day} {them}
# A quote whose placeholder has no value for that line is simply skipped.
#
# What sounds natural (from the listening test): short sentences that end in a full stop, no colons
# or dashes, and no jokes that need a word stressed.
#
# One style: the sarcastic friend (the owner retired the dry butler and the ship's computer, 2026-10-24).

## agent_finished
# The sarcastic friend
It worked harder than you did just now.
Go on then. See what it broke.
Done. Your move, boss.
Finally. I was starting to get bored.
That's {project} sorted. Probably.
Nice. Time to pretend you supervised.
Go have a look before you forget what you asked for.
Another one done. You're on a roll.
Right. Check it before you merge anything.
It's done. Don't leave it hanging now.
Done already. Show off.
That took {duration}. Worth it, I hope.
It's finished. Your turn to look busy.
Look at that. It actually finished.
Done. Read it before you trust it.
Wrapped up. You owe it a coffee.
{project} is done. Go take a look.
It finished. I'm as surprised as you.
All done. And now it's waiting on you.
Done and dusted. Allegedly.
Finished. Read it before you take the credit.
It's back. Hopefully with good news.
Ta da. Now check its homework.
That's done. Try not to break it.
Done. Go see what it got up to.
It finished in {duration}. Not bad.
Another job off your plate. You're welcome.
It's done. Go on, have a look.
Finished. It didn't even complain.
Job done. Time for you to do yours.

## agent_waiting
# The sarcastic friend
It's stuck. Go on, help it out.
It's asking nicely. Mostly.
It's just sitting there. Waiting for you.
Don't leave it hanging.
It needs a yes or a no. Pick one.
You've got a needy agent.
It's tapping its foot.
Quick one. It can't do anything without you.
Your agent misses you.
It's frozen until you answer. No pressure.
It's waiting on you. Again.
Your agent has a question. Brace yourself.
It's stuck on {project}. Go on.
It wants your permission. Be nice.
It's waiting. Don't ghost it.
One click and it's off again.
It's paused. Because of you.
It needs you. Don't let it down.
It's twiddling its thumbs over there.
Go on. It won't approve itself.
You're the blocker now. Just saying.
It asked a question. Go answer it.
It's been waiting a while. Just so you know.
Hey. Your agent's stuck.
It wants your blessing. As usual.
Can't do it without you, apparently.
It's standing still until you say something.
Quick, before it gets lonely.
Your input is needed. Shocking, I know.
It's on hold. Guess who it's waiting for.

## reminder
# The sarcastic friend
Don't say I didn't tell you.
Yes, this one is real.
Future you will thank you.
You knew this was coming.
Go on. Get it done.
Ignore this at your peril.
Past you set this. Blame them.
No snoozing this time.
Better now than at midnight.
Here's your nudge.
Don't make me say it twice.
You asked me to remind you. So here I am.
Right on time. Unlike you.
Just doing my job. Now do yours.
Heads up. This one's now.
You'll forget otherwise. We both know it.
Tick tock.
I'm only the messenger.
Go on, before you talk yourself out of it.
It's {time_of_day}. Perfect time for this.
This won't do itself.
You put this here for a reason.
Remember this one? Yeah.
Do it now and forget about it.
No pressure. Well, a little.

# The workout pool (added 2026-10-24): quips for reminders that look like a workout (Settings →
# Voice → Workout words). Empty or gone, those reminders use the reminder pool.
## workout
# The sarcastic friend
Get up. The couch will still be there later.
No excuses today. I've heard them all.
You'll feel great after. Probably.
Shoes on. Water bottle. Go.
Skipping it won't make you any fitter.
It's {text}. You said you would. So go.
Earphones in. Playlist on. Let's move.
Nobody ever regretted the workout. Only skipping it.
Ten minutes in and you'll stop complaining.
Go on. Earn that dinner.
Up you get. No negotiating.
Your trainers miss you.
Sweat now. Brag later.
The sofa will survive without you.
You'll thank me in the shower.
Stretch first. I'm not calling an ambulance.
Leave the phone. Take the water.
Go. You'll be done before you know it.
Today's excuse is not accepted.
It's {time_of_day}. Great time to suffer a bit.
You've done harder things. Probably.
The playlist is ready even if you're not.
Move. Your back will thank you.
Half an hour. You can do half an hour.
Lace up. Let's go.
Your muscles called. They're bored.
Get it done and the rest of the day is yours.
Don't think. Just start.
You're not tired. You're comfy. Different thing.
Future you is already smug about this.

## readback
# The sarcastic friend
Measured twice, I hope.
Got it. I won't lose it.
Written down. You can forget it now.
Safe and sound.
Noted. Now cut once.
Got it.
Saved. You're welcome.
Noted. I'll pretend I understand it.
In the books.
Written down. Don't argue with it later.
Done. Hope the tape measure was straight.
Locked in.

## catch_up
# The sarcastic friend
Welcome back. They didn't go anywhere.
Nobody else was going to tell you.
Missed you. They missed you more.
Let's not make a habit of this.
You've been busy. So have they.
Good {time_of_day}. You missed a few.
Oh, you're back. Here's what you missed.
{count} of them. Just saying.
Don't panic. It's only a few.
Took your time, didn't you.

# Replies (added 2026-10-25): what ATLAS adds after you write something down or ask it something.
# reply_tasks follows "I pulled out two possible tasks.", reply_filed "Filed it. Nothing to confirm."
# (or "… three things to confirm."), reply_found "I found three entries on that.", and reply_nothing
# "Nothing on that." {count} is the number in words; {them} is "it" for one task and "them" for more.
## reply_tasks
# The sarcastic friend
Look {them} over and pretend you're not gonna procrastinate {them}.
Confirm {them}. Putting {them} off is a separate step.
Have a look. Tasks don't do themselves.
Go on, confirm {them}. I'll act surprised.
There you go. Something fresh to avoid.
Look {them} over before someday happens.
You said it, not me. Go confirm.
Check {them}. Then actually start.
More homework. You're welcome.
That's {count}. No pressure.
Look {them} over. The procrastinating can wait.
Have a look. Future you is watching.

## reply_filed
# The sarcastic friend
Tidy. I like it.
Put away where it belongs. Unlike your socks.
Another one for the archive.
Filed. I'm basically a librarian now.
Safe with me. Unlike your keys.
Done. That was the easy part.
Shelved. You can stop thinking about it.
Neat and tidy. Don't get used to it.
Look at us being organised.
In the brain. The one that doesn't forget.

## reply_found
# The sarcastic friend
Look over it, will you?
There you go. Your own words, no less.
You wrote it. I just remembered it.
Have a look. I'll wait.
It was right where you left it.
Your memory, outsourced. Have a read.
Read it before you argue with yourself.
See? You did write it down.
Have a look. Past you was quite chatty.
Go on, read it. I didn't make it up.

## reply_nothing
# The sarcastic friend
Either you never wrote it down, or you're inventing memories.
Not a word. Did you dream it?
Maybe write it down next time.
I looked. Honestly, I did.
Blank. Like your notes on the subject.
Nope. Not a thing.
If you said it, you said it out loud.
Try other words. Or write it down this time.
Your past self let you down.
I've got nothing. And I've got everything you wrote.
"""


def parse(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """(pools, problems). Problems name the line: a quote outside a pool, an unknown pool or
    placeholder, a stray brace. Those lines are left out; the rest of the file still counts."""
    pools: dict[str, list[str]] = {p: [] for p in POOLS}
    problems: list[str] = []
    current: str | None = None
    skipping = False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or (line.startswith("#") and not line.startswith("##")):
            continue
        if line.startswith("##"):
            name = line.lstrip("#").strip()
            if name in pools:
                current, skipping = name, False
            else:
                current, skipping = None, True
                problems.append(f"line {n}: unknown pool {name!r} (known: {', '.join(POOLS)})")
            continue
        if current is None:
            if not skipping:
                problems.append(f"line {n}: a quote before the first ## heading")
            continue
        fields = _FIELD.findall(line)
        bad = [f for f in fields if f not in PLACEHOLDERS]
        if bad:
            problems.append(f"line {n}: unknown placeholder {{{bad[0]}}}")
            continue
        if _FIELD.sub("", line).count("{") or _FIELD.sub("", line).count("}"):
            problems.append(f"line {n}: a stray brace")
            continue
        pools[current].append(line)
    return pools, problems


def time_of_day(now: datetime) -> str:
    h = now.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    return "evening" if 17 <= h < 22 else "night"


class QuotePicker:
    """Picks a quip per pool in a shuffled cycle. `state` is a plain dict the caller persists
    (`voice.json` in the state folder): {"pools": {pool: {"key", "order", "pos"}}, "last_quip": iso}.
    A pool whose quotes changed is reshuffled. Not thread-safe: the caller holds its lock."""

    def __init__(self, state: dict, rng: random.Random | None = None):
        self.state = state
        self._rng = rng or random.Random()

    def pick(self, pool: str, quotes: list[str], values: dict, now: datetime) -> str | None:
        last = self.state.get("last_quip")
        try:
            if last and now - datetime.fromisoformat(last) < COOLDOWN:
                return None
        except (TypeError, ValueError):
            pass
        if not quotes:
            return None
        values = {"time_of_day": time_of_day(now), **{k: v for k, v in values.items() if v is not None}}
        key = hashlib.sha256("\n".join(quotes).encode()).hexdigest()[:16]
        st = self.state.setdefault("pools", {}).get(pool)
        if not isinstance(st, dict) or st.get("key") != key or not self._valid(st, len(quotes)):
            st = {"key": key, "order": self._shuffled(len(quotes), None, quotes), "pos": 0}
        for _ in range(len(quotes)):
            if st["pos"] >= len(st["order"]):
                prev = quotes[st["order"][-1]] if st["order"] else None
                st["order"], st["pos"] = self._shuffled(len(quotes), prev, quotes), 0
            quote = quotes[st["order"][st["pos"]]]
            st["pos"] += 1
            filled = _fill(quote, values)
            if filled is not None:
                self.state["pools"][pool] = st
                self.state["last_quip"] = now.isoformat(timespec="seconds")
                return filled
        self.state["pools"][pool] = st
        return None

    @staticmethod
    def _valid(st: dict, n: int) -> bool:
        order, pos = st.get("order"), st.get("pos")
        return (isinstance(order, list) and sorted(order) == list(range(n)) and isinstance(pos, int)
                and 0 <= pos <= n)

    def _shuffled(self, n: int, prev: str | None, quotes: list[str]) -> list[int]:
        order = list(range(n))
        self._rng.shuffle(order)
        # a new cycle never starts with the quote the last one ended on
        if prev is not None and n > 1 and quotes[order[0]] == prev:
            order[0], order[-1] = order[-1], order[0]
        return order


def _fill(quote: str, values: dict) -> str | None:
    """The quote with its placeholders filled, or None when one has no value."""
    missing = [f for f in _FIELD.findall(quote) if not str(values.get(f, "")).strip()]
    if missing:
        return None
    return _FIELD.sub(lambda m: str(values[m.group(1)]), quote)


def quotes_path(brain: Path) -> Path:
    return Path(brain) / REL_PATH


def ensure_quotes(brain: Path) -> bool:
    """Seed brain/_voice/quotes.md when it's missing (LF, shared space). True when it wrote it.
    An existing file, however broken, is never touched."""
    path = quotes_path(brain)
    if path.exists():
        return False
    path.parent.mkdir(exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(SEED)
    return True


# The workout section as the seed has it, for a file seeded before it existed (`ensure_workout_pool`).
WORKOUT_MARKER = "# The workout pool"
WORKOUT_SECTION = SEED[SEED.index(WORKOUT_MARKER):SEED.index("## readback\n")]


def ensure_workout_pool(brain: Path) -> bool:
    """Add the workout section once to a quotes file seeded before it existed (2026-10-24). Never
    when the file has a `## workout` heading or the section's marker comment (the owner deleted the
    quips but kept the comment: that stands), never to a file it can't read. True when it wrote."""
    path = quotes_path(brain)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if WORKOUT_MARKER in text or re.search(r"^##\s*workout\s*$", text, re.M):
        return False
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(("" if text.endswith("\n") else "\n") + "\n" + WORKOUT_SECTION.rstrip("\n") + "\n")
    return True


# The replies' section as the seed has it, for a file seeded before it existed (`ensure_reply_pools`).
REPLIES_MARKER = "# Replies (added 2026-10-25)"
REPLIES_SECTION = SEED[SEED.index(REPLIES_MARKER):]
REPLY_POOLS: tuple[str, ...] = ("reply_tasks", "reply_filed", "reply_found", "reply_nothing")


def ensure_reply_pools(brain: Path) -> bool:
    """Append the replies' section once to a quotes file seeded before it (the owner asked for these
    lines, 2026-10-25: V-S2's say-so). Never when the file already has any `## reply_…` heading or the
    section's marker comment, never to a file it can't read. True when it wrote."""
    path = quotes_path(brain)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if REPLIES_MARKER in text or re.search(r"^##\s*reply_\w+\s*$", text, re.M):
        return False
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(("" if text.endswith("\n") else "\n") + "\n" + REPLIES_SECTION.rstrip("\n") + "\n")
    return True


def load_quotes(brain: Path) -> tuple[dict[str, list[str]], list[str]]:
    """The pools and problems of the owner's file; empty pools and the reason when it can't be read."""
    try:
        text = quotes_path(brain).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {p: [] for p in POOLS}, []
    except (OSError, UnicodeDecodeError) as e:
        return {p: [] for p in POOLS}, [f"{REL_PATH} can't be read ({e})"]
    return parse(text)
