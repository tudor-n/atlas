import pytest

from brainhub.config import load_settings, save_settings


def test_defaults_when_no_file(paths):
    s = load_settings(paths)
    assert s["port"] == 8090 and s["recall_model"] == "haiku" and s["default_lead_days"] == 2


def test_save_merges_and_persists(paths):
    save_settings(paths, {"nudge_days": 5})
    assert load_settings(paths)["nudge_days"] == 5
    assert load_settings(paths)["filing_model"] == "sonnet"


def test_save_rejects_unknown_key(paths):
    with pytest.raises(ValueError):
        save_settings(paths, {"nope": 1})


def test_save_rejects_wrong_type(paths):
    with pytest.raises(ValueError):
        save_settings(paths, {"nudge_days": "three"})
