"""Compute the Today view's sections from items and review counts.

Nothing open may vanish: an item is placed by its date (`due`, or `remind_at` when it has no
due date), then by kind, and anything still unplaced lands in the catch-all `other`."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from .models import Item


def _dt(value: str) -> datetime:
    """Local ISO date or datetime; a date-only value means 00:00."""
    return datetime.fromisoformat(value)


def _safe_dt(value: str | None) -> datetime | None:
    """None for a missing or unreadable date (the item then counts as undated)."""
    if not value:
        return None
    try:
        return _dt(value)
    except (TypeError, ValueError):
        return None


def snooze_active(item: Item, now: datetime) -> bool:
    """A snoozed item hides until its snooze_until; an unreadable one has run out."""
    if item.status != "snoozed":
        return False
    until = _safe_dt(item.snooze_until)
    return until is not None and until > now


def compute_today(items: list[Item], proposals: list, failed_entry_ids: list[str],
                  now: datetime, nudge_days: int) -> dict:
    today = now.date()
    out: dict = {"overdue": [], "due_today": [], "upcoming": [], "next_time": {}, "waiting": [],
                 "to_read": [], "open_tasks": [], "other": [], "review": {}}
    for i in items:
        if i.status == "snoozed":
            if snooze_active(i, now):
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
        placed = False
        due, remind = _safe_dt(i.due), _safe_dt(i.remind_at)
        when = due or remind  # an item with only a reminder counts the reminder's date
        if when is not None:
            if when.date() < today:
                out["overdue"].append(d)
                placed = True
            elif when.date() == today:
                out["due_today"].append(d)
                placed = True
            elif due is not None and remind is not None and remind <= now < due:
                out["upcoming"].append(d)
                placed = True
        # waiting and next_time always show in their own sections, dated or not
        if i.kind == "next_time":
            out["next_time"].setdefault(i.project or i.area, []).append(d)
            placed = True
        elif i.kind == "waiting":
            created = _safe_dt(i.created)
            nudge = created is not None and created < now - timedelta(days=nudge_days)
            out["waiting"].append({"item": d, "nudge": nudge})
            placed = True
        elif i.kind == "task" and not placed:
            out["open_tasks"].append(d)
            placed = True
        if not placed:
            out["other"].append(d)
    out["review"] = {"proposals": len(proposals), "failed": len(failed_entry_ids)}
    return out
