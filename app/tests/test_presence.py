import sys
from datetime import datetime, timedelta

import pytest

from atlas import presence as pr

T0 = datetime(2026, 10, 26, 15, 0, 0)


class Now:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


@pytest.fixture
def box():
    now = Now()
    probe = {"locked": False, "idle_s": None}
    returns = []
    p = pr.Presence(now=now, probe=lambda: dict(probe), on_return=returns.append)
    return now, probe, returns, p


def test_nothing_seen_yet_is_idle_not_away(box):
    now, _, returns, p = box
    p.poll()
    assert p.state() == "idle" and returns == []


def test_activity_makes_present_then_idle_then_away_and_back(box):
    now, _, returns, p = box
    p.seen("prompt")
    assert p.state() == "present"
    now.t += timedelta(minutes=11)
    assert p.state() == "idle"
    now.t += timedelta(minutes=10)
    p.poll()
    assert p.state() == "away" and p.away_since() == T0
    now.t += timedelta(minutes=30)
    p.seen("app")
    assert returns == [T0] and p.state() == "present"
    p.seen("app")
    assert returns == [T0]  # one welcome back per absence


def test_the_lock_screen_is_away_and_unlocking_is_the_return(box):
    now, probe, returns, p = box
    p.seen()
    probe["locked"] = True
    now.t += timedelta(minutes=1)
    p.poll()
    assert p.state() == "away" and p.away_since() == now.t
    p.seen("hook")  # an agent's hook while locked isn't the owner back
    assert returns == []
    probe["locked"] = False
    now.t += timedelta(minutes=5)
    p.poll()
    assert returns == [T0 + timedelta(minutes=1)] and p.state() == "present"


def test_windows_idle_time_counts_as_activity(box):
    now, probe, returns, p = box
    probe["idle_s"] = 5
    p.poll()
    assert p.state() == "present"


def test_the_away_threshold_is_a_callable(box):
    now, probe, returns, _ = box
    p = pr.Presence(now=now, probe=lambda: dict(probe), away_after=lambda: timedelta(minutes=1), on_return=returns.append)
    p.seen()
    now.t += timedelta(minutes=2)
    p.poll()
    assert p.state() == "away"


def test_a_failing_probe_or_callback_never_raises(box):
    now, _, _, _ = box

    def boom(*_a):
        raise RuntimeError("x")
    p = pr.Presence(now=now, probe=boom, on_return=boom)
    p.seen()
    now.t += timedelta(hours=1)
    p.poll()
    p.seen()  # the callback raises: swallowed
    assert p.state() == "present"


@pytest.mark.skipif(sys.platform == "win32", reason="the Linux half")
def test_linux_probe_reads_logind(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_ID", "3")
    monkeypatch.setattr(pr.shutil, "which", lambda c: "/usr/bin/" + c)
    calls = []

    def run(cmd):
        calls.append(cmd[0])
        return "yes\n" if cmd[0] == "loginctl" else None
    monkeypatch.setattr(pr, "_run", run)
    assert pr.locked_linux() is True and calls == ["loginctl"]
    monkeypatch.setattr(pr, "_run", lambda cmd: None if cmd[0] == "loginctl" else "(false,)\n")
    assert pr.locked_linux() is False
    monkeypatch.setattr(pr, "_run", lambda cmd: None)
    assert pr.locked_linux() is None
    assert pr.default_probe()["idle_s"] is None


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows half")
def test_windows_probe_reads_last_input():
    idle = pr.idle_windows()
    assert idle is None or idle >= 0
    assert pr.default_probe()["locked"] is None
