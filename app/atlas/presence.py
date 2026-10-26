"""Is the owner at the PC? (chatter spec §5.) No microphone, ever (owner): presence comes from what
the hub can see.

- The lock screen: logind's `LockedHint` for this session, or org.freedesktop.ScreenSaver's
  `GetActive` (Linux). KDE on Wayland doesn't answer `GetSessionIdleTime`, so there is no idle time
  to read there.
- The owner's own activity, as the hub sees it: a prompt typed to a Claude Code session, an answer to
  an agent that was waiting, a write from the PC app, and the app's and widget's input ping
  (`POST /api/presence`).
- On Windows, the real idle time (`GetLastInputInfo`).

States: "away" (locked, or idle for `away_after`), "present" (active within PRESENT_S), "idle" in
between. The first activity after an away stretch calls `on_return(away_since)`: the welcome back.
Nothing here may fail the hub: a probe that errors counts as "don't know"."""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

PRESENT_S = 600  # activity within this: present
AWAY_AFTER = timedelta(minutes=20)  # no activity for this (or the screen locked): away
PROBE_TIMEOUT_S = 2


def _run(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def locked_linux() -> bool | None:
    """True when this session's screen is locked, None when it can't tell."""
    sid = os.environ.get("XDG_SESSION_ID")
    if sid and shutil.which("loginctl"):
        out = _run(["loginctl", "show-session", sid, "-p", "LockedHint", "--value"])
        if out is not None and out.strip() in ("yes", "no"):
            return out.strip() == "yes"
    if shutil.which("gdbus"):
        out = _run(["gdbus", "call", "--session", "--dest", "org.freedesktop.ScreenSaver", "--object-path",
                    "/org/freedesktop/ScreenSaver", "--method", "org.freedesktop.ScreenSaver.GetActive"])
        if out is not None and out.strip() in ("(true,)", "(false,)"):
            return out.strip() == "(true,)"
    return None


def idle_windows() -> float | None:
    """Seconds since the last keyboard or mouse input (Windows), None when it can't tell."""
    if sys.platform != "win32":
        return None
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):  # type: ignore[attr-defined]
            return None
        tick = ctypes.windll.kernel32.GetTickCount  # type: ignore[attr-defined]
        tick.restype = ctypes.c_uint32  # unsigned, and both wrap at 49.7 days: subtract modulo 2**32
        return ((tick() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except (OSError, AttributeError, ValueError):
        return None


def default_probe() -> dict:
    """{"locked": bool | None, "idle_s": float | None} for this OS."""
    if sys.platform == "win32":
        return {"locked": None, "idle_s": idle_windows()}
    return {"locked": locked_linux(), "idle_s": None}


class Presence:
    def __init__(self, *, now: Callable[[], datetime] = datetime.now, probe: Callable[[], dict] = default_probe,
                 away_after: Callable[[], timedelta] = lambda: AWAY_AFTER,
                 on_return: Callable[[datetime], None] | None = None):
        self._now, self._probe, self._away_after, self.on_return = now, probe, away_after, on_return
        self._lock = threading.Lock()
        self._last_seen: datetime | None = None  # the owner's last activity the hub saw
        self._locked: bool | None = None
        self._idle_s: float | None = None
        self._away_since: datetime | None = None
        self.last_source: str | None = None

    def seen(self, source: str = "activity") -> None:
        """The owner did something. After an away stretch this is the welcome back."""
        now = self._now()
        with self._lock:
            self._last_seen, self.last_source = now, source
            back = self._away_since if self._away_since is not None and not self._locked else None
            if back is not None:
                self._away_since = None
        if back is not None and self.on_return is not None:
            try:
                self.on_return(back)
            except Exception:
                log.exception("presence: the welcome back failed")

    def poll(self) -> None:
        """Every tick: read the lock screen (and Windows' idle time), and notice the owner leaving."""
        try:
            p = self._probe() or {}
        except Exception:
            p = {}
        now = self._now()
        with self._lock:
            was_locked = self._locked
            self._locked, self._idle_s = p.get("locked"), p.get("idle_s")
            if self._idle_s is not None and self._idle_s < 60:
                self._last_seen = max(self._last_seen or now, now - timedelta(seconds=self._idle_s))
            if self._away_since is None and self._is_away(now):
                self._away_since = now if self._locked else (self._last_seen or now)
            unlocked = was_locked and self._locked is False
        if unlocked:
            self.seen("unlock")

    def _is_away(self, now: datetime) -> bool:
        if self._locked:
            return True
        if self._last_seen is None:
            return False  # nothing seen since the hub started: don't know, so not away
        return now - self._last_seen >= self._away_after()

    def state(self) -> str:
        now = self._now()
        with self._lock:
            if self._locked or self._away_since is not None or self._is_away(now):
                return "away"
            if self._last_seen is not None and (now - self._last_seen).total_seconds() < PRESENT_S:
                return "present"
            return "idle"

    def away_since(self) -> datetime | None:
        with self._lock:
            return self._away_since

    def view(self) -> dict:
        with self._lock:
            seen = self._last_seen
        return {"state": self.state(), "last_seen": seen.isoformat(timespec="seconds") if seen else None,
                "locked": self._locked, "source": self.last_source}
