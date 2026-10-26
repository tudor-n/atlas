"""Chatter (chatter spec §3): what ATLAS says unprompted, and when.

The Announcer (voice.py) owns the gates, the wordings, the quips, the budget and the speaker; this
decides what's worth saying. Every fact comes from code: the items, the Agents rows, the usage
meter, the store's entries, presence and the hub's own state. No agent text is ever spoken (V16).

It runs on the voice's 30 s tick, on the owner's presence (`welcome_back`), on agent transitions,
on settings saves and on what the owner does in the app (reactions). Its per-OS memory lives in
`voice.json` under "chatter", written under the Announcer's lock. Nothing here may fail the hub."""
from __future__ import annotations

import hashlib
import logging
import subprocess
import threading
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import chattiness as ch
from . import voice_lines as vl
from .lines import lines_path
from .quotes import quotes_path
from .voice import in_quiet_hours, morning_time, reminder_times

log = logging.getLogger(__name__)

NUDGE_WAIT = timedelta(minutes=10)  # an agent waiting this long gets one nudge per stretch
MORNING_FOR = timedelta(hours=4)  # the morning digest plays within this of the end of quiet hours, or not that day
QUIET_FOR_MUSING = timedelta(minutes=30)  # nothing said for this long before a musing (not at debug)
REACT_GAP = timedelta(minutes=1)
SETTLE_S = 5.0  # a burst of settings saves becomes one line, this long after the last
APPROACH = (timedelta(hours=2), timedelta(hours=1), timedelta(minutes=15))
SLOT_WORDS = {APPROACH[0]: "in two hours", APPROACH[1]: "in an hour", APPROACH[2]: "in fifteen minutes"}
SESSION_LINE_GAP = timedelta(hours=1)  # an ATLAS session starting a turn: one line per session an hour
EVENT_WORDS = {"agent_waiting": "agents waiting", "agent_finished": "agents finishing", "reminder": "reminders",
               "readback": "readbacks", "reply": "replies", "briefing": "briefings", "self": "remarks about me",
               "nudge": "nudges", "react": "reactions", "due_today": "due today reminders", "tease": "teasing"}


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _parse(s) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if isinstance(s, str) and s else None
    except ValueError:
        return None


def _due_on(item, day: date) -> datetime | None:
    """When an open item is due on `day`: its time, or midnight for a date-only due; else None."""
    due = getattr(item, "due", None)
    if not isinstance(due, str) or not due:
        return None
    try:
        when = datetime.fromisoformat(due)
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone().replace(tzinfo=None)
    return when if when.date() == day else None


def _locked(fn):
    """Run under the Announcer's lock (voice.json is one dict, saved from several threads)."""
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


def _days(n: int) -> str:
    return "a day" if n == 1 else f"{vl.number_words(n)} days"


class Chatter:
    def __init__(self, *, voice, settings: Callable[[], dict], brain: Path, items: Callable[[], list],
                 agents: Callable[[], list[dict]], entries_today: Callable[[str], int],
                 unconfirmed: Callable[[], int] = lambda: 0, usage: Callable[[], dict | None] = lambda: None,
                 presence=None, now: Callable[[], datetime] = datetime.now,
                 save_settings: Callable[[dict], dict] | None = None, roots: tuple[Path, ...] = (),
                 load_map: Callable[[], object] = lambda: None, commit: Callable[[], str | None] = lambda: None,
                 count_commits: Callable[[str, str], int | None] = lambda a, b: None, settle_s: float = SETTLE_S):
        self.v, self._settings, self._brain, self._items, self._agents = voice, settings, Path(brain), items, agents
        self._entries_today, self._unconfirmed, self._usage, self.presence = entries_today, unconfirmed, usage, presence
        self._now, self._save_settings, self._roots, self._load_map = now, save_settings, roots, load_map
        self._commit, self._count_commits, self._settle_s = commit, count_commits, settle_s
        self.sub = None  # the subscription's patterns (chatter_sub.Sub), when wired
        self._lock = voice._lock  # one lock for voice.json: the Announcer's
        self._away_log: dict = {"finished": [], "since": None}
        self._pending_settings: tuple[dict, dict] | None = None
        self._settings_timer: threading.Timer | None = None
        self._last_react: datetime | None = None
        self._last_tick: datetime | None = None
        voice.busy = self._hectic
        if presence is not None:
            presence.on_return = self.welcome_back

    # -- state ------------------------------------------------------------------------------------
    @property
    def _st(self) -> dict:
        return self.v.chatter_state()

    def _lvl(self) -> ch.Level:
        return ch.level(self._settings())

    def _day_bucket(self, name: str) -> dict:
        """A per-day dict in state, emptied when the day changes."""
        today = self._now().date().isoformat()
        b = self._st.get(name)
        if not isinstance(b, dict) or b.get("day") != today:
            b = {"day": today}
            self._st[name] = b
        return b

    def _present(self) -> bool:
        return self.presence is None or self.presence.state() == "present"

    def _hectic(self) -> bool:
        """The mood: three agents waiting, or usage past the warning. Quips stay out then."""
        try:
            s = self._settings()
            if sum(1 for r in self._agents() if r.get("status") == "waiting" and not r.get("ended_at")) >= 3:
                return True
            u = self._usage() or {}
            five = u.get("five_hour") or {}
            return bool(five.get("used") is not None and five["used"] >= s["usage_warn_pct"])
        except Exception:
            return False

    # -- ATLAS's own project ----------------------------------------------------------------------
    def atlas_project(self) -> str | None:
        s = self._settings()
        if s.get("voice_atlas_project"):
            return s["voice_atlas_project"]
        try:
            from .agents import resolve_project
            m = self._load_map()
            for root in self._roots:
                slug, _, mapped = resolve_project(str(root), m)
                if mapped:
                    return slug
            slugs = {p.get("slug") for p in (getattr(m, "projects", None) or [])}
            return "atlas" if "atlas" in slugs else None
        except Exception:
            log.exception("chatter: finding ATLAS's project failed")
            return None

    # -- the tick ---------------------------------------------------------------------------------
    def tick(self) -> None:
        try:
            self._tick()
        except Exception:
            log.exception("chatter: the tick failed")

    def _tick(self) -> None:
        if self.presence is not None:
            self.presence.poll()  # outside the lock: it may run loginctl
        self._tick_locked()

    @_locked
    def _tick_locked(self) -> None:
        now, s, lvl = self._now(), self._settings(), self._lvl()
        last, self._last_tick = self._last_tick, now
        self._debug_drop_back(s, now)
        quiet = in_quiet_hours(now, s["quiet_hours"]) and not lvl.debug
        if quiet:
            return
        self._digests(now, s, lvl)
        self._due_today(now, s, lvl, last)
        self._nudges(now, s, lvl)
        self._tease(now, s, lvl)
        self._musing(now, lvl)
        self._usage_cross(now, s)
        self._files_edited()

    def _debug_drop_back(self, s: dict, now: datetime) -> None:
        d = self._st.get("debug")
        if s.get("voice_chattiness") != "debug" or not isinstance(d, dict):
            return
        since = _parse(d.get("since"))
        if since is None or now - since >= ch.DEBUG_FOR:
            self._leave_debug(d.get("before") or "medium")

    @_locked
    def _leave_debug(self, before: str) -> None:
        with self._lock:
            self._st.pop("debug", None)
            self.v.save_state()
        if self._save_settings is not None and before in ch.TABLE and before != "debug":
            try:
                self._save_settings({"voice_chattiness": before})
            except Exception:
                log.exception("chatter: dropping back from Very high failed")

    @_locked
    def startup(self, flushed: int = 0) -> None:
        """Once per hub start: Very high drops back, and what changed while ATLAS was off."""
        try:
            s = self._settings()
            if s.get("voice_chattiness") == "debug":
                self._leave_debug((self._st.get("debug") or {}).get("before") or "medium")
            commit = self._commit()
            with self._lock:
                old = self._st.get("commit")
                self._st["commit"] = commit
                self._st["files"] = self._file_hashes()
                self.v.save_state()
            if commit and old and commit != old:
                n = self._count_commits(old, commit)
                values = {"count": vl.number_words(n)} if n else {}
                self.v.chat("self", "self.updated", values, "I've been updated.", key=("updated", commit),
                            why="an update since the last start")
            if flushed:
                self.v.chat("self", "self.flushed", {"count": vl.number_words(flushed)},
                            f"The drive is back. {vl.number_words(flushed).capitalize()} held notes are in the log.",
                            why="held notes flushed")
        except Exception:
            log.exception("chatter: the start failed")

    # -- briefings --------------------------------------------------------------------------------
    def facts(self, since: datetime | None = None) -> list[tuple[str, dict, str]]:
        """The facts worth a briefing, most urgent first, at most three: (pool, values, fixed line)."""
        now = self._now()
        out: list[tuple[str, dict, str]] = []
        waiting = [r for r in self._agents() if r.get("status") == "waiting" and not r.get("ended_at")]
        if waiting:
            names = vl.and_list([vl.spoken_project(r.get("project_name")) for r in waiting])
            out.append(("fact.waiting", {"projects": names, "count": vl.number_words(len(waiting))},
                        f"{names} {'is' if len(waiting) == 1 else 'are'} waiting on you."))
        finished = [f for f in self._away_log["finished"] if since is None or f[0] >= since]
        if finished:
            names = vl.and_list([vl.spoken_project(p) for _, p in finished])
            out.append(("fact.finished", {"projects": names, "count": vl.number_words(len(finished))},
                        f"{names} finished."))
        due = self._due_items(now)
        if len(due) == 1:
            text = vl.reminder_text(due[0].text)
            out.append(("fact.due_one", {"text": text}, f"{text} is due today."))
        elif due:
            closest = vl.reminder_text(due[0].text)
            out.append(("fact.due", {"count": vl.number_words(len(due)), "closest": closest},
                        f"{vl.number_words(len(due)).capitalize()} things are due today. {closest}"))
        if since is not None:
            quiet = self._settings()["quiet_hours"]
            n = sum(1 for it in self._items() for _, when in reminder_times(it, quiet) if since < when <= now)
            if n:
                out.append(("fact.reminders", {"count": vl.number_words(n)},
                            f"{vl.number_words(n).capitalize()} reminders went by."))
        return out[:3]

    def _compose(self, facts: list[tuple[str, dict, str]]) -> str:
        return " ".join(self.v.word(pool, values, fixed)[0] for pool, values, fixed in facts)

    @_locked
    def welcome_back(self, away_since: datetime) -> None:
        """The owner is back after being away (presence): what happened meanwhile, once."""
        try:
            s, lvl = self._settings(), self._lvl()
            if in_quiet_hours(self._now(), s["quiet_hours"]) and not lvl.debug:
                return
            facts = self.facts(away_since)
            text = None
            if self.sub is not None and facts:
                text = self.sub.briefing_for(self.fact_sheet(away_since))
            if facts:
                body = text or self._compose(facts)
                self.v.chat("briefing", "briefing.back", {"facts": body}, f"Welcome back. {body}",
                            key=("back", _iso(away_since)), why="you came back",
                            text=None if text is None else f"Welcome back. {text}")
            elif lvl.callbacks:
                self.v.chat("briefing", "briefing.back_quiet", {}, "Welcome back. Nothing happened.",
                            key=("back", _iso(away_since)), why="you came back")
            self._away_log = {"finished": [], "since": None}
        except Exception:
            log.exception("chatter: the welcome back failed")

    def fact_sheet(self, since: datetime | None) -> dict:
        """The welcome back's facts as data, for pattern B: names and numbers only, no agent text."""
        now = self._now()
        waiting = [vl.spoken_project(r.get("project_name")) for r in self._agents()
                   if r.get("status") == "waiting" and not r.get("ended_at")]
        finished = [vl.spoken_project(p) for t, p in self._away_log["finished"] if since is None or t >= since]
        due = [vl.reminder_text(i.text) for i in self._due_items(now)]
        return {"waiting": waiting, "finished": finished, "due_today": due[:3], "due_count": len(due)}

    def _digests(self, now: datetime, s: dict, lvl: ch.Level) -> None:
        d = self._day_bucket("digest")
        today = now.date()
        morning = datetime.combine(today, morning_time(s["quiet_hours"]))
        evening = datetime.combine(today, time.fromisoformat(s["voice_last_call"]))
        if lvl.name == "mute":
            return
        if "morning" not in d and not lvl.debug and now >= min(morning + MORNING_FOR, evening):
            d["morning"] = "skipped"  # too late for "good morning": the first due-today repeat is the roll call
            self.v.save_state()
        if "morning" not in d and (lvl.debug or (now >= morning and self._present())):
            d["morning"] = _iso(now)
            self.v.save_state()
            facts = self.facts(None)
            if facts:
                body = self._compose(facts)
                self.v.chat("briefing", "briefing.morning", {"facts": body}, f"Good morning. {body}", why="the morning digest")
            else:
                self.v.chat("briefing", "briefing.morning_quiet", {}, "Good morning. Nothing due today.",
                            why="the morning digest")
        if "evening" not in d and (lvl.debug or now >= evening):
            d["evening"] = _iso(now)
            self.v.save_state()
            facts: list[tuple[str, dict, str]] = []
            filed = self._entries_today(today.isoformat())
            if filed:
                facts.append(("fact.filed", {"count": vl.number_words(filed)}, f"I filed {vl.number_words(filed)} notes today."))
            pending = self._unconfirmed()
            if pending:
                facts.append(("fact.unconfirmed", {"count": vl.number_words(pending)},
                              f"{vl.number_words(pending).capitalize()} suggestions are still waiting for a tick."))
            if facts:
                body = self._compose(facts)
                self.v.chat("briefing", "briefing.evening", {"facts": body}, f"Evening. {body}", why="the evening digest")
            else:
                self.v.chat("briefing", "briefing.evening_quiet", {}, "Evening. Nothing left hanging today.",
                            why="the evening digest")
            self._last_call(now, s, lvl)
            if not filed and lvl.callbacks:
                self.v.chat("nudge", "nudge.no_capture", {}, "Nothing written down today.", key=("no_capture", today.isoformat()),
                            why="nothing written today")

    # -- due today --------------------------------------------------------------------------------
    def _due_items(self, now: datetime) -> list:
        """Open items due today and not yet past (date-only ones count all day), closest first."""
        out = []
        for it in self._items():
            if getattr(it, "status", None) != "open":
                continue
            when = _due_on(it, now.date())
            if when is None:
                continue
            timed = len(it.due) > 10
            if timed and when <= now:
                continue  # past: Today's overdue has it, and the reminder already spoke
            out.append((when if timed else datetime.combine(now.date(), time(23, 59)), it))
        out.sort(key=lambda x: x[0])
        return [it for _, it in out]

    def _due_today(self, now: datetime, s: dict, lvl: ch.Level, last: datetime | None) -> None:
        if lvl.due_every is None or not s["voice_events"].get("due_today"):
            return
        b = self._day_bucket("due")
        spoken = b.setdefault("spoken", [])
        due = self._due_items(now)
        # The approach slots of a timed item: 2 h, 1 h and 15 min before (at debug, one after another).
        for it in due:
            when = _due_on(it, now.date())
            if len(it.due) <= 10:
                continue
            for gap in APPROACH:
                key = f"{it.id}|{int(gap.total_seconds())}"
                if key in spoken:
                    continue
                at = when - gap
                if lvl.debug:
                    fire = now - _parse(b.get("slot_at") or "1970-01-01T00:00:00") >= lvl.slot_gap
                else:
                    fire = at <= now and now - at <= timedelta(minutes=5)
                    if at <= now and not fire:
                        spoken.append(key)  # passed while ATLAS was off or quiet: never said late
                        continue
                if not fire:
                    continue
                spoken.append(key)
                b["slot_at"] = _iso(now)
                self.v.save_state()
                text = vl.reminder_text(it.text)
                self.v.chat("due_today", "due_today.soon", {"text": text, "left": SLOT_WORDS[gap]},
                            f"{text} That's due {SLOT_WORDS[gap]}.", budgeted=False, why=f"due {SLOT_WORDS[gap]}")
                b["repeat_at"] = _iso(now)  # one line a tick: the repeat waits a whole cadence from here
                self.v.save_state()
                return
        # The repeat, at the level's cadence, while any are left (after the last call, timed ones only).
        if b.get("last_call"):
            due = [it for it in due if len(it.due) > 10]
        last_repeat = _parse(b.get("repeat_at"))
        if not due or (last_repeat is not None and now - last_repeat < lvl.due_every):
            return
        morning = self._day_bucket("digest").get("morning")
        if last_repeat is None and not lvl.debug and morning is None:
            return  # the morning digest is the day's first roll call
        if not lvl.debug and self.presence is not None and self.presence.state() == "away":
            return
        if last_repeat is None and not lvl.debug and morning != "skipped":
            b["repeat_at"] = _iso(now)  # the digest just named them: the first repeat is one cadence later
            self.v.save_state()
            return
        b["repeat_at"] = _iso(now)
        self.v.save_state()
        if len(due) == 1:
            text = vl.reminder_text(due[0].text)
            self.v.chat("due_today", "due_today.repeat_one", {"text": text}, f"Still due today. {text}",
                        budgeted=False, why="due today")
        else:
            closest = vl.reminder_text(due[0].text)
            self.v.chat("due_today", "due_today.repeat", {"count": vl.number_words(len(due)), "closest": closest},
                        f"{vl.number_words(len(due)).capitalize()} things still due today. {closest}",
                        budgeted=False, why="due today")

    def _last_call(self, now: datetime, s: dict, lvl: ch.Level) -> None:
        if lvl.due_every is None or not s["voice_events"].get("due_today"):
            return
        open_today = [it for it in self._due_items(now) if len(it.due) <= 10]
        if not open_today:
            return
        self._day_bucket("due")["last_call"] = True
        if len(open_today) == 1:
            text = vl.reminder_text(open_today[0].text)
            self.v.chat("due_today", "due_today.last_call", {"text": text}, f"Last call. {text} Still open today.",
                        budgeted=False, why="the last call")
        else:
            texts = vl.cap(" ".join(vl.reminder_text(i.text) for i in open_today))
            self.v.chat("due_today", "due_today.last_call_many", {"count": vl.number_words(len(open_today)), "texts": texts},
                        f"Last call. {vl.number_words(len(open_today)).capitalize()} things still open today. {texts}",
                        budgeted=False, why="the last call")

    # -- nudges, teasing, musings, usage ----------------------------------------------------------
    def _nudges(self, now: datetime, s: dict, lvl: ch.Level) -> None:
        if lvl.name == "mute":
            return
        for r in self._agents():
            since = _parse(r.get("status_since"))
            if r.get("status") != "waiting" or r.get("ended_at") or since is None:
                continue
            wait = now - since
            if NUDGE_WAIT <= wait < timedelta(hours=6) or (lvl.debug and wait >= timedelta(minutes=1)):
                project = vl.spoken_project(r.get("project_name"))
                self.v.chat("nudge", "nudge.waiting", {"project": project, "duration": vl.duration_words(wait.total_seconds())},
                            f"{project} has been waiting {vl.duration_words(wait.total_seconds())}.",
                            key=("nudge_wait", r.get("session_id"), r.get("status_since")), why="an agent left waiting")
        b = self._day_bucket("stale")
        if b.get("done") and not lvl.debug:
            return
        cutoff = now - timedelta(days=s["nudge_days"])
        stale = sorted((it for it in self._items() if it.status == "open" and it.kind == "waiting"
                        and (_parse(it.created) or now) <= cutoff), key=lambda it: it.created)
        for it in stale:
            if it.id in b.setdefault("items", []):
                continue
            days = (now - _parse(it.created)).days
            text = vl.reminder_text(it.text)
            said = self.v.chat("nudge", "nudge.stale", {"text": text, "days": _days(days)},
                               f"{text} That's been waiting {_days(days)}.", why="waiting on someone for a while")
            if said and said["spoken"]:
                b["items"].append(it.id)
                b["done"] = True
                self.v.save_state()
            return

    def _tease(self, now: datetime, s: dict, lvl: ch.Level) -> None:
        if lvl.tease_every is None or not s["voice_events"].get("tease"):
            return
        slug = self.atlas_project()
        if not slug:
            return
        b = self._day_bucket("tease")
        last = _parse(b.get("at"))
        if last is not None and now - last < lvl.tease_every:
            return
        if lvl.tease_max is not None and b.get("count", 0) >= lvl.tease_max:
            return
        today = now.date()
        mine = sorted((it for it in self._items() if it.status == "open" and it.project == slug
                       and _due_on(it, today) is None and it.id not in b.get("items", [])),
                      key=lambda it: it.created)
        if lvl.debug and not mine:
            b["items"] = []
            mine = sorted((it for it in self._items() if it.status == "open" and it.project == slug
                           and _due_on(it, today) is None), key=lambda it: it.created)
        if not mine:
            return
        it = mine[0]
        days = max(1, (now - (_parse(it.created) or now)).days)
        text = vl.reminder_text(it.text)
        said = self.v.chat("tease", "tease", {"text": text, "days": _days(days)}, f"{text} That's been open {_days(days)}.",
                           why="one of my own to-dos")
        if said and said["spoken"]:
            b["at"], b["count"] = _iso(now), b.get("count", 0) + 1
            b.setdefault("items", []).append(it.id)
            self.v.save_state()

    def _musing(self, now: datetime, lvl: ch.Level) -> None:
        if lvl.musing_every is None:
            return
        last = _parse(self._st.get("musing_at"))
        if last is not None and now - last < lvl.musing_every:
            return
        recent = [_parse(x.get("at")) for x in self.v.recent if x.get("spoken")]
        latest = max((t for t in recent if t), default=None)
        if not lvl.debug and latest is not None and now - latest < QUIET_FOR_MUSING:
            return
        self._st["musing_at"] = _iso(now)
        self.v.save_state()
        self.v.chat("musing", "self.musing", {}, "Still here.", why="nothing's happening")

    def _usage_cross(self, now: datetime, s: dict) -> None:
        u = self._usage() or {}
        five = u.get("five_hour") or {}
        used = five.get("used")
        if used is None or used < s["usage_warn_pct"]:
            return
        window = five.get("resets_at") or "?"
        if self._st.get("usage_window") == window:
            return
        self._st["usage_window"] = window
        self.v.save_state()
        self.v.chat("self", "self.usage", {"percent": vl.number_words(int(used))},
                    f"You're at {vl.number_words(int(used))} percent of your window.", why="usage crossed the warning")

    def _file_hashes(self) -> dict:
        out = {}
        for name, path in (("quotes", quotes_path(self._brain)), ("lines", lines_path(self._brain))):
            try:
                out[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            except OSError:
                out[name] = None
        return out

    def _files_edited(self) -> None:
        now_hashes = self._file_hashes()
        with self._lock:
            old = self._st.get("files")
            self._st["files"] = now_hashes
            if old != now_hashes:
                self.v.save_state()
        if not isinstance(old, dict):
            return
        for name in ("quotes", "lines"):
            if old.get(name) and now_hashes.get(name) and old[name] != now_hashes[name]:
                self.v.chat("self", f"self.{name}_edited", {}, "You've been editing my lines.", why=f"{name}.md changed")
                return

    # -- agents -----------------------------------------------------------------------------------
    def agent_transition(self, t: dict) -> None:
        try:
            self._agent_transition(t)
        except Exception:
            log.exception("chatter: an agent transition failed")

    @_locked
    def _agent_transition(self, t: dict) -> None:
        before, after, sid = t.get("before"), t.get("after"), t.get("session_id")
        at = _parse(t.get("at")) or self._now()
        if after == "running" and self.presence is not None:
            self.presence.seen("agent prompt" if before != "waiting" else "agent answer")
        if after == "finished" and self.presence is not None and self.presence.state() == "away":
            self._away_log["finished"].append((at, t.get("project_name")))
            self._away_log["finished"] = self._away_log["finished"][-20:]
            if self.sub is not None:
                self.sub.facts_changed(self.fact_sheet(self.presence.away_since()))
        slug = self.atlas_project()
        mine = slug is not None and t.get("project") == slug
        if mine and after == "running" and before in (None, "idle", "finished"):
            with self._lock:
                seen = self._st.setdefault("atlas_sessions", {})
                last = _parse(seen.get(sid))
                if last is not None and at - last < SESSION_LINE_GAP:
                    return
                seen[sid] = _iso(at)
                if len(seen) > 50:
                    self._st["atlas_sessions"] = dict(sorted(seen.items(), key=lambda kv: kv[1])[-25:])
                self.v.save_state()
            project = vl.spoken_project(t.get("project_name"))
            self.v.chat("self", "self.atlas_session", {"project": project}, f"{project} is working on me.",
                        why="a session in my own repo")
        if mine and after == "finished" and before == "running":
            start = _parse(t.get("since"))
            if start is not None and (at - start) >= timedelta(minutes=2):
                d = vl.duration_words((at - start).total_seconds())
                self.v.chat("self", "self.atlas_finished", {"duration": d}, f"They finished working on me. It took {d}.",
                            why="a session in my own repo finished")
        if after == "waiting" and self._lvl().callbacks:
            b = self._day_bucket("waits")
            name = t.get("project_name") or "?"
            n = b.get(name, 0) + 1
            b[name] = n
            self.v.save_state()
            if n >= 3:
                project = vl.spoken_project(name)
                self.v.chat("nudge", "callback.waiting_today", {"project": project, "count": vl.number_words(n)},
                            f"That's {vl.number_words(n)} times today for {project}.", key=("waits", name, n),
                            why="the same project, again today")

    # -- what the owner does ----------------------------------------------------------------------
    @_locked
    def react(self, kind: str, count: int = 0) -> dict | None:
        """confirm_all, dismiss_all, flip_ask, flip_note, correction, retry, pause."""
        try:
            lvl = self._lvl()
            if self.presence is not None and kind != "pause":
                self.presence.seen("app")
            if kind in ("correction", "retry", "flip_ask", "flip_note"):
                self._tally(kind)
            if kind == "pause":
                self._announce_restraint()
            if lvl.reactions == "off" or (lvl.reactions == "mistakes" and kind not in ("correction", "flip_ask", "flip_note")):
                return None
            now = self._now()
            if self._last_react is not None and now - self._last_react < REACT_GAP and not lvl.debug:
                return None
            self._last_react = now
            b = self._day_bucket("reacts")
            b[kind] = b.get(kind, 0) + 1
            values = {"count": vl.number_words(count or b[kind])}
            return self.v.chat("react", f"react.{kind}", values, "Noted.", budgeted=False, why=kind.replace("_", " "))
        except Exception:
            log.exception("chatter: the reaction failed")
            return None

    @_locked
    def _announce_restraint(self) -> None:
        """Once, when shushing just made an event restrained (CH: learned restraint)."""
        event = self.v.take_restraint_hit()
        if event:
            what = EVENT_WORDS.get(event, event)
            self.v.chat("self", "self.restraint", {"what": what}, f"Noted. I'll keep the {what} to myself.",
                        budgeted=False, why="you keep shushing me")

    def _tally(self, kind: str) -> None:
        """Counts per day for ATLAS's week (pattern C), kept for two weeks."""
        today = self._now().date()
        tally = self._st.setdefault("tally", {})
        day = tally.setdefault(today.isoformat(), {})
        day[kind] = day.get(kind, 0) + 1
        for d in [d for d in tally if d < (today - timedelta(days=14)).isoformat()]:
            tally.pop(d, None)
        self.v.save_state()

    @_locked
    def on_confirmed(self, items: list, all_of_card: bool) -> None:
        try:
            if all_of_card and len(items) >= 2:
                self.react("confirm_all", len(items))
            slug = self.atlas_project()
            for it in items:
                if slug and getattr(it, "project", None) == slug:
                    text = vl.reminder_text(it.text)
                    self.v.chat("self", "self.atlas_item", {"text": text},
                                f"You've put me on your to-do list. {text}", key=("atlas_item", it.id),
                                why="a task about me")
                    return
        except Exception:
            log.exception("chatter: on confirm failed")

    @_locked
    def on_filed(self, files: list[str]) -> None:
        """A filing the owner waited on: a note about ATLAS itself gets a word."""
        try:
            slug = self.atlas_project()
            if slug and any(isinstance(f, str) and f.replace("\\", "/").startswith(f"projects/{slug}/") for f in files):
                self.v.chat("self", "self.atlas_note", {}, "A note about me. Filed it.", why="a note about me")
        except Exception:
            log.exception("chatter: on filed failed")

    # -- settings ---------------------------------------------------------------------------------
    @_locked
    def settings_changed(self, before: dict, after: dict) -> None:
        """A Settings save. Very high's bookkeeping and the shh happen now; the remark waits SETTLE_S
        for the burst to end, then names the biggest change."""
        try:
            b_lvl, a_lvl = before.get("voice_chattiness"), after.get("voice_chattiness")
            if a_lvl != b_lvl:
                self.v.reset_restraint()
                with self._lock:
                    if a_lvl == "debug":
                        self._st["debug"] = {"since": _iso(self._now()), "before": b_lvl or "medium"}
                        self._st.pop("digest", None)  # Very high plays the digests on the next tick
                    else:
                        self._st.pop("debug", None)
                    self.v.save_state()
            dropped = ch.rank(a_lvl or "medium") < ch.rank(b_lvl or "medium")
            if (before.get("voice_enabled") and not after.get("voice_enabled")) or dropped:
                self.v.shh()
                self._announce_restraint()
            with self._lock:
                first = self._pending_settings[0] if self._pending_settings else before
                self._pending_settings = (first, after)
                if self._settings_timer is not None:
                    self._settings_timer.cancel()
                if self._settle_s <= 0:
                    self._settings_timer = None
                else:
                    self._settings_timer = threading.Timer(self._settle_s, self.settings_settled)
                    self._settings_timer.daemon = True
                    self._settings_timer.start()
            if self._settle_s <= 0:
                self.settings_settled()
        except Exception:
            log.exception("chatter: on settings failed")

    @_locked
    def settings_settled(self) -> None:
        with self._lock:
            pending, self._pending_settings, self._settings_timer = self._pending_settings, None, None
        if pending is None:
            return
        before, after = pending
        change = settings_remark(before, after)
        if change is None:
            return
        pool, values, fixed = change
        self.v.chat("self", pool, values, fixed, budgeted=pool != "self.set.other",
                    force=pool == "self.set.debug", quip=pool != "self.set.sass_off", why="you changed my settings")

    def close(self) -> None:
        with self._lock:
            if self._settings_timer is not None:
                self._settings_timer.cancel()
                self._settings_timer = None

    # -- the debug panel --------------------------------------------------------------------------
    TRY = ("briefing", "morning", "evening", "due_today", "last_call", "nudge", "tease", "musing", "self", "react")

    @_locked
    def try_now(self, what: str) -> dict | None:
        """Very high's Try it now: fire one kind of line at once, from real state."""
        now = self._now()
        if what == "briefing":
            facts = self.facts(now - timedelta(hours=1))
            body = self._compose(facts) if facts else ""
            return (self.v.chat("briefing", "briefing.back", {"facts": body}, f"Welcome back. {body}", force=True,
                                why="try it now") if facts else
                    self.v.chat("briefing", "briefing.back_quiet", {}, "Welcome back.", force=True, why="try it now"))
        if what in ("morning", "evening"):
            with self._lock:
                self._day_bucket("digest").pop(what, None)
            self._digests(now, self._settings(), ch.TABLE["debug"])
            return {"ok": True}
        if what == "due_today":
            with self._lock:
                self._day_bucket("due").pop("repeat_at", None)
            self._due_today(now, self._settings(), ch.TABLE["debug"], self._last_tick)
            return {"ok": True}
        if what == "last_call":
            self._last_call(now, self._settings(), ch.TABLE["debug"])
            return {"ok": True}
        if what == "nudge":
            with self._lock:
                self._day_bucket("stale").pop("done", None)
            self._nudges(now, self._settings(), ch.TABLE["debug"])
            return {"ok": True}
        if what == "tease":
            with self._lock:
                self._day_bucket("tease").pop("at", None)
            self._tease(now, self._settings(), ch.TABLE["debug"])
            return {"ok": True}
        if what == "musing":
            with self._lock:
                self._st.pop("musing_at", None)
            return self.v.chat("musing", "self.musing", {}, "Still here.", force=True, why="try it now")
        if what == "self":
            u = (self._usage() or {}).get("five_hour") or {}
            pct = int(u.get("used") or 0)
            return self.v.chat("self", "self.usage", {"percent": vl.number_words(pct)},
                               f"You're at {vl.number_words(pct)} percent of your window.", force=True, why="try it now")
        if what == "react":
            self._last_react = None
            return self.v.chat("react", "react.correction", {"count": "one"}, "Noted.", force=True, why="try it now")
        raise ValueError(f"unknown: {what}")


def settings_remark(before: dict, after: dict) -> tuple[str, dict, str] | None:
    """The biggest change in a Settings save, as (pool, values, fixed line), or None."""
    b, a = before.get("voice_chattiness"), after.get("voice_chattiness")
    if a != b:
        if a == "mute":
            return "self.set.mute", {}, "Fine. I'll sulk silently."
        if a == "debug":
            return "self.set.debug", {}, "Very high. You asked for it."
        up = ch.rank(a or "medium") > ch.rank(b or "medium")
        name = ch.SPOKEN.get(a or "medium", "Medium")
        return ("self.set.chattiness_up" if up else "self.set.chattiness_down"), {"level": name}, f"{name}. Noted."
    if before.get("voice_enabled") != after.get("voice_enabled"):
        return None  # off can't speak; on is its own announcement
    if before.get("voice_sass") != after.get("voice_sass"):
        return ("self.set.sass_on", {}, "Sass is back.") if after.get("voice_sass") else ("self.set.sass_off", {}, "Sass off.")
    if before.get("voice_subscription") != after.get("voice_subscription"):
        return (("self.set.subscription_on", {}, "I can think again.") if after.get("voice_subscription")
                else ("self.set.subscription_off", {}, "Back to my own wits, then."))
    be, ae = before.get("voice_events") or {}, after.get("voice_events") or {}
    for k in ae:
        if be.get(k) != ae.get(k) and k in EVENT_WORDS:
            what = EVENT_WORDS[k]
            if ae[k]:
                return "self.set.event_on", {"what": what.capitalize()}, f"{what.capitalize()} are back on."
            return "self.set.event_off", {"what": what}, f"No more {what} from me."
    for key, up, down in (("voice_speed", "self.set.speed_up", "self.set.speed_down"),
                          ("voice_volume", "self.set.volume_up", "self.set.volume_down")):
        if before.get(key) != after.get(key) and isinstance(after.get(key), (int, float)) and isinstance(before.get(key), (int, float)):
            return (up if after[key] > before[key] else down), {}, "Noted."
    if before.get("quiet_hours") != after.get("quiet_hours"):
        return "self.set.quiet_hours", {}, "New bedtime. Noted."
    if any(before.get(k) != after.get(k) for k in after):
        return "self.set.other", {}, "Settings changed. I noticed."
    return None


def git_commit(repo: Path) -> str | None:
    """The code's commit, read-only (a dev checkout or the live copy); None when it can't tell."""
    try:
        r = subprocess.run(["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def git_count(repo: Path, old: str, new: str) -> int | None:
    try:
        r = subprocess.run(["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo), "rev-list", "--count",
                            f"{old}..{new}"], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
        return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
