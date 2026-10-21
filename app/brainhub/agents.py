"""Agent tracker: turns Claude Code hook events into "agent session" rows for the Agents panel.

Event payload fields (cwd, title, message, branch...) come from outside and are untrusted
data: they are stored and shown with textContent only, never interpreted."""
from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from .brainmap import BrainMap, load_map
from .config import Paths
from .store import Store

WHERE_BY_ENTRYPOINT = {"cli": "terminal", "claude-vscode": "IDE", "claude-desktop": "desktop app"}
WHERE_CHOICES = ("terminal", "IDE", "desktop app", "unknown")
STATUS_ORDER = ("waiting", "running", "finished", "idle")

NOTE_MAX = 300
PART_MAX = 80
POST_TOOL_WRITE_S = 30
_WAIT_NOTIFICATIONS = ("permission_prompt", "elicitation_dialog", "agent_needs_input")
_KEEP_STATUS_SOURCES = ("resume", "clear", "compact", "fork")
_HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")


def norm_path(p: str) -> str:
    n = os.path.normcase(os.path.normpath(p))
    return n.rstrip("\\/") or n


def is_within(path: str, folder: str) -> bool:
    a, f = norm_path(path), norm_path(folder)
    return a == f or a.startswith(f + os.sep)


def find_git_dir(cwd: str) -> tuple[Path, Path] | None:
    """(worktree_top, git_dir), walking up from cwd. A `.git` file (worktree) is followed."""
    if not cwd:
        return None
    start = Path(cwd)
    for d in (start, *start.parents):
        g = d / ".git"
        try:
            if g.is_dir():
                return d, g
            if g.is_file():
                m = re.match(r"\s*gitdir:\s*(.+?)\s*$", g.read_text(encoding="utf-8", errors="replace"))
                if m:
                    target = Path(m.group(1))
                    return d, target if target.is_absolute() else (d / target).resolve()
        except OSError:
            continue
    return None


def read_branch(cwd: str) -> str | None:
    found = find_git_dir(cwd)
    if not found:
        return None
    try:
        head = (found[1] / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head[4:].strip()
        prefix = "refs/heads/"
        return ref[len(prefix):] if ref.startswith(prefix) and len(ref) > len(prefix) else None
    return "detached" if _HEX40.match(head) else None


def simplify_branch(b: str | None) -> str | None:
    return b.rsplit("/", 1)[-1] if b else None


def resolve_project(cwd: str, m: BrainMap | None) -> tuple[str, str, bool]:
    best, best_len = None, -1
    if m and cwd:
        for p in m.projects:
            for folder in p.get("folders", []):
                if folder and is_within(cwd, folder) and len(norm_path(folder)) > best_len:
                    best, best_len = p, len(norm_path(folder))
    if best:
        return best["slug"], best["name"], True
    found = find_git_dir(cwd)
    name = (found[0].name if found and found[0].name else "") or Path(cwd).name or cwd
    return name, name, False


def _s(v) -> str | None:
    return v if isinstance(v, str) and v else None


class AgentTracker:
    def __init__(self, store: Store, paths: Paths, settings: Callable[[], dict],
                 now: Callable[[], datetime] = datetime.now):
        self._store, self._paths, self._settings, self._now = store, paths, settings, now
        self._lock = threading.RLock()

    def _stamp(self) -> str:
        return self._now().replace(microsecond=0).isoformat()

    def _ignored(self, event: dict) -> bool:
        ep = event.get("entrypoint")
        if isinstance(ep, str) and ep.startswith("sdk-"):
            return True
        cwd = event.get("cwd")
        if isinstance(cwd, str) and cwd:
            return any(is_within(cwd, str(p)) for p in (self._paths.data, self._paths.brain))
        return False

    def handle(self, event: dict) -> bool:
        sid, name = event.get("session_id"), event.get("hook_event_name")
        if not isinstance(sid, str) or not sid or not isinstance(name, str) or not name:
            raise ValueError("session_id and hook_event_name are required strings")
        if self._ignored(event):
            return False
        with self._lock:
            return self._apply(sid, name, event)

    def _target_status(self, name: str, event: dict, row: dict | None) -> tuple[str | None, str | None]:
        """(status, note) the event asks for; status None means "no change"."""
        if name == "UserPromptSubmit" or name == "PostToolUse":
            return "running", None
        if name == "Stop":
            return "finished", None
        if name == "StopFailure":
            return "waiting", _s(event.get("error_type")) or _s(event.get("error"))
        if name == "Notification":
            kind = event.get("notification_type")
            if kind in _WAIT_NOTIFICATIONS:
                return "waiting", _s(event.get("message"))
            if kind == "idle_prompt" and row and row["status"] == "running":
                return "waiting", _s(event.get("message"))
            return None, None
        if name == "SessionStart":
            if row and event.get("source") in _KEEP_STATUS_SOURCES:
                return None, None
            return "idle", None
        return None, None

    def _apply(self, sid: str, name: str, event: dict) -> bool:
        st = self._store
        row = st.get_agent(sid)
        stamp = self._stamp()
        if name == "SessionEnd":
            if not row:
                return False
            row.update(ended_at=stamp, last_event_at=stamp)
            st.upsert_agent(row)
            return True
        status, note = self._target_status(name, event, row)
        if row is None and status is None:
            if name == "Notification":
                return False  # an ignorable notification never creates a row
            status = "idle"
        if row is not None and name == "PostToolUse" and row["status"] == "running":
            last = datetime.fromisoformat(row["last_event_at"])
            if self._now() - last < timedelta(seconds=POST_TOOL_WRITE_S):
                return False
        before = None
        if row is None:
            cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
            row = {"session_id": sid, "cwd": cwd, "project": "", "project_name": "", "mapped": False,
                   "branch": None, "title": None, "part_override": None, "entrypoint": None,
                   "where_override": None, "status": status, "note": None, "started_at": stamp,
                   "status_since": stamp, "last_event_at": stamp, "ended_at": None}
            self._refresh(row, event, load_map(self._paths.brain))
        else:
            before = self._visible(row)
            if name == "SessionStart":
                row["ended_at"] = None
                self._refresh(row, event, load_map(self._paths.brain))
            elif name == "UserPromptSubmit":
                self._refresh(row, event, load_map(self._paths.brain))
        if status is not None:
            if status != row["status"]:
                row["status"] = status
                row["status_since"] = stamp
            row["note"] = note[:NOTE_MAX] if note else None
        row["last_event_at"] = stamp
        st.upsert_agent(row)
        return before is None or before != self._visible(row)

    @staticmethod
    def _visible(row: dict) -> tuple:
        return tuple(row[k] for k in ("status", "note", "title", "branch", "project", "project_name",
                                      "entrypoint", "ended_at"))

    def _refresh(self, row: dict, event: dict, m: BrainMap | None) -> None:
        cwd = event.get("cwd")
        if isinstance(cwd, str) and cwd:
            row["cwd"] = cwd
        ep = _s(event.get("entrypoint"))
        if ep:
            row["entrypoint"] = ep
        title = _s(event.get("session_title"))
        if title:
            row["title"] = title
        row["branch"] = read_branch(row["cwd"])
        row["project"], row["project_name"], row["mapped"] = resolve_project(row["cwd"], m)

    # reads
    def _public(self, row: dict, next_time: dict[str, int]) -> dict:
        out = dict(row)
        out["part"] = (row["part_override"] or row["title"] or simplify_branch(row["branch"]) or "—")
        out["where"] = row["where_override"] or WHERE_BY_ENTRYPOINT.get(row["entrypoint"] or "", "unknown")
        # Closed (SessionEnd) but still lingering: its stored status is whatever it was at the end,
        # e.g. "running" for a session closed mid-turn. The UI shows it as ended and counts skip it.
        out["ended"] = bool(row["ended_at"])
        if row["mapped"]:
            if row["project"] not in next_time:
                next_time[row["project"]] = len(self._store.list_items(
                    status="open", kind="next_time", project=row["project"]))
            out["next_time"] = next_time[row["project"]]
        else:
            out["next_time"] = 0
        return out

    def rows(self) -> list[dict]:
        with self._lock:
            self.prune()
            cache: dict[str, int] = {}
            out = [self._public(r, cache) for r in self._store.list_agents()]
        out.sort(key=lambda r: r["last_event_at"], reverse=True)
        out.sort(key=lambda r: STATUS_ORDER.index(r["status"]) if r["status"] in STATUS_ORDER
                 else len(STATUS_ORDER))
        out.sort(key=lambda r: r["ended"])  # ended sessions last (stable: the order above holds)
        return out

    def prune(self) -> bool:
        s = self._settings()
        linger = timedelta(seconds=int(s.get("agents_end_linger_s", 60)))
        stale = timedelta(hours=int(s.get("agents_stale_h", 12)))
        now = self._now()
        with self._lock:
            dead = [r["session_id"] for r in self._store.list_agents()
                    if (r["ended_at"] and now - datetime.fromisoformat(r["ended_at"]) > linger)
                    or now - datetime.fromisoformat(r["last_event_at"]) > stale]
            if dead:
                self._store.delete_agents(dead)
        return bool(dead)

    def edit(self, session_id: str, patch: dict) -> dict:
        with self._lock:
            row = self._store.get_agent(session_id)
            if row is None:
                raise KeyError(session_id)
            bad = set(patch) - {"part", "where"}
            if bad:
                raise ValueError(f"unknown keys: {sorted(bad)}")
            if "part" in patch:
                v = patch["part"]
                if v is not None and not isinstance(v, str):
                    raise ValueError("part must be a string or null")
                v = (v or "").strip()
                if len(v) > PART_MAX:
                    raise ValueError(f"part is longer than {PART_MAX} characters")
                row["part_override"] = v or None
            if "where" in patch:
                v = patch["where"]
                if v is not None and v not in WHERE_CHOICES:
                    raise ValueError(f"where must be one of {WHERE_CHOICES}")
                row["where_override"] = v
            self._store.upsert_agent(row)
            return self._public(row, {})

    def remove(self, session_id: str) -> None:
        self._store.delete_agents([session_id])

    def last_event_at(self) -> str | None:
        times = [r["last_event_at"] for r in self._store.list_agents()]
        return max(times) if times else None
