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
