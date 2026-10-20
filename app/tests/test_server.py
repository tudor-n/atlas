import json
from datetime import datetime

import pytest

from brainhub.brainmap import default_map, save_map
from brainhub.rawlog import read_log
from brainhub.server import DEFAULT_HOST, create_app
from tests.fakes import FakeRunner
from tests.test_filing import GOOD_RESULT

MAP = {
    "areas": [{"slug": s, "name": s.capitalize()} for s in ("general", "projects", "uni", "personal")],
    "projects": [{"slug": "robotarm", "name": "RobotArm", "is_company": False, "folders": []}],
    "courses": [], "people": [],
}


def make_runner():
    return FakeRunner(json_result=GOOD_RESULT, stream_chunks=["ok [e-20261020-000000-abcd]"])


@pytest.fixture
def app_paths(paths):
    return paths


@pytest.fixture
def app(app_paths):
    return create_app(app_paths, runner=make_runner(), start_workers=False)


@pytest.fixture
async def client(aiohttp_client, app):
    return await aiohttp_client(app)


@pytest.fixture
async def ready_client(client):
    r = await client.post("/api/setup", json={"map": MAP, "backup_remote": None})
    assert r.status == 200
    return client


async def test_setup_then_state(client):
    state = await (await client.get("/api/state")).json()
    assert state["setup_done"] is False and state["log_error"] is None
    r = await client.post("/api/setup", json={"map": MAP, "backup_remote": None})
    assert r.status == 200
    assert (await (await client.get("/api/state")).json())["setup_done"] is True
    assert (await client.post("/api/setup", json={"map": MAP})).status == 409


async def test_capture_logs_before_filing(ready_client, app_paths):
    r = await ready_client.post("/api/entries", json={"text": "check wrist next time", "source": "pc"})
    assert r.status == 201
    eid = (await r.json())["entry_id"]
    assert read_log(app_paths.brain)[-1].id == eid
    assert (await ready_client.post("/api/entries", json={"text": " ", "source": "pc"})).status == 400
    assert (await ready_client.post("/api/entries", json={"text": "x", "source": "tablet"})).status == 400


async def test_capture_needs_setup(client):
    r = await client.post("/api/entries", json={"text": "hi", "source": "pc"})
    assert r.status == 409


async def test_file_confirm_today_flow(ready_client, app):
    eid = (await (await ready_client.post("/api/entries", json={"text": "x", "source": "pc"})).json())["entry_id"]
    await app["worker"].file_entry(eid)
    card = await (await ready_client.get(f"/api/filings/{eid}")).json()
    assert card["filing"]["status"] == "done"
    ids = [p["id"] for p in card["proposals"]]
    assert ids
    today = await (await ready_client.get("/api/today")).json()
    assert today["review"]["proposals"] == len(ids)
    r = await ready_client.post("/api/proposals/confirm", json={"ids": ids})
    assert r.status == 200
    today = await (await ready_client.get("/api/today")).json()
    assert today["review"]["proposals"] == 0
    journal = await (await ready_client.get("/api/entries")).json()
    assert journal[0]["id"] == eid and journal[0]["filing"]["status"] == "done"


async def test_ask_sends_hits_before_answer(ready_client):
    await ready_client.post("/api/entries", json={"text": "the wrist servo buzzes", "source": "pc"})
    resp = await ready_client.get("/api/ask", params={"q": "wrist servo?"})
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    body = await resp.text()
    assert body.index("event: hits") < body.index("event: delta") < body.index("event: done")


async def test_ask_no_match_says_nothing(ready_client):
    body = await (await ready_client.get("/api/ask", params={"q": "zebra"})).text()
    assert "Nothing on that." in body


async def test_ask_error_event(aiohttp_client, app_paths):
    from brainhub.claude_runner import ClaudeError
    app = create_app(app_paths, runner=FakeRunner(raise_error=ClaudeError("boom")), start_workers=False)
    c = await aiohttp_client(app)
    await c.post("/api/setup", json={"map": MAP})
    await c.post("/api/entries", json={"text": "the wrist servo buzzes", "source": "pc"})
    body = await (await c.get("/api/ask", params={"q": "wrist"})).text()
    assert "event: hits" in body and "event: error" in body and "boom" in body
    assert body.rstrip().endswith("event: done\ndata: {}")


async def test_websocket_receives_entry_saved(ready_client):
    async with ready_client.ws_connect("/ws") as ws:
        await ready_client.post("/api/entries", json={"text": "ping", "source": "pc"})
        msg = await ws.receive_json(timeout=5)
        assert msg["type"] == "entry.saved" and msg["data"]["entry_id"]


async def test_errors_are_json(ready_client):
    r = await ready_client.get("/api/filings/e-20260101-000000-abcd")
    assert r.status == 404 and "error" in await r.json()
    r = await ready_client.post("/api/proposals/confirm", json=[1])
    assert r.status == 400 and "error" in await r.json()


async def test_damaged_log_does_not_crash_startup(aiohttp_client, app_paths):
    save_map(app_paths.brain, default_map())
    log_file = app_paths.brain / "log" / "2026" / "10" / "2026-10-20.md"
    log_file.parent.mkdir(parents=True)
    log_file.write_bytes(b"<!-- entry id=e-20261020-000000-abcd ts=2026-10-20T00:00:00 source=pc len=500 -->\nshort")
    before = log_file.read_bytes()
    c = await aiohttp_client(create_app(app_paths, runner=make_runner(), start_workers=False))
    r = await c.get("/api/state")
    assert r.status == 200
    state = await r.json()
    assert state["setup_done"] is True and state["log_error"]
    assert log_file.read_bytes() == before


async def test_startup_backfills_missing_entries(aiohttp_client, app_paths):
    from brainhub.rawlog import append_entry
    save_map(app_paths.brain, default_map())
    e = append_entry(app_paths.brain, "orphan entry about servos", "pc")
    app = create_app(app_paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(app)
    assert app["store"].get_entry(e.id) is not None
    assert app["worker"].queue.qsize() == 1
    assert (await (await c.get("/api/state")).json())["log_error"] is None


async def test_foreign_host_forbidden(ready_client):
    r = await ready_client.get("/api/state", headers={"Host": "evil.example"})
    assert r.status == 403 and (await r.json())["error"] == "forbidden host"


async def test_foreign_origin_forbidden_and_log_unchanged(ready_client, app_paths):
    r = await ready_client.post("/api/entries", json={"text": "evil", "source": "pc"},
                                headers={"Origin": "http://evil.example"})
    assert r.status == 403 and (await r.json())["error"] == "forbidden origin"
    assert read_log(app_paths.brain) == []


async def test_same_origin_allowed(ready_client):
    origin = f"http://127.0.0.1:{ready_client.port}"
    r = await ready_client.post("/api/entries", json={"text": "fine", "source": "pc"},
                                headers={"Origin": origin})
    assert r.status == 201


async def test_text_plain_body_rejected(ready_client, app_paths):
    r = await ready_client.post("/api/entries", data=json.dumps({"text": "sneaky", "source": "pc"}),
                                headers={"Content-Type": "text/plain"})
    assert r.status == 415
    assert read_log(app_paths.brain) == []


async def test_ws_foreign_origin_refused(ready_client):
    from aiohttp import WSServerHandshakeError
    with pytest.raises(WSServerHandshakeError):
        await ready_client.ws_connect("/ws", origin="http://evil.example")


async def test_ask_rejects_cross_site_fetch(ready_client):
    r = await ready_client.get("/api/ask", params={"q": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status == 403


async def test_concurrent_captures_all_intact(ready_client, app_paths):
    import asyncio
    texts = [f"entry {i} " + "x" * (60 + i * 1000) for i in range(20)]
    rs = await asyncio.gather(*[ready_client.post("/api/entries", json={"text": t, "source": "pc"})
                                for t in texts])
    assert all(r.status == 201 for r in rs)
    got = [e.text for e in read_log(app_paths.brain)]
    assert sorted(got) == sorted(texts)


async def test_hierarchy_root_counts_item_once(ready_client, app):
    app["store"].create_item(kind="task", text="t", area="projects", project="robotarm",
                             person="ana", source_entry="e-1")
    h = await (await ready_client.get("/api/hierarchy")).json()
    assert h["items"] == 1


async def test_items_empty_filter_means_none(ready_client, app):
    app["store"].create_item(kind="task", text="t", area="projects", source_entry="e-1")
    assert len(await (await ready_client.get("/api/items", params={"status": "", "kind": ""})).json()) == 1


async def test_startup_backfills_entry_missing_from_store_and_fts(aiohttp_client, app_paths):
    from brainhub.rawlog import append_entry
    save_map(app_paths.brain, default_map())
    first = append_entry(app_paths.brain, "first entry about gears", "pc")
    app1 = create_app(app_paths, runner=make_runner(), start_workers=False)
    c1 = await aiohttp_client(app1)  # startup reindexes entry one
    assert app1["store"].fts_count() > 0
    await c1.close()
    second = append_entry(app_paths.brain, "second entry about zeppelins", "pc")
    app2 = create_app(app_paths, runner=make_runner(), start_workers=False)
    c2 = await aiohttp_client(app2)
    assert app2["store"].get_entry(second.id) is not None and first.id
    body = await (await c2.get("/api/ask", params={"q": "zeppelins"})).text()
    assert second.id in body


def test_binds_localhost_only():
    assert DEFAULT_HOST == "127.0.0.1"
