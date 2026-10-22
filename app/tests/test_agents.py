import sys
from datetime import datetime, timedelta

import pytest

from brainhub.agents import (AgentTracker, find_git_dir, read_branch, resolve_project,
                             simplify_branch)
from brainhub.brainmap import BrainMap, save_map

T0 = datetime(2026, 10, 21, 12, 0, 0)


class Now:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def now():
    return Now()


@pytest.fixture
def tracker(store, paths, now):
    return AgentTracker(store, paths, lambda: {"agents_end_linger_s": 60, "agents_stale_h": 12}, now)


def ev(name, sid="s1", cwd="C:/work/proj", **kw):
    return {"session_id": sid, "hook_event_name": name, "cwd": cwd, "entrypoint": "cli", **kw}


def make_repo(root, head="ref: refs/heads/main\n"):
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(head)
    return root


def test_read_branch_variants(tmp_path):
    r = make_repo(tmp_path / "r1", "ref: refs/heads/feat/ui-reskin\n")
    assert read_branch(str(r)) == "feat/ui-reskin"
    assert simplify_branch("feat/ui-reskin") == "ui-reskin"
    assert simplify_branch(None) is None
    r2 = make_repo(tmp_path / "r2", "a" * 40 + "\n")
    assert read_branch(str(r2)) == "detached"
    sub = r / "a" / "b"
    sub.mkdir(parents=True)
    assert read_branch(str(sub)) == "feat/ui-reskin"
    assert find_git_dir(str(sub))[0] == r
    wt = tmp_path / "wt"
    wt.mkdir()
    gd = tmp_path / "real" / ".git" / "worktrees" / "w1"
    gd.mkdir(parents=True)
    (gd / "HEAD").write_text("ref: refs/heads/w1\n")
    (wt / ".git").write_text("gitdir: ../real/.git/worktrees/w1\n")
    assert read_branch(str(wt)) == "w1"
    plain = tmp_path / "plain"
    plain.mkdir()
    assert read_branch(str(plain)) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path rules")
def test_project_from_map_is_case_and_separator_insensitive():
    m = BrainMap(projects=[
        {"slug": "robotarm", "name": "RobotArm", "is_company": False, "folders": ["D:/RobotArmControl/"]},
        {"slug": "work", "name": "Work", "is_company": False, "folders": ["D:/Work"]},
        {"slug": "robot", "name": "Robot", "is_company": False, "folders": ["D:/Work/robot"]}])
    assert resolve_project(r"d:\robotarmcontrol\src", m) == ("robotarm", "RobotArm", True)
    assert resolve_project(r"D:\RobotArmControlOld", m)[2] is False
    assert resolve_project(r"D:\work\robot\x", m) == ("robot", "Robot", True)
    assert resolve_project(r"D:\work\other", m) == ("work", "Work", True)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX path rules")
def test_project_from_map_is_exact_on_posix():
    m = BrainMap(projects=[
        {"slug": "robotarm", "name": "RobotArm", "is_company": False, "folders": ["/home/u/dev/RobotArmControl/"]},
        {"slug": "work", "name": "Work", "is_company": False, "folders": ["/home/u/Work"]},
        {"slug": "robot", "name": "Robot", "is_company": False, "folders": ["/home/u/Work/robot"]}])
    assert resolve_project("/home/u/dev/RobotArmControl/src", m) == ("robotarm", "RobotArm", True)
    assert resolve_project("/home/u/dev/robotarmcontrol/src", m)[2] is False
    assert resolve_project("/home/u/dev/RobotArmControlOld", m)[2] is False
    assert resolve_project("/home/u/Work/robot/x", m) == ("robot", "Robot", True)
    assert resolve_project("/home/u/Work/other", m) == ("work", "Work", True)


def test_project_fallback_git_top_then_basename(tmp_path):
    r = make_repo(tmp_path / "myrepo")
    (r / "sub").mkdir()
    assert resolve_project(str(r / "sub"), None) == ("myrepo", "myrepo", False)
    d = tmp_path / "loose"
    d.mkdir()
    assert resolve_project(str(d), None) == ("loose", "loose", False)


def test_lifecycle(tracker, now):
    assert tracker.handle(ev("SessionStart", source="startup")) is True
    r = tracker.rows()[0]
    assert (r["status"], r["where"]) == ("idle", "terminal")
    assert tracker.handle(ev("UserPromptSubmit")) is True
    assert tracker.rows()[0]["status"] == "running"
    assert tracker.handle(ev("Notification", notification_type="permission_prompt",
                             message="Claude needs your permission to use Bash")) is True
    r = tracker.rows()[0]
    assert r["status"] == "waiting" and r["note"] == "Claude needs your permission to use Bash"
    assert tracker.handle(ev("PostToolUse")) is True
    r = tracker.rows()[0]
    assert r["status"] == "running" and r["note"] is None
    assert tracker.handle(ev("Stop")) is True
    assert tracker.rows()[0]["status"] == "finished"
    assert tracker.handle(ev("Notification", notification_type="idle_prompt")) is False
    assert tracker.rows()[0]["status"] == "finished"
    tracker.handle(ev("SessionEnd"))
    assert tracker.rows()[0]["ended_at"] is not None
    now.advance(seconds=61)
    assert tracker.rows() == []


def test_idle_prompt_while_running_waits(tracker):
    tracker.handle(ev("UserPromptSubmit"))
    assert tracker.handle(ev("Notification", notification_type="idle_prompt")) is True
    assert tracker.rows()[0]["status"] == "waiting"


def test_stop_failure_waits_with_error_note(tracker):
    tracker.handle(ev("StopFailure", error_type="rate_limit"))
    r = tracker.rows()[0]
    assert r["status"] == "waiting" and r["note"] == "rate_limit"
    tracker.handle(ev("StopFailure", sid="s2", error="boom"))
    assert {x["session_id"]: x["note"] for x in tracker.rows()}["s2"] == "boom"


def test_unknown_session_any_event_creates_row(tracker):
    tracker.handle(ev("Stop"))
    assert tracker.rows()[0]["status"] == "finished"
    assert tracker.handle(ev("SessionEnd", sid="zz")) is False
    assert [r["session_id"] for r in tracker.rows()] == ["s1"]


def test_ignored_events(tracker, paths):
    assert tracker.handle(ev("SessionStart", entrypoint="sdk-cli")) is False
    assert tracker.handle(ev("SessionStart", cwd=str(paths.data / "worktrees" / "x"))) is False
    assert tracker.handle(ev("SessionStart", cwd=str(paths.brain))) is False
    assert tracker.rows() == []
    with pytest.raises(ValueError):
        tracker.handle({"hook_event_name": "Stop"})
    with pytest.raises(ValueError):
        tracker.handle({"session_id": "s", "hook_event_name": 5})


def test_part_priority_and_edit(tracker, tmp_path):
    r = make_repo(tmp_path / "repo", "ref: refs/heads/feat/ui-reskin\n")
    cwd = str(r)
    tracker.handle(ev("SessionStart", cwd=cwd))
    assert tracker.rows()[0]["part"] == "ui-reskin"
    tracker.handle(ev("UserPromptSubmit", cwd=cwd, session_title="Reskin work"))
    assert tracker.rows()[0]["part"] == "Reskin work"
    assert tracker.edit("s1", {"part": " wrist "})["part"] == "wrist"
    assert tracker.edit("s1", {"part": ""})["part"] == "Reskin work"
    assert tracker.edit("s1", {"where": "IDE"})["where"] == "IDE"
    with pytest.raises(ValueError):
        tracker.edit("s1", {"where": "moon"})
    with pytest.raises(ValueError):
        tracker.edit("s1", {"bogus": 1})
    with pytest.raises(ValueError):
        tracker.edit("s1", {"part": "x" * 81})
    tracker.edit("s1", {"part": "wrist"})
    tracker.handle(ev("UserPromptSubmit", cwd=cwd))
    r = tracker.rows()[0]
    assert r["part"] == "wrist" and r["where"] == "IDE"
    assert tracker.edit("s1", {"where": None})["where"] == "terminal"
    with pytest.raises(KeyError):
        tracker.edit("nope", {"part": "a"})
    tracker.remove("s1")
    assert tracker.rows() == []


def test_stale_rows_pruned(tracker, now):
    tracker.handle(ev("UserPromptSubmit"))
    now.advance(hours=12)
    assert len(tracker.rows()) == 1
    now.advance(seconds=1)
    assert tracker.rows() == []


def test_next_time_flag(tracker, store, paths):
    save_map(paths.brain, BrainMap(projects=[
        {"slug": "robotarm", "name": "RobotArm", "is_company": False, "folders": ["D:/RobotArmControl"]}]))
    store.create_item(kind="next_time", text="check J5", area="projects", project="robotarm",
                      source_entry="e-1")
    tracker.handle(ev("SessionStart", cwd="D:/RobotArmControl/src"))
    tracker.handle(ev("SessionStart", sid="s2", cwd="D:/Other"))
    rows = {r["session_id"]: r for r in tracker.rows()}
    assert rows["s1"]["next_time"] == 1 and rows["s1"]["mapped"] is True
    assert rows["s2"]["next_time"] == 0


def test_post_tool_use_is_cheap(tracker, now):
    tracker.handle(ev("UserPromptSubmit"))
    first = tracker.rows()[0]["last_event_at"]
    now.advance(seconds=10)
    assert tracker.handle(ev("PostToolUse")) is False
    assert tracker.rows()[0]["last_event_at"] == first
    now.advance(seconds=21)
    assert tracker.handle(ev("PostToolUse")) is False
    assert tracker.rows()[0]["last_event_at"] != first
    assert tracker.last_event_at() == tracker.rows()[0]["last_event_at"]


def test_note_is_capped(tracker):
    tracker.handle(ev("Notification", notification_type="permission_prompt", message="x" * 10000))
    assert len(tracker.rows()[0]["note"]) <= 300


def test_session_start_sources(tracker):
    tracker.handle(ev("UserPromptSubmit"))
    tracker.handle(ev("SessionEnd"))
    tracker.handle(ev("SessionStart", source="resume"))
    r = tracker.rows()[0]
    assert r["status"] == "running" and r["ended_at"] is None
    tracker.handle(ev("SessionStart", source="startup"))
    assert tracker.rows()[0]["status"] == "idle"


def test_ended_session_is_marked_ended_and_sorted_last(tracker, now):
    """A session closed mid-turn keeps its DB status, but shows as ended (final review #6)."""
    tracker.handle(ev("UserPromptSubmit", sid="a"))
    now.advance(seconds=1)
    tracker.handle(ev("Stop", sid="b"))
    assert [r["ended"] for r in tracker.rows()] == [False, False]
    now.advance(seconds=1)
    tracker.handle(ev("SessionEnd", sid="a"))
    rows = tracker.rows()
    assert [(r["session_id"], r["status"], r["ended"]) for r in rows] == [("b", "finished", False),
                                                                        ("a", "running", True)]
    tracker.handle(ev("SessionStart", sid="a", source="resume"))
    assert {r["session_id"]: r["ended"] for r in tracker.rows()} == {"a": False, "b": False}
