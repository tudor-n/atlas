"""The floating desktop widget's single summary (design §7.2) and the robot-arm reachability probe."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Callable
from urllib.parse import urlparse

from .today import compute_today


class ArmProbe:
    """TCP connect to the arm server's host:port, 1 s timeout, result cached per url: `ttl` seconds
    when reachable, `neg_ttl` when not. The arm is normally offline, and a refused connect costs
    about 1 s on Windows, so the negative result is kept longer."""

    def __init__(self, now: Callable[[], float] = time.monotonic, ttl: float = 15.0, neg_ttl: float = 60.0):
        self._now, self._ttl, self._neg_ttl = now, ttl, neg_ttl
        self._cache: dict[str, tuple[float, bool]] = {}

    async def reachable(self, url: str) -> bool:
        hit = self._cache.get(url)
        if hit is not None and self._now() - hit[0] < (self._ttl if hit[1] else self._neg_ttl):
            return hit[1]
        ok = await self._connect(url)
        self._cache[url] = (self._now(), ok)
        return ok

    @staticmethod
    async def _connect(url: str) -> bool:
        try:
            u = urlparse(url)
            host = u.hostname
            port = u.port or (443 if u.scheme == "https" else 80)
            if not host:
                return False
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1.0)
        except (OSError, ValueError, asyncio.TimeoutError):
            return False
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True


def synopsis(today: dict, review: dict, waiting: list[dict], usage: dict, agents: dict, requests: int = 0) -> str:
    parts: list[str] = []
    k = len(today.get("overdue", []))
    n = k + len(today.get("due_today", []))
    if n > 0:
        parts.append(f"{n} due today" + (f" ({k} overdue)" if k > 0 else ""))
    if len(waiting) == 1:
        w = waiting[0]
        parts.append(f"1 waiting on {w.get('name') or w.get('person') or 'someone'}")
    elif waiting:
        parts.append(f"{len(waiting)} waiting")
    rv = review.get("entries", review.get("proposals", 0) + review.get("failed", 0))
    if rv > 0:
        parts.append(f"{rv} to review")
    if requests:  # an agent asked for workbench measurements (phase 3)
        parts.append("1 request" if requests == 1 else f"{requests} requests")
    five = usage.get("five_hour")
    if five and five.get("used") is not None:
        parts.append(f"5h {five['used']:.0f}% (resets {str(five.get('resets_at', ''))[-5:]})")
    running, waiting_a = agents.get("running", 0), agents.get("waiting", 0)
    if running:
        parts.append("1 agent running" if running == 1 else f"{running} agents running")
    if waiting_a:
        parts.append("1 agent waiting" if waiting_a == 1 else f"{waiting_a} agents waiting")
    return " · ".join(parts) or "All clear"


def build_widget(app_state: dict) -> dict:
    items, settings, bmap = app_state["items"], app_state["settings"], app_state.get("map")
    now: datetime = app_state["now"]
    usage, rows = app_state["usage"], app_state["agents"]
    review = {"proposals": app_state["proposals"], "failed": app_state["failed"],
              "entries": app_state.get("entries", app_state["proposals"] + app_state["failed"])}
    today = compute_today(items, [None] * review["proposals"], [None] * review["failed"],
                          now, settings["nudge_days"])
    names = {p["slug"]: p.get("name") or p["slug"] for p in (bmap.people if bmap else [])}

    waiting = []
    for w in today["waiting"]:
        it = w["item"]
        slug = it.get("person")
        waiting.append({"person": slug, "name": names.get(slug, slug) if slug else None,
                        "text": it["text"], "since": it["created"], "nudge": w["nudge"]})

    # A closed session lingers in the list (shown as "ended") but never counts as running or
    # waiting, so it can't raise attention.
    def shown(r: dict) -> str:
        return "ended" if (r.get("ended") or r.get("ended_at")) else r["status"]

    counts = {s: sum(1 for r in rows if shown(r) == s) for s in ("running", "waiting", "finished", "idle")}
    agents = {**counts, "rows": [{**{k: r.get(k) for k in ("project_name", "part", "where", "next_time")},
                                  "status": shown(r)} for r in rows[:6]]}

    open_items = [i for i in items if i.status == "open"]
    deadlines = sorted((i for i in open_items if i.due), key=lambda i: i.due)[:6]
    projects = []
    for p in (bmap.projects if bmap else []):
        mine = [i for i in open_items if i.project == p["slug"]]
        projects.append({"slug": p["slug"], "name": p.get("name") or p["slug"], "open": len(mine),
                         "next_time": sum(1 for i in mine if i.kind == "next_time")})

    requests = int(app_state.get("requests", 0))
    attention = (review["proposals"] + review["failed"] > 0 or counts["waiting"] > 0 or requests > 0
                 or any((usage.get(w) or {}).get("warn") for w in ("five_hour", "seven_day")))
    top = (today["overdue"] + today["due_today"])[:5]
    return {
        "synopsis": synopsis(today, review, waiting, usage, agents, requests),
        "requests": requests,
        "attention": bool(attention),
        "tiles": list(settings["widget_tiles"]),
        "today": {"overdue": len(today["overdue"]), "due_today": len(today["due_today"]),
                  "top": [{"text": d["text"], "due": d["due"]} for d in top]},
        "usage": usage,
        "agents": agents,
        "people": {"waiting": waiting, "to_read": len(today["to_read"])},
        "deadlines": [{"text": i.text, "due": i.due, "project": i.project} for i in deadlines],
        "review": review,
        "projects": projects,
        "arm": {"url": settings["arm_url"], "reachable": app_state["arm_reachable"]},
    }
