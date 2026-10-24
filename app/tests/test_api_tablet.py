"""Phase 3: tablet notes, answers, IGNORE, "File this", the tablet's state and long-poll."""
import asyncio
from datetime import datetime

import pytest

from atlas import holding, osdirs
from atlas.rawlog import read_log
from atlas.server import create_app
from atlas.tablet_routes import answer_text
from atlas.tablet_view import robot_url, today_groups, usage_rows, when
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
    async def offline(url):  # never the real port 8080: the owner's arm server may be running
        return False
    client.server.app["arm"].reachable = offline
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
    monkeypatch.setattr("atlas.tablet_routes.WAIT_S", 0.05)
    same = app["feed"].version
    assert (await (await client.get("/api/tablet/wait", params={"since": str(same)})).json())["version"] == same
    assert (await client.get("/api/tablet/wait", params={"since": "x"})).status == 400


async def test_progress_events_dont_wake_the_tablet(client):
    app = client.server.app
    v = app["feed"].version
    await app["broadcast"]("filing.progress", {"entry_id": "e", "step": "reading"})
    assert app["feed"].version == v


async def test_the_page_and_its_files(client):
    r = await client.get("/tablet")
    assert r.status == 200 and "text/html" in r.headers["Content-Type"] and "tablet.js" in await r.text()
    r = await client.get("/tablet/tablet.js")
    assert r.status == 200 and r.headers["Cache-Control"] == "no-cache"
    assert r.headers["Content-Type"] == "application/javascript; charset=utf-8"
    for bad in ("/tablet/server.py", "/tablet/..%2Fwidget.html", "/tablet/tablet.test.mjs", "/tablet/nope.js"):
        assert (await client.get(bad)).status == 404, bad


async def test_open_requests_reach_the_widget(client):
    w = await (await client.get("/api/widget")).json()
    assert w["requests"] == 0 and w["attention"] is False
    req = await _request(client)
    w = await (await client.get("/api/widget")).json()
    assert w["requests"] == 1 and w["attention"] is True and "1 request" in w["synopsis"]
    await client.post(f"/api/requests/{req['id']}/ignore")
    assert (await (await client.get("/api/widget")).json())["requests"] == 0


async def test_tablet_lan_status_for_settings(client):
    s = await (await client.get("/api/tablet-lan")).json()
    assert s["tablet_lan"] is False and s["listening"] is None and s["error"] is None
    assert "mm" in s["tablet_units"] and isinstance(s["port"], int)
    assert s["this_address"] is None or s["this_address"].count(".") == 3


# -- review: a resend after a lost response never duplicates an entry ----------------------------
async def test_a_resent_note_is_written_once(client, paths):
    body = {"text": "check the belt tension", "client_id": "k7x2m9q4p1"}
    first = await client.post("/api/tablet/notes", json=body)
    again = await client.post("/api/tablet/notes", json=body)
    assert first.status == 201 and again.status == 200
    assert (await first.json())["entry_id"] == (await again.json())["entry_id"]
    assert (await again.json())["duplicate"] is True
    assert sum(1 for e in read_log(paths.brain) if e.text == "check the belt tension") == 1
    assert (await client.post("/api/tablet/notes", json={"text": "x", "client_id": "bad id!"})).status == 400


async def test_a_resent_answer_is_written_once_and_not_refused(client, paths):
    req = await _request(client)
    body = {"answers": {"0": "412 mm"}, "client_id": "a1b2c3d4e5"}
    first = await client.post(f"/api/tablet/requests/{req['id']}/answer", json=body)
    again = await client.post(f"/api/tablet/requests/{req['id']}/answer", json=body)
    assert first.status == 201 and again.status == 200 and (await again.json())["duplicate"] is True
    assert sum(1 for e in read_log(paths.brain) if e.text.startswith("Measurements: Arm calibration")) == 1


async def test_state_version_is_read_before_the_data(client, monkeypatch):
    """A change landing while the state is gathered must not be lost: the tablet would hold the
    newer version with the older data."""
    app = client.server.app
    import atlas.tablet_routes as tr
    real = tr._today

    def bump_midway(a):
        out = real(a)
        app["feed"].bump()  # a change landing while the rest of the state is read
        return out

    monkeypatch.setattr(tr, "_today", bump_midway)
    state = await (await client.get("/api/tablet/state")).json()
    assert state["version"] < app["feed"].version  # so the next wait answers at once


# -- review: answers under load and failure; holds; setup -----------------------------------------
async def test_simultaneous_answers_land_once(client, paths):
    req = await _request(client)
    rs = await asyncio.gather(*[client.post(f"/api/tablet/requests/{req['id']}/answer",
                                            json={"answers": {"0": f"{n} mm"}}) for n in range(3)])
    assert sorted(r.status for r in rs) == [201, 409, 409]
    assert sum(1 for e in read_log(paths.brain) if e.text.startswith("Measurements:")) == 1


async def test_a_failure_after_the_log_still_answers_201(client, monkeypatch):
    req = await _request(client)
    app = client.server.app

    def boom(*a):
        raise OSError("disk full")

    monkeypatch.setattr(app["store"], "export_tablet", boom)
    r = await client.post(f"/api/tablet/requests/{req['id']}/answer", json={"answers": {"0": "1 mm"}})
    assert r.status == 201  # the log has it: a resend would only duplicate it
    assert app["store"].get_request(req["id"])["status"] == "answered"


async def test_a_held_answer_is_linked_once_flushed(aiohttp_client, paths):
    first = create_app(paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(first)
    assert (await c.post("/api/setup", json={"map": MAP, "backup_remote": None})).status == 200
    req = (await (await c.post("/api/requests", json={"title": "t", "items": ["a"]})).json())["request"]
    await c.close()
    text = "Measurements: t\n- a: 5 mm"
    held = holding.hold(osdirs.state_dir(), paths, text, "tablet", datetime(2026, 10, 23, 9), request=req["id"])
    app = create_app(paths, runner=make_runner(), start_workers=False)
    await aiohttp_client(app)  # startup flushes it
    got = app["store"].get_request(req["id"])
    assert got["status"] == "answered" and got["answer_entry"] == held["id"]


async def test_ignore_and_file_this_need_setup(aiohttp_client, paths):
    c = await aiohttp_client(create_app(paths, runner=make_runner(), start_workers=False))
    assert (await c.post("/api/tablet/ignore", json={"kind": "note", "id": "e-x"})).status == 409
    assert (await c.post("/api/tablet-notes/e-x/file")).status == 409
    assert not (paths.brain / "_items").exists()
