from datetime import datetime

from brainhub.models import Item
from brainhub.today import compute_today

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
