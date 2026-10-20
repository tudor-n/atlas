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
