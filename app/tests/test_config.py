import json

import pytest

from atlas.config import load_settings, save_settings


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


def test_claude_path_must_be_bare(paths):
    import json
    assert save_settings(paths, {"claude_path": "claude"})["claude_path"] == "claude"
    for bad in ("C:\\x\\claude.exe", "/usr/bin/claude", "bin/claude"):
        with pytest.raises(ValueError, match="must be a command name"):
            save_settings(paths, {"claude_path": bad})
    # A path stored by an older hub (it is shared by both OSes) falls back to the bare default.
    paths.settings_file.write_text(json.dumps({"claude_path": "C:\\Users\\o\\.local\\bin\\claude.exe",
                                               "nudge_days": 5}), encoding="utf-8")
    problems = []
    s = load_settings(paths, problems)
    assert s["claude_path"] == "claude" and s["nudge_days"] == 5
    assert len(problems) == 1 and "claude_path" in problems[0] and "command name" in problems[0]


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
    import atlas.config as cfg
    save_settings(paths, {"nudge_days": 5})
    before = paths.settings_file.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(cfg.os, "replace", boom)
    with pytest.raises(OSError):
        save_settings(paths, {"nudge_days": 7})
    assert paths.settings_file.read_text(encoding="utf-8") == before


RANGE_CASES = [("port", 1024, 65535), ("filing_timeout_s", 10, 3600), ("recall_timeout_s", 10, 3600),
               ("deeper_timeout_s", 10, 3600), ("recall_top_n", 1, 50), ("default_lead_days", 0, 60),
               ("nudge_days", 1, 60), ("backup_debounce_s", 0, 86400)]


@pytest.mark.parametrize("key,lo,hi", RANGE_CASES)
def test_numeric_settings_are_range_checked(paths, key, lo, hi):
    assert save_settings(paths, {key: lo})[key] == lo
    assert save_settings(paths, {key: hi})[key] == hi
    for bad in (lo - 1, hi + 1):
        with pytest.raises(ValueError, match="between"):
            save_settings(paths, {key: bad})
    assert load_settings(paths)[key] == hi


def test_out_of_range_stored_value_falls_back_to_default(paths):
    import json
    paths.settings_file.write_text(json.dumps({"port": 80}), encoding="utf-8")
    problems = []
    assert load_settings(paths, problems)["port"] == 8090 and "port" in problems[0]


def test_phase2_defaults(paths):
    s = load_settings(paths)
    assert s["usage_warn_pct"] == 80
    assert s["fable_usage_url"] == "https://claude.ai/settings/usage"
    assert s["agents_end_linger_s"] == 60
    assert s["agents_stale_h"] == 12
    assert s["arm_url"] == "http://127.0.0.1:8080"
    assert s["widget_tiles"] == ["today", "usage", "agents", "people", "deadlines", "review", "projects", "arm"]


def test_widget_tiles_validation(paths):
    assert save_settings(paths, {"widget_tiles": ["usage", "today"]})["widget_tiles"] == ["usage", "today"]
    for bad in ([], ["today", "today"], ["bogus"], "today", [1]):
        with pytest.raises(ValueError):
            save_settings(paths, {"widget_tiles": bad})


def test_url_settings_validation(paths):
    assert save_settings(paths, {"arm_url": "http://192.168.1.5:8080"})["arm_url"] == "http://192.168.1.5:8080"
    for bad in ("ftp://x", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            save_settings(paths, {"arm_url": bad})
    with pytest.raises(ValueError):
        save_settings(paths, {"fable_usage_url": "http://claude.ai"})
    for bad in (0, 101):
        with pytest.raises(ValueError):
            save_settings(paths, {"usage_warn_pct": bad})


# -- phase 3: the workbench tablet ----------------------------------------------------------------
def test_tablet_defaults(paths):
    s = load_settings(paths)
    assert s["tablet_lan"] is False and s["tablet_address"] == ""
    assert "mm" in s["tablet_units"] and "°" in s["tablet_units"]


@pytest.mark.parametrize("addr", ["192.168.0.200", "10.0.0.5", "172.16.3.4", "172.31.255.254", ""])
def test_tablet_address_accepts_private_ipv4(paths, addr):
    assert save_settings(paths, {"tablet_address": addr})["tablet_address"] == addr


@pytest.mark.parametrize("addr", ["8.8.8.8", "172.32.0.1", "127.0.0.1", "0.0.0.0", "192.168.0.255.1",
                                  "192.168.0", "localhost", "192.168.0.200 ", "::1", "fe80::1", 5, None,
                                  "192.168.000.200", "192.168.0.256"])
def test_tablet_address_rejects_anything_else(paths, addr):
    with pytest.raises(ValueError):
        save_settings(paths, {"tablet_address": addr})


def test_tablet_lan_is_a_bool(paths):
    assert save_settings(paths, {"tablet_lan": True})["tablet_lan"] is True
    with pytest.raises(ValueError):
        save_settings(paths, {"tablet_lan": 1})


@pytest.mark.parametrize("bad", [[], "mm", ["mm", "mm"], ["mm", ""], ["x" * 7], ["mm", 3], ["m m"], ["a"] * 21])
def test_tablet_units_validation(paths, bad):
    assert save_settings(paths, {"tablet_units": ["mm", "°", "Ω"]})["tablet_units"] == ["mm", "°", "Ω"]
    with pytest.raises(ValueError):
        save_settings(paths, {"tablet_units": bad})


# -- phase 4: voice and sass ----------------------------------------------------------------------
def test_voice_defaults(paths):
    s = load_settings(paths)
    assert s["voice_enabled"] is True and s["voice_sass"] is True and s["voice_sass_readback"] is False
    assert s["voice_events"] == {"agent_waiting": True, "agent_finished": True, "reminder": True,
                                 "readback": True, "reply": True}
    assert s["voice_blend"] == {"bm_george": 0.7, "bm_fable": 0.3}  # Atlas's voice (owner)
    assert s["voice_speed"] == 0.95 and s["voice_volume"] == 80 and s["voice_model"] == "int8"
    assert s["voice_finish_min_s"] == 120
    assert s["voice_boost"] is True and s["voice_duck_to"] == 50  # slightly dulled, -6 dB (V-L4)


def test_a_saved_duck_level_is_kept(paths):
    save_settings(paths, {"voice_duck_to": 30})
    assert load_settings(paths)["voice_duck_to"] == 30


def test_voice_events_validation(paths):
    ok = {"agent_waiting": False, "agent_finished": True, "reminder": True, "readback": False, "reply": False}
    assert save_settings(paths, {"voice_events": ok})["voice_events"] == ok
    for bad in [{}, {**ok, "extra": True}, {**ok, "reminder": 1}, {"agent_waiting": True}, [], None]:
        with pytest.raises(ValueError):
            save_settings(paths, {"voice_events": bad})


def test_voice_events_saved_by_another_build_keep_their_switches(paths):
    """The owner's file has the four switches from before `reply` (VI5): they stay, reply starts on.
    A file from a later build with a switch this one doesn't know keeps the known ones too."""
    paths.settings_file.parent.mkdir(exist_ok=True)
    old = {"agent_waiting": False, "agent_finished": True, "reminder": False, "readback": True}
    paths.settings_file.write_text(json.dumps({"voice_events": old}), encoding="utf-8")
    problems: list = []
    assert load_settings(paths, problems)["voice_events"] == {**old, "reply": True}
    newer = {**old, "reply": False, "something_later": True}
    paths.settings_file.write_text(json.dumps({"voice_events": newer}), encoding="utf-8")
    assert load_settings(paths, problems)["voice_events"] == {**old, "reply": False}
    assert problems == []
    paths.settings_file.write_text(json.dumps({"voice_events": {**old, "reply": "yes"}}), encoding="utf-8")
    assert load_settings(paths, problems)["voice_events"]["agent_waiting"] is True  # a bad value: refused whole
    assert problems
    with pytest.raises(ValueError):  # a save still names every switch, and only those
        save_settings(paths, {"voice_events": {**old, "reply": True, "something_later": True}})


@pytest.mark.parametrize("blend", [{"bm_george": 1}, {"af_heart": 0.5, "bm_george": 0.25, "bf_emma": 0.25},
                                   {"bm_george": 0.7, "bm_fable": 0.3}])
def test_voice_blend_accepts(paths, blend):
    assert save_settings(paths, {"voice_blend": blend})["voice_blend"] == blend


@pytest.mark.parametrize("blend", [{}, {"bm_george": 0}, {"bm_george": -1}, {"bm_george": True},
                                   {"af_heart": 1, "bm_lewis": 1, "bf_emma": 1, "bm_george": 1}, {"BM_GEORGE": 1},
                                   {"../x": 1}, {"zz_nobody": 1},
                                   {"bm_george": "1"}, ["bm_george"], {"bm_george": float("nan")},
                                   {"bm_george": float("inf")}])
def test_voice_blend_rejects(paths, blend):
    with pytest.raises(ValueError):
        save_settings(paths, {"voice_blend": blend})


def test_voice_speed_takes_ints_and_floats_in_range(paths):
    assert save_settings(paths, {"voice_speed": 1})["voice_speed"] == 1
    assert save_settings(paths, {"voice_speed": 1.25})["voice_speed"] == 1.25
    for bad in [0.4, 2.1, True, "1", None, float("nan")]:
        with pytest.raises(ValueError):
            save_settings(paths, {"voice_speed": bad})


def test_voice_volume_model_and_threshold(paths):
    assert save_settings(paths, {"voice_volume": 0, "voice_model": "fp32",
                                 "voice_finish_min_s": 0})["voice_model"] == "fp32"
    for key, bad in [("voice_volume", 101), ("voice_volume", -1), ("voice_model", "int4"),
                     ("voice_finish_min_s", 3601), ("voice_enabled", 1), ("voice_sass", "yes")]:
        with pytest.raises(ValueError):
            save_settings(paths, {key: bad})
