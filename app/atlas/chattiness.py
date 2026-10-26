"""How talkative ATLAS is unprompted (chatter spec §4): Mute, Low, Medium, High and Very high (debug).

One table, read by the Announcer (the budget, the quip policy, quiet hours at debug) and by Chatter
(what fires, and how often). `voice_chatter_per_hour` overrides the level's cap."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta


@dataclass(frozen=True)
class Level:
    name: str
    per_hour: int | None  # budgeted lines an hour; None = no cap
    due_every: timedelta | None  # the due-today repeat; None = no repeats
    reactions: str  # off | mistakes | all
    callbacks: bool  # callbacks and mood
    musing_every: timedelta | None
    tease_every: timedelta | None
    tease_max: int | None  # teases a day; None = no cap
    quip_every: int  # 0 never, 1 every line, 3 one line in three
    cooldown: bool  # the one-minute quiet after a quip
    away_after: timedelta  # how long without activity counts as away
    slot_gap: timedelta  # due-today approach slots: at their time, or one after another at debug
    debug: bool = False


H = timedelta(hours=1)
TABLE: dict[str, Level] = {
    "mute": Level("mute", 0, None, "off", False, None, None, 0, 0, True, timedelta(minutes=20), timedelta(0)),
    "low": Level("low", 2, 3 * H, "mistakes", False, None, None, 0, 3, True, timedelta(minutes=20), timedelta(0)),
    "medium": Level("medium", 4, 2 * H, "all", True, None, None, 0, 1, True, timedelta(minutes=20), timedelta(0)),
    "high": Level("high", 8, H, "all", True, 2 * H, 3 * H, 3, 1, True, timedelta(minutes=20), timedelta(0)),
    "debug": Level("debug", None, timedelta(minutes=2), "all", True, timedelta(minutes=3), timedelta(minutes=4), None,
                   1, False, timedelta(minutes=1), timedelta(seconds=20), debug=True),
}
SPOKEN = {"mute": "Mute", "low": "Low", "medium": "Medium", "high": "High", "debug": "Very high"}
ORDER = ("mute", "low", "medium", "high", "debug")
DEBUG_FOR = timedelta(hours=2)  # Very high drops back by itself after this


def level(settings: dict) -> Level:
    lvl = TABLE.get(settings.get("voice_chattiness") or "medium", TABLE["medium"])
    cap = settings.get("voice_chatter_per_hour")
    if isinstance(cap, int) and not isinstance(cap, bool) and lvl.name not in ("mute", "debug"):
        lvl = replace(lvl, per_hour=cap)
    return lvl


def rank(name: str) -> int:
    return ORDER.index(name) if name in ORDER else ORDER.index("medium")
