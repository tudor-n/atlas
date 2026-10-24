import json
import os
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas import voice as v
from atlas.config import DEFAULT_SETTINGS
from atlas.quotes import ensure_quotes

APP = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 23, 15, 0, 0)  # a Friday afternoon, outside the default quiet hours


class Now:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Sink:
    """Stands in for Speaker: records what would be spoken."""

    def __init__(self):
        self.lines, self.stops, self.last_error = [], 0, None

    def say(self, line):
        self.lines.append(line)
        return True

    def stop(self):
        self.stops += 1

    @property
    def said(self):
        return [ln.parts for ln in self.lines]


def item(id_, text="Pay rent", status="open", due=None, remind_at=None, snooze_until=None):
    return SimpleNamespace(id=id_, text=text, status=status, due=due, remind_at=remind_at, snooze_until=snooze_until)


@pytest.fixture
def env(tmp_path):
    brain = tmp_path / "brain"
    brain.mkdir()
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    settings["voice_sass"] = False
    box = SimpleNamespace(settings=settings, items=[], now=Now(), sink=Sink(), installed=True, brain=brain,
                          state=tmp_path / "state" / "voice.json")

    def make(merge_s=0):
        return v.Announcer(settings=lambda: box.settings, brain=brain, state_path=box.state,
                           files=lambda s: {"model": f"/m/{s['voice_model']}.onnx", "voices": "/m/v.bin"} if box.installed else None,
                           items=lambda: box.items, speaker=box.sink, now=box.now, merge_s=merge_s,
                           rng=random.Random(7))
    box.make = make
    box.a = make()
    return box


def _tr(sid, before, after, at, since=None, project="Robot arm", kind=None):
    return {"session_id": sid, "before": before, "after": after, "at": at.isoformat(), "since": (since or at).isoformat(),
            "project_name": project, "kind": kind}


# -- time helpers ----------------------------------------------------------------------------------
@pytest.mark.parametrize("hhmm,quiet", [("23:00", True), ("02:00", True), ("07:59", True), ("08:00", False),
                                        ("15:00", False), ("22:59", False)])
def test_quiet_hours_wrap_past_midnight(hhmm, quiet):
    h, m = map(int, hhmm.split(":"))
    assert v.in_quiet_hours(T0.replace(hour=h, minute=m), ["23:00", "08:00"]) is quiet


def test_quiet_hours_same_day_and_switched_off():
    assert v.in_quiet_hours(T0.replace(hour=13), ["12:00", "14:00"])
    assert not v.in_quiet_hours(T0.replace(hour=15), ["12:00", "14:00"])
    assert not v.in_quiet_hours(T0, ["09:00", "09:00"])


def test_morning_is_the_end_of_quiet_hours():
    assert v.next_morning(T0, ["23:00", "08:00"]) == datetime(2026, 10, 24, 8, 0)
    assert v.next_morning(T0.replace(hour=6), ["23:00", "07:30"]) == datetime(2026, 10, 23, 7, 30)
    assert v.next_morning(T0, ["10:00", "10:00"]) == datetime(2026, 10, 24, 8, 0)


def test_reminder_times():
    q = ["23:00", "08:00"]
    assert v.reminder_times(item("a", due="2026-10-25", remind_at="2026-10-23"), q) == [
        ("2026-10-23", datetime(2026, 10, 23, 8, 0)), ("2026-10-25", datetime(2026, 10, 25, 8, 0))]
    assert v.reminder_times(item("b", due="2026-10-25T14:00", remind_at="2026-10-25T14:00"), q) == [
        ("2026-10-25T14:00", datetime(2026, 10, 25, 14, 0))]
    assert v.reminder_times(item("c", status="snoozed", due="2026-10-01", snooze_until="2026-10-24T09:30"), q) == [
        ("2026-10-24T09:30", datetime(2026, 10, 24, 9, 30))]
    assert v.reminder_times(item("d", status="done", due="2026-10-25"), q) == []
    assert v.reminder_times(item("e", due="garbage"), q) == []


# -- agents ------------------------------------------------------------------------------------------
def test_waiting_speaks_once_per_stretch(env):
    t = T0
    env.a.agent_transition(_tr("s1", "running", "waiting", t, kind="permission_prompt"))
    env.a.agent_transition(_tr("s1", "running", "waiting", t, kind="permission_prompt"))  # same stretch
    env.a.agent_transition(_tr("s1", "running", "waiting", t + timedelta(minutes=1)))
    assert env.sink.said == [["Robot arm needs you. It's asking for permission."], ["Robot arm needs you."]]


def test_finished_speaks_only_after_the_threshold(env):
    env.a.agent_transition(_tr("s1", "idle", "running", T0))
    env.a.agent_transition(_tr("s1", "running", "finished", T0 + timedelta(seconds=119)))
    assert env.sink.said == []
    env.a.agent_transition(_tr("s1", "finished", "running", T0 + timedelta(minutes=5)))
    env.a.agent_transition(_tr("s1", "running", "finished", T0 + timedelta(minutes=47)))
    assert env.sink.said == [["Robot arm has finished. It took forty-two minutes."]]


def test_a_permission_prompt_mid_turn_does_not_restart_the_clock(env):
    env.a.agent_transition(_tr("s1", "idle", "running", T0))
    env.a.agent_transition(_tr("s1", "running", "waiting", T0 + timedelta(minutes=1)))
    env.a.agent_transition(_tr("s1", "waiting", "running", T0 + timedelta(minutes=2)))
    env.a.agent_transition(_tr("s1", "running", "finished", T0 + timedelta(minutes=3)))
    assert env.sink.said[-1] == ["Robot arm has finished. It took three minutes."]


def test_finished_without_a_seen_start_uses_since(env):
    env.a.agent_transition(_tr("s9", "running", "finished", T0 + timedelta(minutes=10), since=T0))
    assert env.sink.said == [["Robot arm has finished. It took ten minutes."]]


def test_finished_from_idle_is_silent_and_the_threshold_is_a_setting(env):
    env.settings["voice_finish_min_s"] = 0
    env.a.agent_transition(_tr("s1", "idle", "finished", T0))
    assert env.sink.said == []
    env.a.agent_transition(_tr("s2", "idle", "running", T0))
    env.a.agent_transition(_tr("s2", "running", "finished", T0 + timedelta(seconds=5)))
    assert env.sink.said == [["Robot arm has finished. It took less than a minute."]]


def test_agent_project_names_are_spoken_names_only(env):
    env.a.agent_transition(_tr("s1", "running", "waiting", T0, project="RobotArmControl"))
    env.a.agent_transition(_tr("s2", "running", "waiting", T0, project=""))
    assert env.sink.said == [["Robot Arm Control needs you."], ["An agent needs you."]]


# -- gates -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change", ["off", "not_installed", "event_off", "quiet"])
def test_gates_silence_a_line(env, change):
    if change == "off":
        env.settings["voice_enabled"] = False
    elif change == "not_installed":
        env.installed = False
    elif change == "event_off":
        env.settings["voice_events"] = {**env.settings["voice_events"], "agent_waiting": False}
    else:
        env.now.t = T0.replace(hour=23, minute=30)
    env.a.agent_transition(_tr("s1", "running", "waiting", env.now.t))
    assert env.sink.said == []


def test_a_line_silenced_by_quiet_hours_is_not_saved_for_later(env):
    env.now.t = T0.replace(hour=23, minute=30)
    env.a.agent_transition(_tr("s1", "running", "waiting", env.now.t))
    env.now.t = T0 + timedelta(days=1)
    env.a.tick()
    env.a.agent_transition(_tr("s1", "running", "waiting", T0.replace(hour=23, minute=30)))  # a repeat
    assert env.sink.said == []


def test_pause_resume_and_stop(env):
    until = env.a.pause(60)
    assert until == T0 + timedelta(hours=1) and env.sink.stops == 1
    env.a.agent_transition(_tr("s1", "running", "waiting", T0))
    assert env.sink.said == [] and env.a.paused_until() == until
    # the pause is per-OS state and survives a restart
    again = env.make()
    assert again.paused_until() == until
    env.a.resume()
    env.a.agent_transition(_tr("s1", "running", "waiting", T0 + timedelta(seconds=1)))
    assert len(env.sink.said) == 1
    env.now.t = T0 + timedelta(hours=2)
    assert env.a.pause(until_morning=True) == datetime(2026, 10, 24, 8, 0)


def test_a_pause_that_ran_out_lets_lines_through(env):
    env.a.pause(1)
    env.now.t = T0 + timedelta(minutes=2)
    env.a.agent_transition(_tr("s1", "running", "waiting", env.now.t))
    assert len(env.sink.said) == 1 and env.a.paused_until() is None


# -- reminders -----------------------------------------------------------------------------------------
def test_reminders_speak_once_when_their_time_passes(env):
    env.items = [item("r1", "Submit the lab report", due="2026-10-23T15:01")]
    env.a.tick()  # the first tick ever only records the time
    env.now.t = T0 + timedelta(seconds=30)
    env.a.tick()
    assert env.sink.said == []
    env.now.t = T0 + timedelta(seconds=90)
    env.a.tick()
    assert env.sink.said == [["A reminder. Submit the lab report. It's due today at 3 oh 1 pm."]]
    env.now.t = T0 + timedelta(seconds=120)
    env.a.tick()
    assert len(env.sink.said) == 1


def test_a_date_only_reminder_speaks_at_the_end_of_quiet_hours(env):
    env.now.t = datetime(2026, 10, 24, 7, 59, 30)
    env.items = [item("r1", "Call mum", remind_at="2026-10-24", due="2026-10-26")]
    env.a.tick()
    env.now.t = datetime(2026, 10, 24, 8, 0, 0)
    env.a.tick()
    assert env.sink.said == [["A reminder. Call mum. It's due on Monday."]]


def test_restart_does_not_repeat_and_startup_catch_up_is_one_line(env):
    env.items = [item("a", "One", due="2026-10-23T15:10"), item("b", "Two", due="2026-10-23T15:20")]
    env.a.tick()
    env.now.t = T0 + timedelta(minutes=30)  # the hub was off meanwhile
    later = env.make()
    later.tick()
    assert env.sink.said == [["You have two reminders from while I was off."]]
    env.now.t = T0 + timedelta(minutes=31)
    later.tick()
    again = env.make()  # another restart
    env.now.t = T0 + timedelta(minutes=32)
    again.tick()
    assert len(env.sink.said) == 1


def test_one_missed_reminder_is_spoken_as_itself(env):
    env.items = [item("a", "One", due="2026-10-23T15:10")]
    env.a.tick()
    env.now.t = T0 + timedelta(minutes=30)
    env.make().tick()
    assert env.sink.said == [["A reminder. One. It's due today at 3 10 pm."]]


def test_more_than_a_day_missed_counts_as_none(env):
    env.items = [item("a", "One", due="2026-10-24T10:00")]
    env.a.tick()
    env.now.t = T0 + timedelta(days=2)
    env.make().tick()
    assert env.sink.said == []


def test_reminder_off_or_quiet_silences_the_catch_up(env):
    env.items = [item("a", "One", due="2026-10-23T15:10"), item("b", "Two", due="2026-10-23T15:20")]
    env.a.tick()
    env.settings["voice_events"] = {**env.settings["voice_events"], "reminder": False}
    env.now.t = T0 + timedelta(minutes=30)
    env.make().tick()
    assert env.sink.said == []


# -- readbacks, merge, sass -------------------------------------------------------------------------------
def test_readbacks(env):
    env.a.readback_note("buy m3 screws")
    env.a.readback_answer({0: "412 mm"})
    assert env.sink.said == [["Note saved. buy m3 screws."], ["412 millimetres. Saved."]]


def test_lines_of_one_kind_merge(env):
    a = env.make(merge_s=60)  # the timer won't fire in the test; flush by hand
    a.agent_transition(_tr("s1", "running", "waiting", T0))
    a.agent_transition(_tr("s2", "running", "waiting", T0, project="ATLAS"))
    assert env.sink.said == []
    a.flush("agent_waiting")
    assert env.sink.said == [["Two agents need you. Robot arm and Atlas."]]
    a.close()


def test_stop_drops_lines_waiting_to_merge(env):
    a = env.make(merge_s=60)
    a.agent_transition(_tr("s1", "running", "waiting", T0))
    a.stop()
    a.flush("agent_waiting")
    assert env.sink.said == [] and env.sink.stops == 1


def test_the_merge_timer_fires(env):
    a = env.make(merge_s=0.05)
    a.agent_transition(_tr("s1", "running", "waiting", T0))
    deadline = time.monotonic() + 5
    while not env.sink.said and time.monotonic() < deadline:
        time.sleep(0.02)
    assert env.sink.said == [["Robot arm needs you."]]


def test_sass_adds_a_quip_after_the_info(env):
    ensure_quotes(env.brain)
    env.settings["voice_sass"] = True
    env.a.agent_transition(_tr("s1", "running", "waiting", T0))
    info, quip = env.sink.said[0]
    assert info == "Robot arm needs you." and quip
    env.now.t = T0 + timedelta(minutes=5)
    env.a.readback_note("x")
    assert env.sink.said[1] == ["Note saved. x."]  # no sass on readbacks by default
    env.settings["voice_sass_readback"] = True
    env.now.t = T0 + timedelta(minutes=10)
    env.a.readback_note("y")
    assert len(env.sink.said[2]) == 2


def test_quips_fill_the_project_and_keep_their_place(env):
    (env.brain / "_voice").mkdir()
    (env.brain / "_voice" / "quotes.md").write_text("## agent_waiting\nGo help {project}.\n", encoding="utf-8")
    env.settings["voice_sass"] = True
    env.a.agent_transition(_tr("s1", "running", "waiting", T0, project="robot-arm"))
    assert env.sink.said == [["robot arm needs you.", "Go help robot arm."]]
    assert json.loads(env.state.read_text(encoding="utf-8"))["quotes"]["pools"]["agent_waiting"]["pos"] == 1


def test_the_request_carries_the_voice_settings(env):
    env.settings.update(voice_blend={"af_heart": 1}, voice_speed=1.1, voice_volume=40, voice_model="fp32")
    env.a.readback_note("x")
    req = env.sink.lines[0].request
    assert req == {"op": "say", "parts": ["Note saved. x."], "blend": {"af_heart": 1}, "speed": 1.1, "volume": 40,
                   "model": "/m/fp32.onnx", "voices": "/m/v.bin"}


def test_sample_ignores_quiet_pause_and_off_but_needs_the_files(env):
    env.settings["voice_enabled"] = False
    env.a.pause(60)
    env.now.t = T0.replace(hour=23, minute=30)
    assert env.a.sample({"voice_speed": 1.3}) is True
    assert env.sink.lines[-1].parts[0] == "Good night. This is how I'll sound." and env.sink.lines[-1].request["speed"] == 1.3
    env.installed = False
    assert env.a.sample() is False


def test_view(env):
    ensure_quotes(env.brain)
    view = env.a.view()
    assert view == {"installed": True, "model": "int8", "enabled": True, "paused_until": None, "quiet_now": False,
                    "last_error": None, "quote_problems": []}
    (env.brain / "_voice" / "quotes.md").write_text("## nope\n", encoding="utf-8")
    assert env.a.view()["quote_problems"] == ["line 1: unknown pool 'nope' (known: agent_finished, agent_waiting, "
                                              "reminder, readback, catch_up)"]


def test_a_broken_state_file_is_ignored(env):
    env.state.parent.mkdir(parents=True)
    env.state.write_text("[not a dict", encoding="utf-8")
    a = env.make()
    a.readback_note("x")
    assert env.sink.said == [["Note saved. x."]]


# -- the Speaker, with the real worker process in fake mode ------------------------------------------------
def _speaker(tmp_path, **kw):
    env = {**os.environ, "ATLAS_VOICE_TRACE": "1", "ATLAS_VOICE_FAKE": str(tmp_path / "said.jsonl"), "PYTHONPATH": str(APP),
           "ATLAS_VOICE_FAKE_DELAY": kw.pop("delay", "0")}
    return v.Speaker(tmp_path / "state", env=env, **kw)


def _line(text, created=None):
    ln = v.Line("readback", text, {}, created or datetime.now(), parts=[text])
    ln.request = {"op": "say", "parts": [text], "blend": {"bm_george": 1}, "speed": 1, "volume": 80,
                  "model": "m", "voices": "v"}
    return ln


def _wait(cond, secs=15):
    deadline = time.monotonic() + secs
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.05)
    return cond()


def _log(tmp_path):
    """The worker's stderr log, for a failure message."""
    f = tmp_path / "state" / "voice-worker.log"
    return f.read_text(encoding="utf-8", errors="replace")[-4000:] if f.exists() else "(no voice-worker.log)"


def _said(tmp_path):
    f = tmp_path / "said.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def test_speaker_speaks_lines_in_order_and_kills_the_idle_worker(tmp_path):
    sp = _speaker(tmp_path, idle_s=1.0)
    try:
        sp.say(_line("one"))
        sp.say(_line("two"))
        assert _wait(lambda: sp.spoken == 2), _log(tmp_path)
        assert [s["parts"] for s in _said(tmp_path)] == [["one"], ["two"]]
        pid = _said(tmp_path)[0]["pid"]
        assert _wait(lambda: sp._proc is None and not _alive(pid), 10)  # idle: the worker is gone
        sp.say(_line("three"))  # and comes back on demand
        assert _wait(lambda: sp.spoken == 3), _log(tmp_path)
    finally:
        sp.close()


def _alive(pid):
    if sys.platform == "win32":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie isn't alive
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


def test_speaker_keeps_the_error_and_goes_on(tmp_path):
    sp = _speaker(tmp_path)
    try:
        sp.say(_line("FAIL-SYNTH"))
        assert _wait(lambda: sp.last_error is not None), _log(tmp_path)
        assert "fake synthesis failure" in sp.last_error
        sp.say(_line("fine"))
        assert _wait(lambda: sp.spoken == 1) and sp.last_error is None
    finally:
        sp.close()


def test_speaker_drops_stale_lines_and_keeps_the_newest_five(tmp_path):
    sp = _speaker(tmp_path, delay="1")
    try:
        sp.say(_line("old", created=datetime.now() - timedelta(seconds=v.STALE_S + 5)))
        sp.say(_line("first"))
        assert _wait(lambda: any(s["parts"] == ["first"] for s in _said(tmp_path)) or sp.busy() is False), _log(tmp_path)
        for i in range(7):
            sp.say(_line(f"n{i}"))
        assert _wait(lambda: sp.spoken >= 6, 30), _log(tmp_path)
        got = [s["parts"][0] for s in _said(tmp_path)]
        assert "old" not in got and got[0] == "first"
        assert got[-5:] == ["n2", "n3", "n4", "n5", "n6"]
    finally:
        sp.close()


def test_speaker_stop_empties_the_queue_and_cuts_the_line(tmp_path):
    sp = _speaker(tmp_path, delay="20")
    try:
        sp.say(_line("long"))
        sp.say(_line("never"))
        assert _wait(lambda: sp._proc is not None), _log(tmp_path)
        time.sleep(1.5)  # "long" is playing, "never" waits behind it
        sp.stop()
        assert _wait(lambda: sp.spoken == 1, 10), _log(tmp_path)
        assert [s["parts"] for s in _said(tmp_path)] == [["long"]] and _said(tmp_path)[0]["stopped"]
    finally:
        sp.close()


def test_speaker_survives_a_worker_that_cannot_start(tmp_path):
    sp = v.Speaker(tmp_path / "state", command=lambda: [str(tmp_path / "no-such-python")])
    try:
        sp.say(_line("x"))
        assert _wait(lambda: sp.last_error is not None), _log(tmp_path)
    finally:
        sp.close()


def test_speaker_close_kills_the_worker(tmp_path):
    sp = _speaker(tmp_path, delay="20")
    sp.say(_line("long"))
    assert _wait(lambda: bool(_said(tmp_path)) or sp._proc is not None), _log(tmp_path)
    proc = sp._proc
    sp.close()
    assert proc is None or proc.poll() is not None



# -- found in review ------------------------------------------------------------------------------------
def test_a_snooze_that_ran_out_lets_the_later_times_speak(env):
    q = ["23:00", "08:00"]
    it = item("s", "Pay rent", status="snoozed", snooze_until="2026-10-20T09:00", remind_at="2026-10-22T10:00",
              due="2026-10-24T14:00")
    assert [r for r, _ in v.reminder_times(it, q)] == ["2026-10-20T09:00", "2026-10-22T10:00", "2026-10-24T14:00"]
    early = item("e", status="snoozed", snooze_until="2026-10-25T09:00", remind_at="2026-10-22T10:00",
                 due="2026-10-24T14:00")
    assert [r for r, _ in v.reminder_times(early, q)] == ["2026-10-25T09:00"]  # nothing while it's snoozed
    assert v.reminder_times(item("x", status="snoozed", snooze_until="junk", due="2026-10-24"), q) == []


def test_odd_dates_are_skipped_not_fatal_for_the_others(env):
    q = ["23:00", "08:00"]
    assert v.reminder_times(item("n", due=12345), q) == []
    aware = v.reminder_times(item("a", due="2026-10-23T15:01+00:00"), q)
    assert aware and aware[0][1].tzinfo is None
    env.items = [item("bad", due="2026-10-23T15:00:30+99:99"), item("odd", due=7),
                 item("good", "Feed the cat", due="2026-10-23T15:01")]
    env.a.tick()
    env.now.t = T0 + timedelta(seconds=90)
    env.a.tick()
    assert env.sink.said == [["A reminder. Feed the cat. It's due today at 3 oh 1 pm."]]


def test_an_error_stop_ends_the_turn(env):
    env.a.agent_transition(_tr("s1", "idle", "running", T0))
    env.a.agent_transition(_tr("s1", "running", "waiting", T0 + timedelta(seconds=30), kind="stop_failure"))
    env.a.agent_transition(_tr("s1", "waiting", "running", T0 + timedelta(hours=2)))
    env.a.agent_transition(_tr("s1", "running", "finished", T0 + timedelta(hours=2, seconds=20)))
    assert env.sink.said == [["Robot arm needs you."]]  # the 20 s turn is under the threshold


def test_a_long_sleep_is_a_catch_up_and_a_day_counts_as_none(env):
    env.items = [item("a", "One", due="2026-10-23T16:00"), item("b", "Two", due="2026-10-23T17:00")]
    env.a.tick()
    env.now.t = T0 + timedelta(hours=3)  # the laptop slept; the same hub process wakes up
    env.a.tick()
    assert env.sink.said == [["You have two reminders from while I was off."]]
    env.items = [item("c", "Three", due="2026-10-24T12:00")]
    env.now.t = T0 + timedelta(days=3)
    env.a.tick()
    assert len(env.sink.said) == 1


def test_one_sessions_lines_merge_into_its_latest(env):
    a = env.make(merge_s=60)
    a.agent_transition(_tr("s1", "running", "waiting", T0, kind="permission_prompt"))
    a.agent_transition(_tr("s1", "running", "waiting", T0 + timedelta(seconds=1), kind="permission_prompt"))
    a.flush("agent_waiting")
    assert env.sink.said == [["Robot arm needs you. It's asking for permission."]]
    a.close()


def test_the_tick_keeps_its_window_when_the_items_cannot_be_read(env):
    env.items = [item("a", "One", due="2026-10-23T15:01")]
    env.a.tick()
    good = env.a._items

    def broken():
        raise RuntimeError("database is locked")
    env.a._items = broken
    env.now.t = T0 + timedelta(seconds=90)
    env.a.tick()
    env.a._items = good
    env.now.t = T0 + timedelta(seconds=120)
    env.a.tick()
    assert env.sink.said == [["A reminder. One. It's due today at 3 oh 1 pm."]]


def test_speaker_drops_a_line_stopped_before_it_was_sent(tmp_path):
    sp = v.Speaker(tmp_path / "state", command=lambda: ["unused"])
    sp._stops = 1
    assert sp._write({"op": "say"}, gen=0) is None  # stopped after it left the queue: never sent
    sp.close()
