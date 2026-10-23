"""Phase 3: tablet notes, answers, IGNORE, "File this", the tablet's state and long-poll."""
import asyncio
from datetime import datetime

import pytest

from brainhub import holding, osdirs
from brainhub.rawlog import read_log
from brainhub.server import create_app
from brainhub.tablet_routes import answer_text
from brainhub.tablet_view import robot_url, today_groups, usage_rows, when
from tests.test_server import MAP, make_runner


@pytest.fixture
async def client(aiohttp_client, paths):
    app = create_app(paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(app)
    assert (await c.post("/api/setup", json={"map": MAP, "backup_remote": None})).status == 200
    return c


async def _request(c, items=("ArUco ID 3 → arm base", "ID 3 → ID 7")):
    r = await c.post("/api/requests", json={"title": "Arm calibration", "items": list(items)})
    return (await r.json())["request"]


# -- the view's formatting ----------------------------------------------------------------------
def test_when():
    now = datetime(2026, 10, 23, 12, 0)
    assert when("2026-10-23T14:05", now) == "14:05" and when("2026-10-23", now) == "today"
    assert when("2026-10-21", now) == "10-21" and when("2026-10-21T09:00:00", now) == "10-21 09:00"
    assert when(None, now) == "" and when("garbage", now) == "garbage"


def test_robot_url_points_the_arm_back_home():
    assert robot_url("http://127.0.0.1:8080", "192.168.0.200:8090") == (
        "http://192.168.0.200:8080/?home=http%3A%2F%2F192.168.0.200%3A8090%2Ftablet")
    assert robot_url("http://arm.lan:9000/ui?x=1", "192.168.0.200:8090") == (
        "http://arm.lan:9000/ui?x=1&home=http%3A%2F%2F192.168.0.200%3A8090%2Ftablet")


def test_today_groups_and_usage_rows():
    now = datetime(2026, 10, 23, 12)
    item = {"text": "retune PID", "due": "2026-10-21", "remind_at": None, "person": None}
    today = {"overdue": [item], "due_today": [], "upcoming": [],
             "waiting": [{"item": {"text": "endpoints", "person": "andrei"}, "nudge": True}]}
    groups = today_groups(today, now)
    assert [g["title"] for g in groups] == ["OVERDUE", "WAITING ON OTHERS"]
    assert groups[0]["rows"][0] == {"text": "retune PID", "meta": "due 10-21", "attention": True}
    assert groups[1]["rows"][0]["meta"] == "andrei · no news"
    rows = usage_rows({"five_hour": {"used": 61.6, "resets_at": "2026-10-23T15:00", "warn": False},
                       "seven_day": None}, now)
    assert rows[0] == {"label": "5 HOURS", "pct": 62, "text": "resets 15:00", "warn": False}
    assert rows[1]["pct"] is None and rows[1]["text"] == "no data yet"


def test_answer_text_names_what_was_left_empty():
    req = {"title": "Arm calibration", "project": "robotarm", "project_name": "Robot arm",
           "items": ["ArUco ID 3 → arm base", "ID 3 → ID 7", "jaw gap"]}
    assert answer_text(req, {0: "412 mm", 2: "3.5 mm"}) == (
        "Measurements: Arm calibration (Robot arm)\n- ArUco ID 3 → arm base: 412 mm\n- jaw gap: 3.5 mm\n"
        "(not answered: ID 3 → ID 7)")


# -- notes ----------------------------------------------------------------------------------------
async def test_a_tablet_note_is_kept_verbatim_and_never_filed(client, paths):
    app = client.server.app
    v = app["feed"].version
    text = "motor 3 runs hot after 10 min\n  check the driver's current limit"
    r = await client.post("/api/tablet/notes", json={"text": text})
    assert r.status == 201 and (await r.json())["held"] is False
    eid = (await r.json())["entry_id"]
    logged = [e for e in read_log(paths.brain) if e.id == eid][0]
    assert logged.text == text and logged.source == "tablet"
    assert app["store"].get_filing(eid) is None and app["worker"].queue.empty()  # owner: not auto-filed
    assert app["feed"].version > v
    hits = await (await client.get("/api/search", params={"q": "driver current"})).json()
    assert any(h["ref"] == eid for h in hits)  # recall finds it
    state = await (await client.get("/api/tablet/state")).json()
    assert state["notes"][0]["id"] == eid and state["notes"][0]["answer_to"] is None
    for bad in ({"text": "  "}, {"text": 5}, {}, {"text": "x" * 20001}):
        assert (await client.post("/api/tablet/notes", json=bad)).status == 400


async def test_tablet_notes_need_setup(aiohttp_client, paths):
    c = await aiohttp_client(create_app(paths, runner=make_runner(), start_workers=False))
    assert (await c.post("/api/tablet/notes", json={"text": "x"})).status == 409
    assert (await c.get("/api/tablet/state")).status == 409
    assert not (paths.brain / "log").exists()


async def test_a_held_tablet_note_is_not_filed_when_flushed(aiohttp_client, paths):
    held = holding.hold(osdirs.state_dir(), paths, "held at the bench", "tablet", datetime(2026, 10, 23, 9))
    pc = holding.hold(osdirs.state_dir(), paths, "held at the pc", "pc", datetime(2026, 10, 23, 9, 1))
    first = create_app(paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(first)
    assert (await c.post("/api/setup", json={"map": MAP, "backup_remote": None})).status == 200
    await c.close()
    app = create_app(paths, runner=make_runner(), start_workers=False)
    await aiohttp_client(app)  # startup flushes the held captures
    assert app["store"].get_entry(held["id"]).source == "tablet"
    assert app["store"].get_filing(held["id"]) is None
    assert app["store"].get_filing(pc["id"]).status == "queued"


# -- answers ----------------------------------------------------------------------------------------
async def test_answering_writes_one_entry_and_closes_the_request(client, paths):
    app = client.server.app
    req = await _request(client)
    state = await (await client.get("/api/tablet/state")).json()
    assert [r["id"] for r in state["requests"]] == [req["id"]] and state["home"]["journal"].startswith("1 request")
    r = await client.post(f"/api/tablet/requests/{req['id']}/answer", json={"answers": {"0": " 412 mm ", "1": ""}})
    assert r.status == 201
    eid = (await r.json())["entry_id"]
    entry = [e for e in read_log(paths.brain) if e.id == eid][0]
    assert entry.text == ("Measurements: Arm calibration\n- ArUco ID 3 → arm base: 412 mm\n"
                          "(not answered: ID 3 → ID 7)")
    assert app["store"].get_request(req["id"])["status"] == "answered"
    assert app["store"].get_filing(eid) is None
    state = await (await client.get("/api/tablet/state")).json()
    assert state["requests"] == [] and state["notes"][0]["answer_to"] == "Arm calibration"
    r = await client.post(f"/api/tablet/requests/{req['id']}/answer", json={"answers": {"0": "1 mm"}})
    assert r.status == 409


@pytest.mark.parametrize("answers", [{}, {"0": " "}, {"5": "1"}, {"x": "1"}, {"0": "a\nb"}, {"0": 3},
                                     {"0": "x" * 201}, "412 mm", None])
async def test_bad_answers(client, answers):
    req = await _request(client)
    r = await client.post(f"/api/tablet/requests/{req['id']}/answer", json={"answers": answers})
    assert r.status == 400, (answers, await r.text())


async def test_answer_to_an_unknown_request(client):
    assert (await client.post("/api/tablet/requests/r-x/answer", json={"answers": {"0": "1"}})).status == 404


# -- IGNORE, the PC view, File this ------------------------------------------------------------------
async def test_ignore_hides_and_restore_brings_back(client):
    eid = (await (await client.post("/api/tablet/notes", json={"text": "stale idea"})).json())["entry_id"]
    req = await _request(client)
    assert (await client.post("/api/tablet/ignore", json={"kind": "note", "id": eid})).status == 204
    assert (await client.post("/api/tablet/ignore", json={"kind": "request", "id": req["id"]})).status == 204
    state = await (await client.get("/api/tablet/state")).json()
    assert state["notes"] == [] and state["requests"] == []
    view = await (await client.get("/api/tablet-notes")).json()
    assert view["notes"] == [] and view["requests"] == [] and view["hidden"] == {"notes": 1, "requests": 1}
    view = await (await client.get("/api/tablet-notes", params={"hidden": "1"})).json()
    assert view["notes"][0]["hidden"] is True and view["requests"][0]["hidden"] is True
    assert (await client.post(f"/api/tablet-notes/{eid}/restore")).status == 204
    assert (await (await client.get("/api/tablet/state")).json())["notes"][0]["id"] == eid
    for bad in ({"kind": "item", "id": eid}, {"kind": "note"}, {"kind": "note", "id": 3}):
        assert (await client.post("/api/tablet/ignore", json=bad)).status == 400
    pc_entry = (await (await client.post("/api/entries", json={"text": "pc", "source": "pc"})).json())["entry_id"]
    assert (await client.post("/api/tablet/ignore", json={"kind": "note", "id": pc_entry})).status == 404


async def test_file_this_sends_a_note_through_filing(client):
    app = client.server.app
    eid = (await (await client.post("/api/tablet/notes", json={"text": "order a new wrist servo"})).json())["entry_id"]
    view = await (await client.get("/api/tablet-notes")).json()
    assert view["notes"][0]["filing"] is None
    assert (await client.post(f"/api/tablet-notes/{eid}/file")).status == 202
    assert app["store"].get_filing(eid).status == "queued"
    assert (await client.post(f"/api/tablet-notes/{eid}/file")).status == 409  # already queued
    await app["worker"].file_entry(eid)
    assert app["store"].get_filing(eid).status == "done"
    assert (await (await client.get("/api/tablet-notes")).json())["notes"][0]["filing"] == "done"
    pc_entry = (await (await client.post("/api/entries", json={"text": "pc", "source": "pc"})).json())["entry_id"]
    assert (await client.post(f"/api/tablet-notes/{pc_entry}/file")).status == 404


# -- state and the long-poll --------------------------------------------------------------------------
async def test_state_on_the_pc(client):
    state = await (await client.get("/api/tablet/state")).json()
    assert state["device"] is None and state["version"] == client.server.app["feed"].version
    assert state["robot_url"].startswith("http://127.0.0.1:8080/?home=http%3A%2F%2F127.0.0.1%3A")
    assert state["home"]["arm"] == "arm server offline" and [u["label"] for u in state["usage"]] == ["5 HOURS", "WEEK"]


async def test_wait_wakes_on_a_change(client, monkeypatch):
    app = client.server.app
    v = app["feed"].version
    waiter = asyncio.create_task(client.get("/api/tablet/wait", params={"since": str(v)}))
    await asyncio.sleep(0.05)
    assert not waiter.done()
    await client.post("/api/tablet/notes", json={"text": "wake up"})
    r = await asyncio.wait_for(waiter, 5)
    assert (await r.json())["version"] > v
    monkeypatch.setattr("brainhub.tablet_routes.WAIT_S", 0.05)
    same = app["feed"].version
    assert (await (await client.get("/api/tablet/wait", params={"since": str(same)})).json())["version"] == same
    assert (await client.get("/api/tablet/wait", params={"since": "x"})).status == 400


async def test_progress_events_dont_wake_the_tablet(client):
    app = client.server.app
    v = app["feed"].version
    await app["broadcast"]("filing.progress", {"entry_id": "e", "step": "reading"})
    assert app["feed"].version == v
