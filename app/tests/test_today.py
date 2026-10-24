from datetime import datetime

from atlas.models import Item
from atlas.today import compute_today

NOW = datetime(2026, 10, 20, 12, 0)


def mk(**o) -> Item:
    d = dict(id="i-1", kind="task", text="t", area="general", project=None, person=None, due=None,
             remind_at=None, status="open", snooze_until=None, source_entry="e-1",
             created="2026-10-20T09:00:00", updated="2026-10-20T09:00:00")
    d.update(o)
    return Item(**d)


def test_sections():
    items = [mk(kind="deadline", due="2026-10-19"), mk(kind="deadline", due="2026-10-20"),
             mk(kind="deadline", due="2026-10-25", remind_at="2026-10-20"), mk(kind="next_time", project="robotarm"),
             mk(kind="waiting", created="2026-10-10T09:00:00"), mk(kind="waiting", status="to_read"),
             mk(kind="task"), mk(kind="task", status="snoozed", snooze_until="2026-10-21T09:00")]
    t = compute_today(items, [object()], ["e-9"], NOW, nudge_days=3)
    assert len(t["overdue"]) == 1 and len(t["due_today"]) == 1 and len(t["upcoming"]) == 1
    assert list(t["next_time"]) == ["robotarm"] and t["waiting"][0]["nudge"] is True
    assert len(t["to_read"]) == 1 and len(t["open_tasks"]) == 1
    assert t["review"] == {"proposals": 1, "failed": 1}


def test_items_are_dicts_and_expired_snooze_resurfaces():
    t = compute_today([mk(status="snoozed", snooze_until="2026-10-20T08:00")], [], [], NOW, 3)
    assert t["open_tasks"][0]["status"] == "snoozed" and isinstance(t["open_tasks"][0], dict)


def test_remind_at_date_only_means_midnight_and_recent_waiting_no_nudge():
    items = [mk(kind="reminder", due="2026-10-22", remind_at="2026-10-21"),
             mk(kind="waiting", created="2026-10-19T09:00:00")]
    t = compute_today(items, [], [], NOW, 3)
    assert t["upcoming"] == [] and t["waiting"][0]["nudge"] is False


def test_due_today_not_also_upcoming():
    t = compute_today([mk(kind="deadline", due="2026-10-20T15:00", remind_at="2026-10-18")], [], [], NOW, 3)
    assert len(t["due_today"]) == 1 and t["upcoming"] == []


import pytest  # noqa: E402

KINDS = ["task", "deadline", "reminder", "waiting", "next_time"]
DATE_SHAPES = {
    "due past": dict(due="2026-10-15"),
    "due today": dict(due="2026-10-20T18:00"),
    "due future": dict(due="2026-11-30"),
    "due future, reminder passed": dict(due="2026-10-25", remind_at="2026-10-19"),
    "due future, reminder future": dict(due="2026-10-30", remind_at="2026-10-28"),
    "remind only, past": dict(remind_at="2026-10-18T09:00"),
    "remind only, today": dict(remind_at="2026-10-20"),
    "remind only, future": dict(remind_at="2026-10-27"),
    "neither": dict(),
    "unreadable due": dict(due="next friday"),
    "unreadable remind": dict(remind_at="soonish"),
    "expired snooze": dict(status="snoozed", snooze_until="2026-10-19T09:00"),
    "unreadable snooze": dict(status="snoozed", snooze_until="whenever"),
    "to read": dict(status="to_read"),
}


def _placements(t: dict, item_id: str) -> list[str]:
    where = []
    for key in ("overdue", "due_today", "upcoming", "to_read", "open_tasks", "other"):
        where += [key for d in t[key] if d["id"] == item_id]
    where += ["next_time" for group in t["next_time"].values() for d in group if d["id"] == item_id]
    where += ["waiting" for w in t["waiting"] if w["item"]["id"] == item_id]
    return where


@pytest.mark.parametrize("shape", list(DATE_SHAPES))
@pytest.mark.parametrize("kind", KINDS)
def test_every_open_item_lands_in_today(kind, shape):
    item = mk(id="i-x", kind=kind, **DATE_SHAPES[shape])
    where = _placements(compute_today([item], [], [], NOW, 3), "i-x")
    if kind in ("waiting", "next_time") and DATE_SHAPES[shape].get("status") != "to_read":
        assert len(where) >= 1 and kind in where, where  # may also sit in a date section
    else:
        assert len(where) == 1, where


def test_remind_only_counts_its_date_and_future_snooze_hides():
    t = compute_today([mk(id="a", kind="reminder", remind_at="2026-10-20T15:00"),
                       mk(id="b", kind="deadline", remind_at="2026-10-01"),
                       mk(id="c", kind="deadline", status="snoozed", snooze_until="2026-10-21T09:00"),
                       mk(id="d", kind="reminder")], [], [], NOW, 3)
    assert [d["id"] for d in t["due_today"]] == ["a"] and [d["id"] for d in t["overdue"]] == ["b"]
    assert [d["id"] for d in t["other"]] == ["d"]
    assert all(d["id"] != "c" for k in ("overdue", "due_today", "upcoming", "other", "open_tasks") for d in t[k])
