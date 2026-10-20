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


@pytest.mark.parametrize("remote", ["-oProxyCommand=x", "--upload-pack=x", "ext::sh -c x", "file:///x",
                                    "https://a/b::c", "has space", "-a@b:c", ""])
def test_backup_remote_rejects_unsafe(paths, remote):
    with pytest.raises(ValueError):
        save_settings(paths, {"backup_remote": remote})


@pytest.mark.parametrize("remote", [None, "https://github.com/u/r.git", "ssh://git@github.com/u/r.git",
                                    "git@github.com:u/r.git"])
def test_backup_remote_accepts_urls(paths, remote):
    assert save_settings(paths, {"backup_remote": remote})["backup_remote"] == remote


@pytest.mark.parametrize("key", ["claude_path", "filing_model", "recall_model", "deeper_model"])
def test_command_and_model_must_not_be_empty(paths, key):
    with pytest.raises(ValueError):
        save_settings(paths, {key: "  "})


def test_corrupt_file_falls_back_to_defaults_and_is_not_rewritten(paths):
    paths.settings_file.write_text("{oops", encoding="utf-8")
    problems = []
    assert load_settings(paths, problems)["port"] == 8090
    assert problems and "settings.json" in problems[0]
    assert paths.settings_file.read_text(encoding="utf-8") == "{oops"
    assert save_settings(paths, {"nudge_days": 6})["nudge_days"] == 6  # a save writes a fresh file
    assert load_settings(paths)["nudge_days"] == 6


def test_bad_stored_value_falls_back_for_that_key_only(paths):
    import json
    paths.settings_file.write_text(json.dumps({"nudge_days": "x", "recall_model": "opus"}), encoding="utf-8")
    problems = []
    s = load_settings(paths, problems)
    assert s["nudge_days"] == 3 and s["recall_model"] == "opus" and len(problems) == 1


def test_save_is_atomic(paths, monkeypatch):
    import brainhub.config as cfg
    save_settings(paths, {"nudge_days": 5})
    before = paths.settings_file.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(cfg.os, "replace", boom)
    with pytest.raises(OSError):
        save_settings(paths, {"nudge_days": 7})
    assert paths.settings_file.read_text(encoding="utf-8") == before
