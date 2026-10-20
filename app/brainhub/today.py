"""Compute the Today view's sections from items and review counts."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from .models import Item


def _dt(value: str) -> datetime:
    """Local ISO date or datetime; a date-only value means 00:00."""
    return datetime.fromisoformat(value)


def compute_today(items: list[Item], proposals: list, failed_entry_ids: list[str],
                  now: datetime, nudge_days: int) -> dict:
    today = now.date()
    out: dict = {"overdue": [], "due_today": [], "upcoming": [], "next_time": {}, "waiting": [],
                 "to_read": [], "open_tasks": [], "review": {}}
    for i in items:
        if i.status == "snoozed":
            if i.snooze_until and _dt(i.snooze_until) > now:
                continue
            status = "open"  # the snooze has run out
        else:
            status = i.status
        d = asdict(i)
        if status == "to_read":
            out["to_read"].append(d)
            continue
        if status != "open":
            continue
        if i.due:
            due_day = _dt(i.due).date()
            if due_day < today:
                out["overdue"].append(d)
            elif due_day == today:
                out["due_today"].append(d)
            if i.remind_at and _dt(i.remind_at) <= now < _dt(i.due):
                out["upcoming"].append(d)
        if i.kind == "next_time":
            out["next_time"].setdefault(i.project or i.area, []).append(d)
        elif i.kind == "waiting":
            nudge = _dt(i.created) < now - timedelta(days=nudge_days)
            out["waiting"].append({"item": d, "nudge": nudge})
        elif i.kind == "task" and not i.due:
            out["open_tasks"].append(d)
    out["review"] = {"proposals": len(proposals), "failed": len(failed_entry_ids)}
    return out
