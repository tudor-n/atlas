import json
from datetime import datetime

import pytest

from brainhub.usage import UsageStore
from tests.fakes import Clock

T = 1_800_000_000
URL = "https://example.test/usage"
PAYLOAD = {"rate_limits": {"five_hour": {"used_percentage": 62.4, "resets_at": T + 3600},
                           "seven_day": {"used_percentage": 81, "resets_at": T + 86400}}}


def make(tmp_path, t=T):
    clock = Clock(t)
    return UsageStore(tmp_path / "usage.json", clock), clock


def test_update_and_view(tmp_path):
    store, _ = make(tmp_path)
    assert store.update(PAYLOAD) is True
    v = store.view(80, URL)
    five, seven = v["five_hour"], v["seven_day"]
    assert five["used"] == 62.4 and five["warn"] is False and five["reset_passed"] is False
    assert five["resets_at"] == datetime.fromtimestamp(T + 3600).strftime("%Y-%m-%dT%H:%M")
    assert seven["warn"] is True
    assert v["warn_pct"] == 80 and v["fable_url"] == URL
    assert store.update(PAYLOAD) is False


def test_missing_window_keeps_last(tmp_path):
    store, _ = make(tmp_path)
    store.update(PAYLOAD)
    assert store.update({"rate_limits": {"seven_day": {"used_percentage": 90, "resets_at": T + 86400}}})
    v = store.view(80, URL)
    assert v["five_hour"]["used"] == 62.4 and v["seven_day"]["used"] == 90


def test_reset_passed_hides_used(tmp_path):
    store, clock = make(tmp_path)
    store.update(PAYLOAD)
    clock.t = T + 3601
    five = store.view(80, URL)["five_hour"]
    assert five["used"] is None and five["last_used"] == 62.4
    assert five["reset_passed"] is True and five["warn"] is False


@pytest.mark.parametrize("bad", [
    {"used_percentage": "62", "resets_at": T}, {"used_percentage": -1, "resets_at": T},
    {"used_percentage": 100.5, "resets_at": T}, {"used_percentage": True, "resets_at": T},
    {"used_percentage": 5, "resets_at": "x"}, {"used_percentage": 5, "resets_at": 1.5},
])
def test_bad_values_rejected(tmp_path, bad):
    store, _ = make(tmp_path)
    store.update({"rate_limits": {"five_hour": bad,
                                  "seven_day": {"used_percentage": 10, "resets_at": T + 5}}})
    v = store.view(80, URL)
    assert v["five_hour"] is None and v["seven_day"]["used"] == 10


def test_bad_rate_limits_and_boundary(tmp_path):
    store, _ = make(tmp_path)
    with pytest.raises(ValueError):
        store.update({"rate_limits": "x"})
    with pytest.raises(ValueError):
        store.update({})
    assert store.update({"rate_limits": {"five_hour": {"used_percentage": 100.0, "resets_at": T + 5}}})
    assert store.view(80, URL)["five_hour"]["used"] == 100.0


def test_persists_and_corrupt_file_starts_empty(tmp_path):
    store, _ = make(tmp_path)
    store.update(PAYLOAD)
    again, _ = make(tmp_path)
    assert again.view(80, URL)["five_hour"]["used"] == 62.4
    (tmp_path / "usage.json").write_text("{nope", encoding="utf-8")
    broken, _ = make(tmp_path)
    assert broken.view(80, URL)["five_hour"] is None


def test_age(tmp_path):
    store, clock = make(tmp_path)
    assert store.updated_at() is None
    assert store.view(80, URL)["age_s"] is None and store.view(80, URL)["updated_at"] is None
    store.update(PAYLOAD)
    clock.t = T + 42
    assert store.view(80, URL)["age_s"] == 42
    assert store.updated_at() is not None


@pytest.mark.parametrize("bad", [10**15, -5, 0, 4_102_444_800])
def test_out_of_range_resets_at_ignored(tmp_path, bad):
    store, _ = make(tmp_path)
    store.update({"rate_limits": {"five_hour": {"used_percentage": 5, "resets_at": bad},
                                  "seven_day": {"used_percentage": 10, "resets_at": T + 5}}})
    v = store.view(80, URL)
    assert v["five_hour"] is None and v["seven_day"]["used"] == 10


def test_file_with_bad_resets_at_loads(tmp_path):
    (tmp_path / "usage.json").write_text(json.dumps({
        "five_hour": {"used_percentage": 5.0, "resets_at": 10**15},
        "seven_day": {"used_percentage": 7.0, "resets_at": T + 5}}), encoding="utf-8")
    store, _ = make(tmp_path)
    v = store.view(80, URL)
    assert v["five_hour"] is None and v["seven_day"]["used"] == 7.0


def test_failed_save_leaves_memory_unchanged(tmp_path, monkeypatch):
    store, _ = make(tmp_path)
    monkeypatch.setattr(store, "_save", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        store.update(PAYLOAD)
    assert store.view(80, URL)["five_hour"] is None and store.updated_at() is None


def test_integral_float_resets_at_accepted(tmp_path):
    """A JSON encoder may send 1800003600.0: an integral float is a valid epoch (final review #8)."""
    store, _ = make(tmp_path)
    assert store.update({"rate_limits": {"five_hour": {"used_percentage": 5, "resets_at": float(T + 3600)},
                                         "seven_day": {"used_percentage": 7, "resets_at": float("inf")}}})
    v = store.view(80, URL)
    assert v["five_hour"]["resets_at"] == datetime.fromtimestamp(T + 3600).strftime("%Y-%m-%dT%H:%M")
    assert v["seven_day"] is None
    saved = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert saved["five_hour"]["resets_at"] == T + 3600 and type(saved["five_hour"]["resets_at"]) is int


# -- several sessions post at once (owner feedback 2026-10-22: the meter lagged ~2 points) ------
def _five(used, resets=T + 3600):
    return {"rate_limits": {"five_hour": {"used_percentage": used, "resets_at": resets}}}


def test_lower_reading_in_the_same_window_is_stale(tmp_path):
    """Each Claude Code session posts its own last response's numbers: an idle session's older,
    lower reading must not pull the meter back (usage only rises inside a window)."""
    store, clock = make(tmp_path)
    assert store.update(_five(7))
    stamp = store.updated_at()
    clock.t = T + 60
    assert store.update(_five(5)) is False
    assert store.view(80, URL)["five_hour"]["used"] == 7
    assert store.updated_at() == stamp  # a stale reading is not news
    assert store.update(_five(8)) is True
    assert store.view(80, URL)["five_hour"]["used"] == 8
    assert json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))["five_hour"]["used_percentage"] == 8


def test_window_order_decides_over_the_number(tmp_path):
    store, _ = make(tmp_path)
    store.update(_five(40))
    assert store.update(_five(90, resets=T - 60)) is False  # an earlier, finished window
    assert store.view(80, URL)["five_hour"]["used"] == 40
    assert store.update(_five(1, resets=T + 3600 + 18000)) is True  # a new window starts low
    five = store.view(80, URL)["five_hour"]
    assert five["used"] == 1
    assert five["resets_at"] == datetime.fromtimestamp(T + 3600 + 18000).strftime("%Y-%m-%dT%H:%M")


def test_windows_merge_independently(tmp_path):
    store, _ = make(tmp_path)
    store.update(PAYLOAD)
    assert store.update({"rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": T + 3600},
                                         "seven_day": {"used_percentage": 82, "resets_at": T + 86400}}})
    v = store.view(80, URL)
    assert v["five_hour"]["used"] == 62.4 and v["seven_day"]["used"] == 82


def test_rate_limits_from_run_event():
    """The shape `claude -p --output-format stream-json` reports (seen with 2.1.309)."""
    from brainhub.usage import rate_limits_from_event
    info = {"status": "allowed", "resetsAt": T + 3600, "rateLimitType": "five_hour",
            "unifiedWindows": {"five_hour": {"utilization": 0.07, "resetsAt": T + 3600},
                               "seven_day": {"utilization": 0.09, "resetsAt": T + 86400}}}
    assert rate_limits_from_event(info) == {
        "five_hour": {"used_percentage": 7.0, "resets_at": T + 3600},
        "seven_day": {"used_percentage": 9.0, "resets_at": T + 86400}}
    assert rate_limits_from_event({"unifiedWindows": {"seven_day": {"utilization": 1, "resetsAt": 5}}}) == {
        "seven_day": {"used_percentage": 100, "resets_at": 5}}


@pytest.mark.parametrize("info", [None, [], "x", {}, {"unifiedWindows": []},
                                  {"unifiedWindows": {"five_hour": "x"}},
                                  {"unifiedWindows": {"five_hour": {"utilization": "0.5", "resetsAt": T}}},
                                  {"unifiedWindows": {"five_hour": {"utilization": True, "resetsAt": T}}},
                                  {"unifiedWindows": {"other": {"utilization": 0.5, "resetsAt": T}}}])
def test_rate_limits_from_odd_events(info):
    from brainhub.usage import rate_limits_from_event
    assert rate_limits_from_event(info) is None


def test_one_window_even_if_resets_at_drifts(tmp_path):
    """The status line and the hub's runs might not agree to the second on a window's reset. A new
    window can only start after the old one ends, so resets under an hour apart are one window."""
    store, _ = make(tmp_path)
    store.update(_five(30, resets=T + 3600))
    assert store.update(_five(29, resets=T + 3601)) is False
    assert store.update(_five(29, resets=T + 3599)) is False
    assert store.view(80, URL)["five_hour"]["used"] == 30
    assert store.update(_five(31, resets=T + 3599)) is True
    assert store.view(80, URL)["five_hour"]["used"] == 31
    assert store.update(_five(2, resets=T + 3600 + 3600)) is True  # an hour later: a new window


def test_readings_are_rounded_so_equal_sources_agree(tmp_path):
    store, clock = make(tmp_path)
    store.update(_five(29.0))
    stamp = store.updated_at()
    clock.t = T + 60
    assert store.update(_five(28.999999999999996)) is False  # 0.29 * 100 from some encoder
    assert store.updated_at() != stamp  # the same reading: it confirms the value


def test_stale_reading_after_reload(tmp_path):
    store, _ = make(tmp_path)
    store.update(_five(40))
    again, _ = make(tmp_path)
    assert again.update(_five(35)) is False and again.view(80, URL)["five_hour"]["used"] == 40


def test_run_event_over_the_limit_is_clamped():
    from brainhub.usage import rate_limits_from_event
    info = {"unifiedWindows": {"five_hour": {"utilization": 1.2, "resetsAt": T + 3600}}}
    assert rate_limits_from_event(info) == {"five_hour": {"used_percentage": 100, "resets_at": T + 3600}}
