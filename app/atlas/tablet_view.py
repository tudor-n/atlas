"""Phase 3: what the workbench tablet shows (`GET /api/tablet/state`), as ready-made strings.

The tablet runs ES5 on old WebKit at 1024×600, so dates, labels and URLs are formatted here and
its page only places text. Pure: tested without a hub."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import quote, urlsplit, urlunsplit

STATUS_LABEL = {"idle": "ready", "running": "running", "waiting": "waiting for you", "finished": "finished"}
NOTES_SHOWN = 10
ROWS_MAX = 30
NOTE_CHARS = 600
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def when(value: str | None, now: datetime) -> str:
    """'14:00' today, '10-21' for another day, '10-21 14:00' for a time on another day."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    has_time = "T" in value
    if dt.date() == now.date():
        return f"{dt:%H:%M}" if has_time else "today"
    return f"{dt:%m-%d %H:%M}" if has_time else f"{dt:%m-%d}"


def _group(title: str, rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return {"title": title, "rows": rows[:ROWS_MAX], "more": max(0, len(rows) - ROWS_MAX)}


def today_groups(today: dict, now: datetime) -> list[dict]:
    """compute_today's sections the tablet glances at (spec §3.3 TODAY): overdue, due today,
    waiting on others, coming up. Read-only (owner): no ids, nothing to tick."""
    def dated(d: dict) -> str:
        return d.get("due") or d.get("remind_at") or ""
    groups = [
        _group("OVERDUE", [{"text": d["text"], "meta": f"due {when(dated(d), now)}", "attention": True}
                           for d in today.get("overdue", [])]),
        _group("DUE TODAY", [{"text": d["text"], "meta": when(dated(d), now), "attention": False}
                             for d in today.get("due_today", [])]),
        _group("WAITING ON OTHERS", [
            {"text": w["item"]["text"],
             "meta": " · ".join(x for x in (w["item"].get("person") or "", "no news" if w["nudge"] else "") if x),
             "attention": bool(w["nudge"])} for w in today.get("waiting", [])]),
        _group("COMING UP", [{"text": d["text"], "meta": when(dated(d), now), "attention": False}
                             for d in today.get("upcoming", [])]),
    ]
    return [g for g in groups if g]


def agent_rows(rows: list[dict]) -> list[dict]:
    return [{"project": r.get("project_name") or r.get("project") or "", "part": r.get("part") or "",
             "status": r.get("status") or "", "label": STATUS_LABEL.get(r.get("status"), r.get("status") or "")}
            for r in rows if not r.get("ended")]


def usage_rows(view: dict, now: datetime) -> list[dict]:
    out = []
    for key, label in (("five_hour", "5 HOURS"), ("seven_day", "WEEK")):
        w = view.get(key)
        if not w or w.get("used") is None:
            out.append({"label": label, "pct": None, "text": "no data yet", "warn": False})
            continue
        out.append({"label": label, "pct": int(round(w["used"])), "text": f"resets {when(w['resets_at'], now)}",
                    "warn": bool(w.get("warn"))})
    return out


def request_rows(requests: list[dict], hidden: set[str], now: datetime) -> list[dict]:
    return [{"id": r["id"], "title": r["title"], "project": r.get("project_name") or r.get("project") or "",
             "when": when(r["created"], now), "items": list(r["items"])}
            for r in requests if r["status"] == "open" and r["id"] not in hidden]


def note_rows(entries: list, hidden: set[str], answered: dict[str, str], now: datetime) -> list[dict]:
    """The newest tablet notes and answers, not ignored (answered: entry id -> request title)."""
    out = []
    for e in entries:
        if e.id in hidden:
            continue
        text = e.text if len(e.text) <= NOTE_CHARS else e.text[:NOTE_CHARS - 1] + "…"
        out.append({"id": e.id, "when": when(e.ts.isoformat(timespec="minutes"), now), "text": text,
                    "answer_to": answered.get(e.id)})
        if len(out) == NOTES_SHOWN:
            break
    return out


def home_url(host: str) -> str:
    """The tablet page as the tablet reaches it (host = the request's Host: '192.168.0.200:8090')."""
    return f"http://{host}/tablet"


def robot_url(arm_url: str, host: str) -> str:
    """The arm UI for the tablet's ROBOT button, carrying `?home=` back here (design §8). An arm_url
    on loopback (the PC's view, the default) is reached at the tablet's host name instead."""
    parts = urlsplit(arm_url)
    hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    netloc = parts.netloc
    if (parts.hostname or "") in _LOOPBACK:
        netloc = f"{hostname}:{parts.port}" if parts.port else hostname
    query = (parts.query + "&" if parts.query else "") + "home=" + quote(home_url(host), safe="")
    return urlunsplit((parts.scheme or "http", netloc, parts.path or "/", query, ""))


def build_tablet_state(*, version: int, device: dict | None, today: dict, requests: list[dict],
                       hidden_requests: set[str], notes: list, hidden_notes: set[str],
                       answered: dict[str, str], agents: list[dict], usage: dict, arm_reachable: bool,
                       arm_url: str, host: str, now: datetime, units: list[str]) -> dict:
    groups = today_groups(today, now)
    reqs = request_rows(requests, hidden_requests, now)
    due_today = sum(len(g["rows"]) + g["more"] for g in groups if g["title"] in ("OVERDUE", "DUE TODAY"))
    agents_rows = agent_rows(agents)
    return {
        "version": version,
        "device": device["name"] if device else None,
        "clock": f"{now:%H:%M}",
        "home": {"arm": "arm server reachable" if arm_reachable else "arm server offline",
                 "arm_ok": arm_reachable,
                 "journal": " · ".join(x for x in (
                     f"{len(reqs)} request{'s' if len(reqs) != 1 else ''}" if reqs else "",
                     f"{due_today} due" if due_today else "",
                     f"{sum(1 for a in agents_rows if a['status'] == 'waiting')} agent waiting"
                     if any(a["status"] == "waiting" for a in agents_rows) else "") if x) or "nothing waiting"},
        "today": groups,
        "requests": reqs,
        "notes": note_rows(notes, hidden_notes, answered, now),
        "agents": agents_rows,
        "usage": usage_rows(usage, now),
        "robot_url": robot_url(arm_url, host),
        "units": list(units),
    }
