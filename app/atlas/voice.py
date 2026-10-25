"""The voice (phase 4 design §3–§4): when to speak, what, and the queue in front of the worker.

`Announcer` decides. It takes agent transitions (from AgentTracker, in a worker thread), the
reminder tick (every 30 s), the tablet's readbacks and the one input's replies (`reply`: the line
comes back as the screen's caption too), runs every line through the gates (on, the
files installed, the event ticked, not quiet hours, not paused, not a repeat), merges lines of one
kind that arrive within a few seconds, adds a quip, and hands the result to `Speaker`.

`Speaker` owns the worker process (`voice_worker.py`): it starts it on the first line, sends one
line at a time, kills it after `IDLE_S` with nothing to say, and never lets a failure reach the
hub: the last error is kept for Settings. Both are plain threads: nothing here may hold up a request.

Per-OS state lives in `<state>/voice.json`: the reminder tick's last time, a pause, the quotes'
positions. A pause doesn't follow the owner to the other OS (design §7.2)."""
from __future__ import annotations

import itertools
import json
import logging
import os
import queue
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import voice_lines as vl
from .quotes import POOLS, QuotePicker, load_quotes

log = logging.getLogger(__name__)

TICK_S = 30
MERGE_S = 3.0  # lines of one kind arriving within this become one
QUEUE_MAX = 5
STALE_S = 60  # a line older than this when its turn comes is dropped
IDLE_S = 600  # the worker exits after this long with nothing to say
REPLY_TIMEOUT_S = 120
LOG_MAX = 1_000_000  # bytes of voice-worker.log before it's rotated
CATCH_UP_MAX = timedelta(hours=24)
CATCH_UP_GAP = timedelta(minutes=5)  # a tick this late means the laptop slept: treat it as a start  # more missed than this counts as none: Today's Overdue has them
DEFAULT_MORNING = time(8, 0)  # a date-only reminder when quiet hours are switched off
SAMPLE_TEXT = ("Good {time_of_day}. This is how I'll sound.", "Do let me know if I'm too loud.")


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_quiet_hours(now: datetime, quiet: list[str]) -> bool:
    """Quiet hours wrap past midnight (["23:00", "08:00"]); equal start and end means none."""
    start, end = _hhmm(quiet[0]), _hhmm(quiet[1])
    t = now.time()
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end


def morning_time(quiet: list[str]) -> time:
    """When a date-only reminder speaks, and what "until morning" means: the end of quiet hours."""
    start, end = _hhmm(quiet[0]), _hhmm(quiet[1])
    return DEFAULT_MORNING if start == end else end


def next_morning(now: datetime, quiet: list[str]) -> datetime:
    t = datetime.combine(now.date(), morning_time(quiet))
    return t if t > now else t + timedelta(days=1)


def reminder_times(item, quiet: list[str]) -> list[tuple[str, datetime]]:
    """(raw value, when it speaks) for an item: an open one at its remind_at and its due time; a
    snoozed one at its snooze_until, then at a remind_at or due later than that (a snooze that ran
    out leaves the item open, as Today shows it). A date-only value speaks at the end of quiet
    hours. A value that isn't a plain local date or time is skipped, never fatal for the rest."""
    if item.status == "snoozed":
        raws = [item.snooze_until, item.remind_at, item.due]
    elif item.status == "open":
        raws = [item.remind_at, item.due]
    else:
        return []
    out: list[tuple[str, datetime]] = []
    for raw in raws:
        if not isinstance(raw, str) or not raw or any(r == raw for r, _ in out):
            continue
        try:
            when = (datetime.combine(date.fromisoformat(raw), morning_time(quiet)) if len(raw) == 10
                    else datetime.fromisoformat(raw))
        except (TypeError, ValueError):
            continue
        if when.tzinfo is not None:  # an offset from a hand-edited items.json: this machine's local time
            when = when.astimezone().replace(tzinfo=None)
        out.append((raw, when))
    if item.status == "snoozed" and out and out[0][0] == item.snooze_until:
        out = [out[0]] + [x for x in out[1:] if x[1] > out[0][1]]
    elif item.status == "snoozed":
        out = []  # snoozed without a readable snooze_until: Today decides, the voice stays out
    return out


@dataclass
class Line:
    event: str
    info: str
    meta: dict
    created: datetime
    parts: list[str] = field(default_factory=list)
    request: dict = field(default_factory=dict)


class VoiceState:
    """`<state>/voice.json`, read once and written atomically on change. Thread-safe enough: the
    Announcer holds its own lock around every use."""

    def __init__(self, path: Path):
        self.path = path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("quotes", {})
        if not isinstance(self.data["quotes"], dict):
            self.data["quotes"] = {}

    def get_time(self, key: str) -> datetime | None:
        try:
            return datetime.fromisoformat(self.data[key]) if self.data.get(key) else None
        except (TypeError, ValueError):
            return None

    def set_time(self, key: str, value: datetime | None) -> None:
        self.data[key] = value.isoformat(timespec="seconds") if value else None

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8", newline="\n")
            os.replace(tmp, self.path)
        except OSError as e:  # the voice never takes anything down with it
            log.warning("could not save %s: %s", self.path, e)


class Announcer:
    def __init__(self, *, settings: Callable[[], dict], brain: Path, state_path: Path,
                 files: Callable[[dict], dict | None], items: Callable[[], list],
                 speaker: "Speaker", now: Callable[[], datetime] = datetime.now,
                 merge_s: float = MERGE_S, rng=None):
        self._settings, self._brain, self._files, self._items = settings, Path(brain), files, items
        self._speaker, self._now, self._merge_s = speaker, now, merge_s
        self._lock = threading.RLock()
        self._state = VoiceState(state_path)
        self._picker = QuotePicker(self._state.data["quotes"], rng)
        self._pending: dict[str, list[Line]] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._turn_start: dict[str, datetime] = {}
        self._spoken: set[tuple] = set()
        self._ticked = False

    # -- state the UI shows ---------------------------------------------------------------------
    def paused_until(self) -> datetime | None:
        with self._lock:
            until = self._state.get_time("paused_until")
            return until if until and until > self._now() else None

    def view(self) -> dict:
        s = self._settings()
        _, problems = load_quotes(self._brain)
        until = self.paused_until()
        return {"installed": self._files(s) is not None, "model": s["voice_model"], "enabled": s["voice_enabled"],
                "paused_until": until.isoformat(timespec="minutes") if until else None,
                "quiet_now": in_quiet_hours(self._now(), s["quiet_hours"]),
                "last_error": self._speaker.last_error, "quote_problems": problems[:5]}

    # -- controls -----------------------------------------------------------------------------
    def pause(self, minutes: int | None = None, *, until_morning: bool = False) -> datetime:
        now = self._now()
        until = next_morning(now, self._settings()["quiet_hours"]) if until_morning else now + timedelta(minutes=minutes or 60)
        with self._lock:
            self._state.set_time("paused_until", until)
            self._state.save()
            self._drop_pending()
        self._speaker.stop()
        return until

    def resume(self) -> None:
        with self._lock:
            self._state.set_time("paused_until", None)
            self._state.save()

    def stop(self) -> None:
        with self._lock:
            self._drop_pending()
        self._speaker.stop()

    def sample(self, overrides: dict | None = None) -> bool:
        """Play the sample line in the saved settings, or in unsaved ones from Settings. It ignores
        quiet hours, pause and the on/off switch: the owner asked for it."""
        s = {**self._settings(), **(overrides or {})}
        files = self._files(s)
        if files is None:
            return False
        now = self._now()
        tod = vl.sentence(SAMPLE_TEXT[0].format(time_of_day=_tod(now)))
        line = Line("sample", tod, {}, now, parts=[tod, SAMPLE_TEXT[1]])
        line.request = _request(line.parts, s, files)
        self._speaker.stop()
        return self._speaker.say(line)

    # -- events ---------------------------------------------------------------------------------
    def agent_transition(self, t: dict) -> None:
        """From AgentTracker (a worker thread): a session's status changed."""
        try:
            self._agent_transition(t)
        except Exception:
            log.exception("voice: agent transition failed")

    def _agent_transition(self, t: dict) -> None:
        sid, before, after = t["session_id"], t.get("before"), t["after"]
        at = _parse(t.get("at")) or self._now()
        with self._lock:
            if after == "running" and before != "waiting":
                self._turn_start[sid] = at  # a permission prompt mid-turn doesn't restart the clock
            if after == "waiting" and t.get("kind") == "stop_failure":
                self._turn_start.pop(sid, None)  # an error stop ends the turn: the next prompt starts a new one
            if after == "waiting":
                key = ("agent_waiting", sid, t.get("at"))
                line = vl.agent_waiting_line(t.get("project_name"), t.get("kind"))
                self._submit("agent_waiting", line, {"project": t.get("project_name"), "sid": sid}, key)
            elif after == "finished" and before in ("running", "waiting"):
                start = self._turn_start.pop(sid, None) or _parse(t.get("since"))
                if start is None:
                    return
                seconds = (at - start).total_seconds()
                if seconds < self._settings()["voice_finish_min_s"]:
                    return
                line = vl.agent_finished_line(t.get("project_name"), seconds)
                self._submit("agent_finished", line, {"project": t.get("project_name"), "sid": sid,
                                                      "duration": vl.duration_words(seconds)},
                             ("agent_finished", sid, t.get("at")))

    def readback_note(self, text: str) -> None:
        with self._lock:
            self._submit("readback", vl.readback_note(text), {"text": vl.cap(text, 120)}, None)

    def readback_answer(self, answers: dict[int, str]) -> None:
        """Only the owner's values: the item labels were written by an agent (atlas-request)."""
        with self._lock:
            self._submit("readback", vl.readback_answer(answers), {}, None)

    def reply(self, kind: str, count: int = 0, others: int = 0) -> str:
        """What ATLAS says after the one input filed a note or answered a question (VI4), spoken
        through the same gates as every line (on, installed, the `reply` switch, quiet hours,
        pause). Returns the line as composed, quip and all, whether it was spoken or not: the
        screen shows it as the caption, so the sass survives a silent voice. At once, never merged."""
        info = vl.reply_line(kind, count, others)
        try:
            with self._lock:
                s, now = self._settings(), self._now()
                parts = [info]
                pool_name = f"reply_{kind}"
                if s["voice_sass"] and pool_name in POOLS:
                    pool, _ = load_quotes(self._brain)
                    values = {"count": vl.number_words(count) if count else None,
                              "them": ("it" if count == 1 else "them") if kind == "tasks" else None}
                    quip = self._picker.pick(pool_name, pool.get(pool_name, []), values, now)
                    self._state.save()
                    if quip:
                        parts.append(quip)
                files = self._files(s)
                if files is not None and self._gate("reply", s, now):
                    line = Line("reply", info, {"line": info}, now, parts=parts)
                    line.request = _request(parts, s, files)
                    self._speaker.say(line)
                return " ".join(parts)
        except Exception:
            log.exception("voice: the reply failed")
            return info

    def tick(self) -> None:
        """Every TICK_S: speak the reminders whose time fell in (last tick, now]. The first tick of
        a run is the catch-up: what fell due while the hub was off becomes one line."""
        try:
            self._tick()
        except Exception:
            log.exception("voice: reminder tick failed")

    def _tick(self) -> None:
        now = self._now()
        quiet = self._settings()["quiet_hours"]
        with self._lock:
            last = self._state.get_time("last_tick")
            if last is None or last >= now or now - last > CATCH_UP_MAX:
                # the very first run, a clock that went back, or too long ago to matter
                self._ticked = True
                self._state.set_time("last_tick", now)
                self._state.save()
                return
            # Read the items before moving last_tick: if the store can't be read now (locked, the
            # drive gone for a moment), the next tick covers this window again.
            items = self._items()
            # The first tick of a run, or one after a suspend: what fell due meanwhile is a catch-up.
            first = not self._ticked or now - last > CATCH_UP_GAP
            self._ticked = True
            self._state.set_time("last_tick", now)
            self._state.save()
            due = []
            for item in items:
                for raw, when in reminder_times(item, quiet):
                    if last < when <= now and ("reminder", item.id, raw) not in self._spoken:
                        due.append((item, raw))
            if not due:
                return
            if first and len(due) > 1:
                for item, raw in due:
                    self._spoken.add(("reminder", item.id, raw))
                self._submit("catch_up", vl.catch_up_line(len(due)), {"count": vl.number_words(len(due))}, None)
                return
            words = self._settings().get("voice_workout_words") or []
            for item, raw in due:
                workout = vl.is_workout(item.text, words)
                opener = vl.WORKOUT_OPENER if workout else "A reminder."
                meta = {"text": vl.cap(item.text, 120), **({"pool": "workout"} if workout else {})}
                self._submit("reminder", vl.reminder_line(item.text, item.due, now, opener), meta,
                             ("reminder", item.id, raw))

    # -- the gates, the merge, the quip ---------------------------------------------------------
    def _gate(self, event: str, s: dict, now: datetime) -> bool:
        if not s["voice_enabled"] or self._files(s) is None:
            return False
        kind = "reminder" if event == "catch_up" else event
        if not s["voice_events"].get(kind, False):
            return False
        if in_quiet_hours(now, s["quiet_hours"]):
            return False
        until = self._state.get_time("paused_until")
        return not (until and until > now)

    def _submit(self, event: str, info: str, meta: dict, key: tuple | None) -> None:
        """Caller holds the lock."""
        now = self._now()
        if key is not None:
            if key in self._spoken:
                return
            self._spoken.add(key)
            if len(self._spoken) > 5000:  # a long-running hub: forget the oldest half
                self._spoken = set(itertools.islice(self._spoken, 2500, None))
        if not self._gate(event, self._settings(), now):
            return
        line = Line(event, info, {**meta, "line": info}, now)
        if self._merge_s <= 0 or event in ("readback", "catch_up"):
            self._send([line])
            return
        self._pending.setdefault(event, []).append(line)
        if event not in self._timers:
            timer = threading.Timer(self._merge_s, self.flush, args=(event,))
            timer.daemon = True
            self._timers[event] = timer
            timer.start()

    def flush(self, event: str) -> None:
        with self._lock:
            self._timers.pop(event, None)
            lines = self._pending.pop(event, [])
            if lines:
                self._send(lines)

    def _send(self, lines: list[Line]) -> None:
        """Caller holds the lock: one spoken line (merged when several), plus its quip."""
        s, now = self._settings(), self._now()
        files = self._files(s)
        if files is None or not self._gate(lines[0].event, s, now):
            return  # switched off, paused or gone quiet while the lines waited to merge
        event = lines[0].event
        if event.startswith("agent_"):  # one session's quick succession of lines counts once: its latest
            latest: dict = {}
            for ln in lines:
                latest[ln.meta.get("sid")] = ln
            lines = list(latest.values())
        info = vl.merge(event, [ln.meta for ln in lines])
        parts = [info]
        if s["voice_sass"] and (event != "readback" or s["voice_sass_readback"]):
            pool, _ = load_quotes(self._brain)
            values = {**lines[0].meta, "project": vl.spoken_project(lines[0].meta.get("project"))
                      if lines[0].meta.get("project") else None}
            values.pop("line", None)
            values.pop("sid", None)
            # A workout reminder draws from the workout pool, else (empty or gone) the reminder one.
            name = values.pop("pool", None) or event
            if not pool.get(name):
                name = event
            quip = self._picker.pick(name, pool.get(name, []), values, now)
            self._state.save()
            if quip:
                parts.append(quip)
        line = Line(event, info, lines[0].meta, lines[0].created, parts=parts)
        line.request = _request(parts, s, files)
        self._speaker.say(line)

    def _drop_pending(self) -> None:
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        self._pending.clear()

    def close(self) -> None:
        with self._lock:
            self._drop_pending()


def _tod(now: datetime) -> str:
    from .quotes import time_of_day
    return time_of_day(now)


def _parse(s) -> datetime | None:
    try:
        return datetime.fromisoformat(s) if isinstance(s, str) and s else None
    except ValueError:
        return None


def _request(parts: list[str], s: dict, files: dict) -> dict:
    return {"op": "say", "parts": parts, "blend": s["voice_blend"], "speed": s["voice_speed"],
            "volume": s["voice_volume"], "boost": s.get("voice_boost", True), "duck_to": s.get("voice_duck_to", 100),
            "model": files["model"], "voices": files["voices"]}


def worker_command(state: Path) -> list[str]:
    return [sys.executable, "-m", "atlas", "voice-worker", "--state", str(state)]


class Speaker:
    """One thread in front of the worker process: a bounded queue, one line at a time.

    on_event(type, data), from this thread: "voice.speaking" {env, frame_ms} as a line starts to
    sound (its loudness, for the ATLAS dot), "voice.quiet" {} once it has ended, however it ended."""

    def __init__(self, state: Path, *, command: Callable[[], list[str]] | None = None,
                 env: dict | None = None, now: Callable[[], datetime] = datetime.now,
                 idle_s: float = IDLE_S, reply_timeout_s: float = REPLY_TIMEOUT_S,
                 on_event: Callable[[str, dict], None] | None = None):
        self._state, self._now, self._on_event = Path(state), now, on_event
        self._command = command or (lambda: worker_command(self._state))
        self._env, self._idle_s, self._reply_timeout_s = env, idle_s, reply_timeout_s
        self._lines: deque[Line] = deque()
        self._cv = threading.Condition()
        self._proc: subprocess.Popen | None = None
        self._replies: queue.Queue = queue.Queue()
        self._write_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._closed = False
        self._stops = 0  # bumped by stop(): a line taken off the queue before it is never sent
        self.last_error: str | None = None
        self.spoken = 0
        self._thread: threading.Thread | None = None  # started on the first line

    def say(self, line: Line) -> bool:
        with self._cv:
            if self._closed:
                return False
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="voice-speaker", daemon=True)
                self._thread.start()
            if len(self._lines) >= QUEUE_MAX:
                self._lines.popleft()  # the oldest goes: the newest news matters most
            self._lines.append(line)
            self._cv.notify()
        return True

    def stop(self) -> None:
        """Empty the queue and cut the line playing now (or being synthesised, or about to be sent)."""
        with self._cv:
            self._lines.clear()
            self._stops += 1
        self._write({"op": "stop"})

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._lines.clear()
            self._cv.notify()
        self._kill()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def busy(self) -> bool:
        with self._cv:
            return bool(self._lines)

    def _run(self) -> None:
        while True:
            with self._cv:
                if not self._lines and not self._closed:
                    self._cv.wait(timeout=self._idle_s)
                if self._closed:
                    return
                if not self._lines:
                    idle = True
                    line = None
                else:
                    idle = False
                    line = self._lines.popleft()
                    gen = self._stops
            if idle:
                self._kill()  # nothing to say for IDLE_S: the model's memory goes back
                continue
            if (self._now() - line.created).total_seconds() > STALE_S:
                continue
            try:
                self._speak(line, gen)
            except Exception as e:
                self.last_error = str(e) or type(e).__name__
                log.warning("voice: %s", self.last_error)

    def _speak(self, line: Line, gen: int) -> None:
        self._ensure_worker()
        rid = str(next(self._ids))
        sent = self._write({**line.request, "id": rid}, gen)
        if sent is None:
            return  # stopped between leaving the queue and now
        if not sent:
            self._kill()
            raise RuntimeError("the voice worker stopped")
        speaking = False
        try:
            while True:
                try:
                    reply = self._replies.get(timeout=self._reply_timeout_s)
                except queue.Empty:
                    self._kill()
                    raise RuntimeError("the voice worker didn't answer in time") from None
                if reply is None:
                    self._kill()
                    raise RuntimeError("the voice worker stopped")
                if reply.get("id") != rid:
                    continue  # a late reply to a line cut short earlier
                if reply.get("event") == "playing" and "ok" not in reply:  # not the end: it starts to sound
                    env = reply.get("env")
                    speaking = True
                    self._emit("voice.speaking", {
                        "env": [int(x) for x in env if isinstance(x, (int, float))] if isinstance(env, list) else [],
                        "frame_ms": reply.get("frame_ms") if isinstance(reply.get("frame_ms"), int) else 40})
                    continue
                if reply.get("ok"):
                    self.last_error = None
                    self.spoken += 1
                else:
                    self.last_error = str(reply.get("error") or "unknown error")
                return
        finally:
            if speaking:  # ended, stopped, or the worker gone: the dot settles
                self._emit("voice.quiet", {})

    def _emit(self, type_: str, data: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(type_, data)
        except Exception:
            log.exception("voice: the %s event failed", type_)

    def _ensure_worker(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        self._state.mkdir(parents=True, exist_ok=True)
        logfile = self._state / "voice-worker.log"
        try:
            if logfile.stat().st_size > LOG_MAX:
                logfile.replace(logfile.with_name("voice-worker.log.1"))  # keep one old one
        except OSError:
            pass
        errlog = open(logfile, "a", encoding="utf-8")
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            self._proc = subprocess.Popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=errlog, env=self._env, text=True, encoding="utf-8",
                                          bufsize=1, **kwargs)
        finally:
            errlog.close()
        self._replies = queue.Queue()
        threading.Thread(target=self._read, args=(self._proc, self._replies), daemon=True).start()
        if self._closed:  # close() ran while the worker was starting
            self._kill()
            raise RuntimeError("the voice is closing")

    @staticmethod
    def _read(proc: subprocess.Popen, replies: queue.Queue) -> None:
        try:
            for raw in proc.stdout:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    replies.put(msg)
        except (OSError, ValueError):
            pass
        replies.put(None)

    def _write(self, obj: dict, gen: int | None = None) -> bool | None:
        """True when sent; None when a stop came after `gen` (nothing sent). stop() bumps the
        count before it takes this lock, so a line is either cut by it or never sent."""
        with self._write_lock:
            if gen is not None and gen != self._stops:
                return None
            proc = self._proc
            if not proc or proc.poll() is not None:
                return False
            try:
                proc.stdin.write(json.dumps(obj) + "\n")
                proc.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def _kill(self) -> None:
        with self._write_lock:
            proc, self._proc = self._proc, None
        if not proc:
            return
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        kill_tree(proc)


def kill_tree(proc: subprocess.Popen) -> None:
    """The worker and its player: a process group on POSIX, taskkill /T on Windows."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
