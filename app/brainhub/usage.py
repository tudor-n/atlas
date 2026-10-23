"""Claude plan-usage meter: stores the status line's `rate_limits` and serves a view."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)
WINDOWS = ("five_hour", "seven_day")
MAX_RESETS = 4_102_444_800  # year 2100: keeps datetime.fromtimestamp safe
# A new window starts only after the old one ends (5 h or 7 days later), so two readings whose
# resets are closer than this are the same window, even if the sources disagree by a second.
SAME_WINDOW_S = 3600


def _num(v) -> bool:
    return type(v) in (int, float)


def _clean(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    used, resets = raw.get("used_percentage"), raw.get("resets_at")
    if type(resets) is float and resets.is_integer():
        resets = int(resets)  # an integral float (1800003600.0) is still a whole epoch second
    if not _num(used) or not 0 <= used <= 100 or type(resets) is not int or not 0 < resets < MAX_RESETS:
        return None
    # Rounded: the status line and the hub's runs report the same reading (0.29 → 28.999999999999996).
    return {"used_percentage": round(float(used), 2), "resets_at": resets}


def _stale(new: dict, held: dict) -> bool:
    """Every Claude Code session posts its own last response's numbers, so readings arrive out of
    order. Usage only rises inside a window: a reading from an earlier window, or lower in the
    same one, is an older response, never news (owner feedback, 2026-10-22)."""
    if abs(new["resets_at"] - held["resets_at"]) >= SAME_WINDOW_S:
        return new["resets_at"] < held["resets_at"]
    return new["used_percentage"] < held["used_percentage"]


def rate_limits_from_event(info) -> dict | None:
    """The status line's `rate_limits` shape from the `rate_limit_info` of a `claude -p` stream's
    `rate_limit_event` (`unifiedWindows.<window>.utilization` is a 0-1 fraction; claude 2.1.309).
    The hub's own runs use the plan too, and no status line reports them. None if no window."""
    windows = info.get("unifiedWindows") if isinstance(info, dict) else None
    if not isinstance(windows, dict):
        return None
    out = {}
    for w in WINDOWS:
        win = windows.get(w)
        if isinstance(win, dict) and _num(win.get("utilization")):
            # Over the limit (overage) can report more than 1: the meter shows it as full.
            out[w] = {"used_percentage": min(round(win["utilization"] * 100, 2), 100),
                      "resets_at": win.get("resetsAt")}
    return out or None


class UsageStore:
    def __init__(self, path: Path, now: Callable[[], float] = time.time):
        self.path, self.now = path, now
        self._lock = threading.Lock()
        self._data: dict = {}
        self._updated: float | None = None  # epoch seconds, from the injected clock
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
            data = {w: c for w in WINDOWS if (c := _clean(raw.get(w)))}
            updated = raw.get("updated_at")
            stamp = datetime.fromisoformat(updated).timestamp() if isinstance(updated, str) else None
        except (OSError, ValueError) as exc:
            log.warning("usage file unreadable, starting empty: %s", exc)
            return
        self._data, self._updated = data, stamp

    def _save(self, data: dict, updated: float | None) -> None:
        out = dict(data)
        if updated is not None:
            out["updated_at"] = datetime.fromtimestamp(updated).isoformat(timespec="seconds")
        self.path.parent.mkdir(exist_ok=True)  # data/ only, never the root (it may be gone)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(out, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)  # atomic

    def update(self, payload: dict) -> bool:
        limits = payload.get("rate_limits") if isinstance(payload, dict) else None
        if not isinstance(limits, dict):
            raise ValueError("rate_limits must be an object")
        with self._lock:
            data, seen, changed = dict(self._data), False, False
            for w in WINDOWS:
                clean = _clean(limits.get(w))
                if clean is None or (w in data and _stale(clean, data[w])):
                    continue
                seen = True
                if data.get(w) != clean:
                    data[w], changed = clean, True
            updated = self.now() if seen else self._updated
            if changed:
                self._save(data, updated)  # raises before memory is touched
            self._data, self._updated = data, updated
            return changed

    def updated_at(self) -> str | None:
        if self._updated is None:
            return None
        return datetime.fromtimestamp(self._updated).isoformat(timespec="seconds")

    def view(self, warn_pct: int, fable_url: str) -> dict:
        with self._lock:
            now = self.now()
            out: dict = {}
            for w in WINDOWS:
                cur = self._data.get(w)
                if cur is None:
                    out[w] = None
                    continue
                passed = now >= cur["resets_at"]
                used = None if passed else cur["used_percentage"]
                out[w] = {"used": used, "last_used": cur["used_percentage"],
                          "resets_at": datetime.fromtimestamp(cur["resets_at"]).strftime("%Y-%m-%dT%H:%M"),
                          "reset_passed": passed, "warn": used is not None and used >= warn_pct}
            out.update(updated_at=self.updated_at(),
                       age_s=None if self._updated is None else int(now - self._updated),
                       warn_pct=warn_pct, fable_url=fable_url)
            return out
