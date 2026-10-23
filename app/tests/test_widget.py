import asyncio
from datetime import datetime

from brainhub.brainmap import BrainMap
from brainhub.widget import ArmProbe, build_widget, synopsis
from tests.test_today import mk

NOW = datetime(2026, 10, 20, 12, 0)
NO_USAGE = {"five_hour": None, "seven_day": None}
NO_AGENTS = {"running": 0, "waiting": 0}
NO_REVIEW = {"proposals": 0, "failed": 0}


def test_synopsis_example():
    today = {"overdue": [], "due_today": [{}, {}, {}]}
    usage = {"five_hour": {"used": 62.0, "resets_at": "2026-10-20T14:20", "warn": False}, "seven_day": None}
    s = synopsis(today, NO_REVIEW, [{"name": "Teammate A"}], usage, {"running": 2, "waiting": 0})
    assert s == "3 due today · 1 waiting on Teammate A · 5h 62% (resets 14:20) · 2 agents running"


def test_synopsis_all_clear():
    assert synopsis({"overdue": [], "due_today": []}, NO_REVIEW, [], NO_USAGE, NO_AGENTS) == "All clear"


def test_synopsis_overdue_and_review_and_plural():
    s = synopsis({"overdue": [{}], "due_today": [{}]}, {"proposals": 2, "failed": 1},
                 [{"name": "a"}, {"name": "b"}], NO_USAGE, {"running": 1, "waiting": 3})
    assert s == "2 due today (1 overdue) · 2 waiting · 3 to review · 1 agent running · 3 agents waiting"


def test_build_widget_shapes():
    items = [mk(id="a", kind="deadline", due="2026-10-19", project="robotarm"),
             mk(id="b", kind="deadline", due="2026-10-20T15:00"),
             *[mk(id=f"d{n}", kind="deadline", due=f"2026-11-0{n}") for n in range(1, 8)],
             mk(id="w", kind="waiting", person="teammate-a", text="the part", created="2026-10-10T09:00:00"),
             mk(id="n", kind="next_time", project="robotarm"),
             mk(id="r", kind="waiting", status="to_read"), mk(id="t", kind="task", project="robotarm")]
    bmap = BrainMap(projects=[{"slug": "robotarm", "name": "RobotArm"}],
                    people=[{"slug": "teammate-a", "name": "Teammate A", "projects": []}])
    rows = [{"project_name": "RobotArm", "part": "wrist", "where": "vscode", "status": s, "next_time": None}
            for s in ("running", "running", "waiting")]
    settings = {"nudge_days": 3, "widget_tiles": ["today", "arm"], "arm_url": "http://127.0.0.1:8080"}
    w = build_widget({"items": items, "proposals": 1, "failed": 0, "map": bmap, "usage": NO_USAGE,
                      "agents": rows, "settings": settings, "arm_reachable": True, "now": NOW})
    assert w["today"]["overdue"] == 1 and w["today"]["due_today"] == 1
    assert [x["text"] for x in w["today"]["top"]] == ["t", "t"]
    assert w["attention"] is True and w["tiles"] == ["today", "arm"]
    assert w["agents"]["running"] == 2 and w["agents"]["waiting"] == 1 and len(w["agents"]["rows"]) == 3
    assert w["people"]["waiting"] == [{"person": "teammate-a", "name": "Teammate A", "text": "the part",
                                       "since": "2026-10-10T09:00:00", "nudge": True}]
    assert w["people"]["to_read"] == 1
    dues = [d["due"] for d in w["deadlines"]]
    assert len(dues) == 6 and dues == sorted(dues) and dues[0] == "2026-10-19"
    assert w["projects"] == [{"slug": "robotarm", "name": "RobotArm", "open": 3, "next_time": 1}]
    assert w["review"] == {"proposals": 1, "failed": 0}
    assert w["arm"] == {"url": "http://127.0.0.1:8080", "reachable": True}
    assert w["synopsis"].startswith("2 due today (1 overdue) · 1 waiting on Teammate A")


def test_build_widget_attention_false_when_calm():
    settings = {"nudge_days": 3, "widget_tiles": [], "arm_url": "http://x"}
    w = build_widget({"items": [], "proposals": 0, "failed": 0, "map": None, "usage": NO_USAGE, "agents": [],
                      "settings": settings, "arm_reachable": False, "now": NOW})
    assert w["attention"] is False and w["synopsis"] == "All clear" and w["projects"] == []


async def test_arm_probe_reachable_and_cached():
    clock = [0.0]
    probe = ArmProbe(now=lambda: clock[0], ttl=15.0)
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    assert await probe.reachable(url) is True
    server.close()
    await server.wait_closed()
    clock[0] = 10.0
    assert await probe.reachable(url) is True
    clock[0] = 16.0
    assert await probe.reachable(url) is False
    t0 = asyncio.get_running_loop().time()
    assert await ArmProbe().reachable("http://127.0.0.1:1") is False
    assert asyncio.get_running_loop().time() - t0 < 1.5


async def test_arm_probe_caches_failure_and_does_not_reconnect():
    clock = [0.0]
    probe = ArmProbe(now=lambda: clock[0], ttl=15.0, neg_ttl=15.0)
    accepted = []
    server = await asyncio.start_server(lambda r, w: (accepted.append(1), w.close()), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server.close()
    await server.wait_closed()
    assert await probe.reachable(url) is False
    server = await asyncio.start_server(lambda r, w: (accepted.append(1), w.close()), "127.0.0.1", port)
    try:
        clock[0] = 5.0
        assert await probe.reachable(url) is False and accepted == []  # failure cached
        clock[0] = 16.0
        assert await probe.reachable(url) is True
        await asyncio.sleep(0.05)
        assert len(accepted) == 1
        clock[0] = 20.0
        assert await probe.reachable(url) is True
        await asyncio.sleep(0.05)
        assert len(accepted) == 1  # second probe within ttl did not reconnect
    finally:
        server.close()
        await server.wait_closed()


def test_ended_sessions_excluded_from_counts_and_attention():
    """A session closed mid-turn stays listed (as "ended") but no longer counts (final review #6)."""
    rows = [{"project_name": "P", "part": "x", "where": "terminal", "status": "waiting", "next_time": 0,
             "ended_at": "2026-10-21T11:59:00", "ended": True},
            {"project_name": "P", "part": "y", "where": "terminal", "status": "running", "next_time": 0,
             "ended_at": "2026-10-21T11:59:00", "ended": True},
            {"project_name": "Q", "part": "z", "where": "vscode", "status": "running", "next_time": 0,
             "ended_at": None, "ended": False}]
    settings = {"nudge_days": 3, "widget_tiles": [], "arm_url": "http://x"}
    w = build_widget({"items": [], "proposals": 0, "failed": 0, "map": None, "usage": NO_USAGE, "agents": rows,
                      "settings": settings, "arm_reachable": False, "now": NOW})
    assert (w["agents"]["running"], w["agents"]["waiting"]) == (1, 0)
    assert w["attention"] is False and w["synopsis"] == "1 agent running"
    assert [r["status"] for r in w["agents"]["rows"]] == ["ended", "ended", "running"]


async def test_arm_probe_caches_failure_for_60s_by_default():
    """The arm is normally offline and a refused connect costs ~1 s on Windows: cache a
    negative result for 60 s, a positive one for 15 s (final review #10)."""
    clock = [0.0]
    probe = ArmProbe(now=lambda: clock[0])
    accepted = []
    server = await asyncio.start_server(lambda r, w: (accepted.append(1), w.close()), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server.close()
    await server.wait_closed()
    assert await probe.reachable(url) is False
    server = await asyncio.start_server(lambda r, w: (accepted.append(1), w.close()), "127.0.0.1", port)
    try:
        clock[0] = 59.0
        assert await probe.reachable(url) is False and accepted == []  # still the cached failure
        clock[0] = 61.0
        assert await probe.reachable(url) is True
        clock[0] = 75.0
        assert await probe.reachable(url) is True  # cached success (15 s)
        await asyncio.sleep(0.05)
        assert len(accepted) == 1
        clock[0] = 77.0
        assert await probe.reachable(url) is True
        await asyncio.sleep(0.05)
        assert len(accepted) == 2  # success expired after 15 s: probed again
    finally:
        server.close()
        await server.wait_closed()


def test_synopsis_counts_measurement_requests():
    """Phase 3: an agent asked for workbench measurements; the owner should notice at the PC too."""
    assert synopsis({"overdue": [], "due_today": []}, NO_REVIEW, [], NO_USAGE, NO_AGENTS, 1) == "1 request"
    assert synopsis({"overdue": [], "due_today": []}, NO_REVIEW, [], NO_USAGE, NO_AGENTS, 2) == "2 requests"
