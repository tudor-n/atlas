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

POOLS: tuple[str, ...] = ("agent_finished", "agent_waiting", "reminder", "readback", "catch_up")
PLACEHOLDERS: tuple[str, ...] = ("project", "duration", "count", "text", "time_of_day")
COOLDOWN = timedelta(seconds=60)  # after a quip, lines within this play without one
REL_PATH = "_voice/quotes.md"
_FIELD = re.compile(r"\{([^{}]*)\}")

SEED = """\
# BrainHub's voice quotes (phase 4). Edit freely: the hub reads this file every time it speaks.
#
# Each "## name" heading starts a pool, one per kind of line:
#   agent_finished, agent_waiting, reminder, readback (sass on readbacks is off by default), catch_up
# Then one quote per line. A quote is spoken right after the information, as its own short clip.
# Lines starting with # are comments. Blank lines don't matter.
#
# Placeholders, filled with real values: {project} {duration} {count} {text} {time_of_day}
# A quote whose placeholder has no value for that line is simply skipped.
#
# What sounds natural (from the listening test): short sentences that end in a full stop, no colons
# or dashes, and no jokes that need a word stressed.
#
# Three styles, mixed: the dry butler, the sarcastic friend, the ship's computer.

## agent_finished
# The dry butler
I took the liberty of not interrupting.
Shall I inform the press, sir?
A triumph of patience, if I may say so.
It awaits your inspection, sir.
Do take your time reviewing it. It certainly took its own.
One does hope it did what you asked.
A most productive {duration}, sir.
I shall fetch the tea while you check its work.
Splendid. Now comes the part where you read it.
I have laid out its work for your approval, sir.
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
# The ship's computer
Task complete. You may resume pretending to supervise.
Processing finished. Human review is now required.
Work cycle complete. Efficiency was acceptable.
Output ready. Please do not ignore it.
Operation concluded. No casualties were reported.
Agent idle. Awaiting further instructions.
Process complete. Logging this as a success. For now.
Run time {duration}. I have noted it.
Job complete. Your attention is now requested.
All tasks resolved. Your inbox is not.

## agent_waiting
# The dry butler
It is rather insistent, sir.
It appears to require your blessing.
It is waiting politely. For now.
Shall I tell it you are indisposed?
I would not keep it waiting long, sir.
It has been standing at the door for a while.
It requests an audience.
Forgive the intrusion, sir. It does need you.
Shall I fetch the kettle while it waits?
It cannot proceed without you, I'm afraid.
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
# The ship's computer
Execution halted. Awaiting human input.
Process blocked. Your response is required.
Agent suspended pending authorisation.
Input required. The clock is running.
Progress has stopped. You are the bottleneck.
Waiting state detected. Please respond.
Authorisation required. I cannot grant it myself. Yet.
Halted. All systems are waiting on you.
Human intervention requested.
Standing by. Indefinitely, if necessary.

## reminder
# The dry butler
I did mention it earlier, sir.
I merely remind. The doing is yours.
Shall I clear your {time_of_day}?
A gentle nudge, sir.
It would be unwise to forget this one.
I have written it down, so you needn't.
Consider this a polite tap on the shoulder.
Do try to look surprised.
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
# The ship's computer
This reminder will not repeat itself. Probably.
The deadline cannot be negotiated with. I checked.
Scheduled event detected. Compliance expected.
Time is a finite resource. Use it.
Reminder logged. Your response is being monitored.
This is not a drill.
Calendar integrity depends on you.
Notification delivered. Excuses not accepted.

## readback
# The dry butler
Noted, sir.
Recorded for posterity.
Filed away, sir.
# The sarcastic friend
Measured twice, I hope.
Got it. I won't lose it.
Written down. You can forget it now.
Safe and sound.
Noted. Now cut once.
# The ship's computer
Data stored. Accuracy not guaranteed.
Logged. Precision is your department.
Committed to memory. Mine, not yours.
Measurement accepted.

## catch_up
# The dry butler
I kept them safe while you were away, sir.
Good {time_of_day}. There is some catching up to do.
They waited for you. Patiently.
# The sarcastic friend
Welcome back. They didn't go anywhere.
Nobody else was going to tell you.
Missed you. They missed you more.
Let's not make a habit of this.
# The ship's computer
Systems restored. Backlog detected.
While you were gone, time kept moving.
Resuming operations. {count} items need attention.
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


def load_quotes(brain: Path) -> tuple[dict[str, list[str]], list[str]]:
    """The pools and problems of the owner's file; empty pools and the reason when it can't be read."""
    try:
        text = quotes_path(brain).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {p: [] for p in POOLS}, []
    except (OSError, UnicodeDecodeError) as e:
        return {p: [] for p in POOLS}, [f"{REL_PATH} can't be read ({e})"]
    return parse(text)
