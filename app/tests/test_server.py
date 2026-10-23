import json
import sys
from datetime import datetime

import pytest

from brainhub.brainmap import default_map, save_map
from brainhub.rawlog import read_log
from brainhub.server import DEFAULT_HOST, create_app
from tests.fakes import FakeRepo, FakeRunner
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


async def test_setup_then_state(client, app):
    state = await (await client.get("/api/state")).json()
    assert state["setup_done"] is False and state["log_error"] is None
    assert state["brain_path"] == str(app["paths"].brain)
    assert state["os"] == ("windows" if sys.platform == "win32" else "linux")
    assert state["warnings"] == [] and app["root_status"].state == "fresh"
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


async def test_search_endpoint_returns_hits_without_claude(ready_client, app):
    eid = (await (await ready_client.post("/api/entries", json={"text": "wrist servo", "source": "pc"})).json())["entry_id"]
    r = await ready_client.get("/api/search", params={"q": "wrist"})
    assert r.status == 200
    hits = await r.json()
    assert hits[0]["ref"] == eid and app["runner"].calls == []
    assert (await ready_client.get("/api/search", params={"q": "  "})).status == 400
    assert (await ready_client.get("/api/search")).status == 400


async def test_notes_rejects_path_escape(ready_client):
    assert (await ready_client.get("/api/notes", params={"prefix": "../"})).status == 400


async def test_notes_lists_markdown_and_rejects_private_folders(ready_client, app_paths):
    folder = app_paths.brain / "projects" / "robotarm"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "notes.md").write_bytes("J5 servo é".encode("utf-8") + b"\xff")
    (folder / "skip.txt").write_text("no")
    got = await (await ready_client.get("/api/notes", params={"prefix": "projects/robotarm/"})).json()
    assert [n["path"] for n in got] == ["projects/robotarm/notes.md"] and "J5 servo" in got[0]["text"]
    for bad in ("log/", "_items/x", ".git/", "projects/../log/", "", "../../etc",
                "LOG/", "Log", "_Items/x"):
        assert (await ready_client.get("/api/notes", params={"prefix": bad})).status == 400, bad
    assert await (await ready_client.get("/api/notes", params={"prefix": "projects/none/"})).json() == []


async def test_backup_push_now_ignores_debounce(ready_client, app):
    app["backup"].repo = FakeRepo([True]); app["backup"].notify_commit()
    await ready_client.put("/api/settings", json={"backup_remote": "https://example.com/r.git", "backup_debounce_s": 9999})
    assert (await ready_client.post("/api/backup/push")).status == 200 and app["backup"].repo.pushes == ["https://example.com/r.git"]


async def test_code_paths_come_from_the_package_not_the_data_root(aiohttp_client, app_paths):
    import brainhub
    pkg = __import__("pathlib").Path(brainhub.__file__).resolve().parent
    assert not (app_paths.root / "app").exists()
    c = await aiohttp_client(create_app(app_paths, runner=make_runner(), start_workers=False))
    page = await (await c.get("/")).text()
    assert "/static/js/app.js" in page            # the real UI, not the placeholder
    assert (await c.get("/static/js/app.js")).status == 200
    assert app_paths.prompts == pkg / "prompts" and (app_paths.prompts / "filing.md").is_file()
    assert (app_paths.schemas / "filing.schema.json").is_file()
    assert not (app_paths.root / "app").exists()  # nothing created under the data root


async def _started(aiohttp_client, app_paths):
    app = create_app(app_paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(app)
    return app, c, await (await c.get("/api/state")).json()


def _setup_brain(app_paths):
    from brainhub.gitrepo import BrainRepo
    save_map(app_paths.brain, default_map())
    repo = BrainRepo(app_paths.brain)
    repo.init()
    repo.commit_all("setup")
    return repo


async def test_stale_index_lock_is_removed_at_startup(aiohttp_client, app_paths):
    repo = _setup_brain(app_paths)
    lock = app_paths.brain / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    (app_paths.brain / "general" / "mine.md").parent.mkdir(parents=True, exist_ok=True)
    (app_paths.brain / "general" / "mine.md").write_text("owner note", encoding="utf-8")
    app, c, state = await _started(aiohttp_client, app_paths)
    assert not lock.exists()
    assert any("index.lock" in e for e in state["startup_error"])
    assert not repo.is_dirty()  # the owner-edits commit after it worked


async def test_stale_ref_lock_is_removed_at_startup(aiohttp_client, app_paths):
    """A hard power-off can leave refs/heads/main.lock, which would block every commit."""
    repo = _setup_brain(app_paths)
    lock = app_paths.brain / ".git" / "refs" / "heads" / "main.lock"
    lock.write_text("", encoding="utf-8")
    (app_paths.brain / "general" / "mine.md").parent.mkdir(parents=True, exist_ok=True)
    (app_paths.brain / "general" / "mine.md").write_text("owner note", encoding="utf-8")
    app, c, state = await _started(aiohttp_client, app_paths)
    assert not lock.exists()
    assert any("brain/.git/refs/heads/main.lock" in e for e in state["startup_error"])
    assert not repo.is_dirty()


async def test_ghost_rows_ended_at_start(aiohttp_client, app_paths):
    """Sessions seen before this boot (of either OS) can't be running now: they are marked ended
    at startup instead of lingering as ghost "running" rows (dual-boot design §6.6)."""
    from brainhub import osdirs
    from brainhub.store import Store
    _setup_brain(app_paths)
    stamp = datetime.now().replace(microsecond=0).isoformat()
    row = {"cwd": "/w", "project": "w", "project_name": "w", "mapped": False, "branch": None,
           "title": None, "part_override": None, "entrypoint": "cli", "where_override": None,
           "status": "running", "note": None, "started_at": stamp, "status_since": stamp,
           "last_event_at": stamp, "ended_at": None}
    s = Store(app_paths.db)
    s.init_schema()
    s.upsert_agent({**row, "session_id": "ghost", "os": "windows", "boot": "old"})
    s.upsert_agent({**row, "session_id": "live", "os": osdirs.os_name(), "boot": osdirs.boot_id()})
    s.close()
    app, c, state = await _started(aiohttp_client, app_paths)
    assert app["store"].get_agent("ghost")["ended_at"] is not None
    assert app["store"].get_agent("live")["ended_at"] is None
    rows = {r["session_id"]: r for r in await (await c.get("/api/agents")).json()}
    assert rows["ghost"]["ended"] and rows["ghost"]["os"] == "windows" and not rows["live"]["ended"]


async def test_corrupt_settings_uses_defaults_keeps_file_and_capture_works(aiohttp_client, app_paths):
    _setup_brain(app_paths)
    app_paths.settings_file.write_text("{not json", encoding="utf-8")
    app, c, state = await _started(aiohttp_client, app_paths)
    assert state["settings"]["port"] == 8090
    assert any("settings.json" in e for e in state["startup_error"])
    assert app_paths.settings_file.read_text(encoding="utf-8") == "{not json"  # not overwritten
    r = await c.post("/api/entries", json={"text": "still captured", "source": "pc"})
    assert r.status == 201
    assert (await c.put("/api/settings", json={"nudge_days": 4})).status == 200
    assert json.loads(app_paths.settings_file.read_text(encoding="utf-8"))["nudge_days"] == 4


async def test_corrupt_items_json_reports_and_keeps_serving(aiohttp_client, app_paths):
    _setup_brain(app_paths)
    (app_paths.brain / "_items").mkdir(exist_ok=True)
    (app_paths.brain / "_items" / "items.json").write_text("[{broken", encoding="utf-8")
    app, c, state = await _started(aiohttp_client, app_paths)
    assert any("items.json" in e for e in state["startup_error"])
    assert (await c.get("/api/today")).status == 200
    assert (await c.post("/api/entries", json={"text": "x", "source": "pc"})).status == 201


async def test_damaged_index_db_is_moved_aside_and_rebuilt(aiohttp_client, app_paths):
    from brainhub.rawlog import append_entry
    _setup_brain(app_paths)
    e = append_entry(app_paths.brain, "the gearbox whines", "pc")
    app_paths.db.write_bytes(b"this is not a sqlite database" * 100)
    app, c, state = await _started(aiohttp_client, app_paths)
    assert any("damaged" in x for x in state["startup_error"])
    assert list(app_paths.data.glob("hub.db.damaged-*"))
    hits = await (await c.get("/api/search", params={"q": "gearbox"})).json()
    assert [h["ref"] for h in hits] == [e.id]
    assert (await c.post("/api/entries", json={"text": "y", "source": "pc"})).status == 201


async def test_truncated_log_file_keeps_the_good_entries(aiohttp_client, app_paths):
    from brainhub.rawlog import append_entry, log_path
    _setup_brain(app_paths)
    good = append_entry(app_paths.brain, "the good file mentions sprockets", "pc", datetime(2026, 10, 19, 9, 0, 0))
    bad = append_entry(app_paths.brain, "this one gets cut short", "pc", datetime(2026, 10, 20, 9, 0, 0))
    p = log_path(app_paths.brain, bad.ts)
    p.write_bytes(p.read_bytes()[:-20])
    before = p.read_bytes()
    app, c, state = await _started(aiohttp_client, app_paths)
    assert "2026-10-20.md" in state["log_error"] and "2026-10-19.md" not in state["log_error"]
    hits = await (await c.get("/api/search", params={"q": "sprockets"})).json()
    assert [h["ref"] for h in hits] == [good.id]
    assert p.read_bytes() == before
    assert state["startup_error"] is None


async def test_brain_clone_with_autocrlf_keeps_the_raw_log_readable(ready_client, app_paths, tmp_path):
    import subprocess
    from brainhub.rawlog import append_entry
    assert (app_paths.brain / ".gitattributes").read_text(encoding="utf-8") == "* -text\n"
    texts = ["line one\nline two\n", "trailing\n\n", "single"]
    for x in texts:
        append_entry(app_paths.brain, x, "pc")
    app = ready_client.server.app
    app["repo"].commit_all("entries")
    clone = tmp_path / "restored"
    subprocess.run(["git", "-c", "core.autocrlf=true", "clone", "-q", str(app_paths.brain), str(clone)],
                   check=True, capture_output=True)
    assert [e.text for e in read_log(clone)] == texts
    assert not any(b"\r\n" in p.read_bytes() for p in (clone / "log").rglob("*.md"))


async def test_startup_adds_missing_gitattributes_in_its_own_commit(aiohttp_client, app_paths):
    repo = _setup_brain(app_paths)
    assert not (app_paths.brain / ".gitattributes").exists()
    await _started(aiohttp_client, app_paths)
    assert (app_paths.brain / ".gitattributes").read_text(encoding="utf-8") == "* -text\n"
    assert repo._run("log", "-1", "--format=%s").stdout.strip() == "add .gitattributes"
    assert not repo.is_dirty()


async def _refs(c, q):
    return [h["ref"] for h in await (await c.get("/api/search", params={"q": q})).json()]


async def test_owner_hand_edit_is_searchable_at_once_and_old_text_gone(ready_client, app, app_paths):
    note = app_paths.brain / "personal" / "mine.md"
    note.write_text("my quokka plans", encoding="utf-8")
    eid = (await (await ready_client.post("/api/entries", json={"text": "x", "source": "pc"})).json())["entry_id"]
    await app["worker"].file_entry(eid)  # commits "owner edits" first
    assert "personal/mine.md" in await _refs(ready_client, "quokka")
    note.write_text("now about wombats", encoding="utf-8")
    eid2 = (await (await ready_client.post("/api/entries", json={"text": "y", "source": "pc"})).json())["entry_id"]
    await app["worker"].file_entry(eid2)
    assert "personal/mine.md" in await _refs(ready_client, "wombats")
    assert "personal/mine.md" not in await _refs(ready_client, "quokka")
    note.unlink()
    eid3 = (await (await ready_client.post("/api/entries", json={"text": "z", "source": "pc"})).json())["entry_id"]
    await app["worker"].file_entry(eid3)
    assert "personal/mine.md" not in await _refs(ready_client, "wombats")  # deleted: rows dropped


async def test_confirm_commit_reindexes_swept_owner_edits(ready_client, app, app_paths):
    [p] = app["store"].add_proposals("e-1", [("item", {"kind": "task", "text": "t", "area": "general"}, [])])
    (app_paths.brain / "general" / "idea.md").write_text("a zeppelin idea", encoding="utf-8")
    assert (await ready_client.post("/api/proposals/confirm", json={"ids": [p.id]})).status == 200
    assert "general/idea.md" in await _refs(ready_client, "zeppelin")


async def test_startup_rebuilds_notes_and_skips_dot_folders(aiohttp_client, app_paths):
    _setup_brain(app_paths)
    (app_paths.brain / "general").mkdir(exist_ok=True)
    (app_paths.brain / "general" / "n.md").write_text("old aardvark text", encoding="utf-8")
    app1, c1, _ = await _started(aiohttp_client, app_paths)
    assert "general/n.md" in await _refs(c1, "aardvark")
    await c1.close()
    (app_paths.brain / "general" / "n.md").write_text("fresh narwhal text", encoding="utf-8")
    (app_paths.brain / ".obsidian").mkdir()
    (app_paths.brain / ".obsidian" / "x.md").write_text("narwhal workspace", encoding="utf-8")
    app2, c2, state = await _started(aiohttp_client, app_paths)
    assert await _refs(c2, "narwhal") == ["general/n.md"]
    assert await _refs(c2, "aardvark") == []
    assert state["log_error"] is None


def _delete_db(app_paths):
    for suffix in ("", "-wal", "-shm"):
        f = app_paths.db.with_name(app_paths.db.name + suffix)
        if f.exists():
            f.unlink()


async def test_rebuilt_index_marks_filed_entries_done_and_keeps_pending_proposals(
        ready_client, app, aiohttp_client, app_paths):
    eid = (await (await ready_client.post("/api/entries", json={"text": "wrist servo", "source": "pc"})).json())["entry_id"]
    await app["worker"].file_entry(eid)
    before = app["store"].get_filing(eid)
    pending = [p.id for p in app["store"].list_proposals(entry_id=eid)]
    assert before.status == "done" and before.commit and pending
    await ready_client.close()
    _delete_db(app_paths)
    app2, c2, state = await _started(aiohttp_client, app_paths)
    f = app2["store"].get_filing(eid)
    assert f.status == "done" and f.commit == before.commit
    assert f.synopsis and before.synopsis.startswith(f.synopsis)
    assert app2["worker"].queue.qsize() == 0          # never re-filed
    assert [p.id for p in app2["store"].list_proposals(entry_id=eid)] == pending
    card = await (await c2.get(f"/api/filings/{eid}")).json()
    assert card["filing"]["status"] == "done" and len(card["proposals"]) == len(pending)
    assert state["startup_error"] is None


async def test_many_unfiled_entries_wait_for_retry_few_are_queued(aiohttp_client, app_paths, tmp_path):
    from brainhub.filing import UNFILED_AT_STARTUP
    from brainhub.rawlog import append_entry
    _setup_brain(app_paths)
    ids = [append_entry(app_paths.brain, f"backlog {i}", "pc").id for i in range(11)]
    app, c, _ = await _started(aiohttp_client, app_paths)
    assert app["worker"].queue.qsize() == 0
    assert all(app["store"].get_filing(i).status == "failed" for i in ids)
    assert app["store"].get_filing(ids[0]).error == UNFILED_AT_STARTUP
    r = await c.post(f"/api/filings/{ids[0]}/retry")
    assert r.status == 202 and app["worker"].queue.qsize() == 1


async def test_ten_unfiled_entries_are_queued(aiohttp_client, app_paths):
    from brainhub.rawlog import append_entry
    _setup_brain(app_paths)
    for i in range(10):
        append_entry(app_paths.brain, f"backlog {i}", "pc")
    app, c, _ = await _started(aiohttp_client, app_paths)
    assert app["worker"].queue.qsize() == 10


def _map_with(**extra):
    m = json.loads(json.dumps(MAP))
    for k, v in extra.items():
        m[k] = v
    return m


async def test_map_edit_refuses_to_strand_items_or_notes(ready_client, app, app_paths):
    two = _map_with(projects=MAP["projects"] + [{"slug": "spare", "name": "Spare", "is_company": False, "folders": []}],
                    courses=[{"slug": "fizica", "name": "Fizica"}])
    assert (await ready_client.put("/api/map", json=two)).status == 200
    app["store"].create_item(kind="task", text="t", area="projects", project="robotarm", source_entry="e-1")
    app["store"].update_item(app["store"].list_items()[0].id, {"status": "done"})  # any status counts
    without_robotarm = _map_with(projects=[two["projects"][1]], courses=two["courses"])
    r = await ready_client.put("/api/map", json=without_robotarm)
    assert r.status == 400 and (await r.json())["error"] == \
        "project 'robotarm' has notes or items; it can't be removed or renamed yet"
    (app_paths.brain / "uni" / "fizica").mkdir(parents=True, exist_ok=True)
    (app_paths.brain / "uni" / "fizica" / "curs.md").write_text("notes", encoding="utf-8")
    renamed = _map_with(projects=two["projects"], courses=[{"slug": "fizica-1", "name": "Fizica"}])
    r = await ready_client.put("/api/map", json=renamed)
    assert r.status == 400 and "course 'fizica'" in (await r.json())["error"]
    empty_removed = _map_with(projects=MAP["projects"], courses=two["courses"])  # 'spare' has nothing
    assert (await ready_client.put("/api/map", json=empty_removed)).status == 200
    assert [p["slug"] for p in (await (await ready_client.get("/api/map")).json())["projects"]] == ["robotarm"]


async def test_hierarchy_counts_items_filed_under_a_course(ready_client, app):
    m = _map_with(courses=[{"slug": "fizica", "name": "Fizica"}])
    assert (await ready_client.put("/api/map", json=m)).status == 200
    app["store"].create_item(kind="deadline", text="lab", area="uni", project="fizica", source_entry="e-1")
    h = await (await ready_client.get("/api/hierarchy")).json()
    uni = next(c for c in h["children"] if c["slug"] == "uni")
    assert next(c for c in uni["children"] if c["slug"] == "fizica")["items"] == 1


async def test_setup_with_a_bad_remote_writes_nothing_then_a_good_one_works(client, app_paths):
    r = await client.post("/api/setup", json={"map": MAP, "backup_remote": "htps://typo"})
    assert r.status == 400 and "backup_remote" in (await r.json())["error"]
    assert not (app_paths.brain / "map.json").exists() and not (app_paths.brain / ".git").exists()
    assert not app_paths.settings_file.exists()
    r = await client.post("/api/setup", json={"map": MAP, "backup_remote": "https://github.com/me/brain.git"})
    assert r.status == 200
    assert (await (await client.get("/api/settings")).json())["backup_remote"] == "https://github.com/me/brain.git"


async def test_setup_with_a_bad_map_writes_nothing(client, app_paths):
    bad = _map_with(projects=[{"slug": "log", "name": "Log", "is_company": False, "folders": []}])
    assert (await client.post("/api/setup", json={"map": bad})).status == 400
    assert not (app_paths.brain / "map.json").exists() and not (app_paths.brain / ".git").exists()


async def test_failed_filings_endpoint_lists_every_failure(ready_client, app):
    from brainhub.models import Filing
    ids = []
    for i in range(3):
        r = await ready_client.post("/api/entries", json={"text": f"entry {i}", "source": "pc"})
        ids.append((await r.json())["entry_id"])
    app["store"].upsert_filing(Filing(ids[0], "failed", error="claude timed out"))
    rows = await (await ready_client.get("/api/filings", params={"status": "failed"})).json()
    assert rows == [{"entry_id": ids[0], "error": "claude timed out",
                     "ts": app["store"].get_entry(ids[0]).ts.isoformat(), "text": "entry 0"}]
    assert (await ready_client.get("/api/filings", params={"status": "bogus"})).status == 400
    app["store"].add_proposals(ids[1], [("item", {"kind": "task", "text": "t", "area": "general"}, [])])
    groups = await (await ready_client.get("/api/proposals")).json()
    assert [(g["entry_id"], g["text"]) for g in groups] == [(ids[1], "entry 1")]


async def test_correct_marks_the_filing_queued_at_once(ready_client, app):
    import asyncio
    eid = (await (await ready_client.post("/api/entries", json={"text": "x", "source": "pc"})).json())["entry_id"]
    await app["worker"].file_entry(eid)
    gate = asyncio.Event()

    async def held(entry_id, text, *, queued=False):
        await gate.wait()

    app["worker"].correct = held
    r = await ready_client.post(f"/api/filings/{eid}/correct", json={"text": "it is the 12th"})
    assert r.status == 202
    card = await (await ready_client.get(f"/api/filings/{eid}")).json()
    assert card["filing"]["status"] == "queued"
    assert (await ready_client.post(f"/api/filings/{eid}/correct", json={"text": "again"})).status == 409
    assert (await ready_client.post(f"/api/filings/{eid}/retry")).status == 409
    gate.set()


async def test_usage_endpoints(client):
    payload = {"rate_limits": {"five_hour": {"used_percentage": 62.4, "resets_at": 4_000_000_000}}}
    r = await client.post("/api/usage", json=payload)
    assert r.status == 204
    got = await (await client.get("/api/usage")).json()
    assert got["five_hour"]["used"] == 62.4 and got["warn_pct"] == 80
    assert (await client.post("/api/usage", json={"rate_limits": 3})).status == 400
    r = await client.post("/api/usage", data="{}", headers={"Content-Type": "text/plain"})
    assert r.status == 415


async def test_own_claude_runs_feed_the_usage_meter(app_paths, aiohttp_client):
    """The hub's filings and answers use the plan too; their rate_limit_event reaches /api/usage."""
    app = create_app(app_paths, start_workers=False)  # the real ClaudeRunner, never started here
    client = await aiohttp_client(app)
    info = {"unifiedWindows": {"five_hour": {"utilization": 0.07, "resetsAt": 4_000_000_000}}}
    async with client.ws_connect("/ws") as ws:
        await app["runner"].on_rate_limit(info)
        msg = await ws.receive_json(timeout=5)
    assert msg["type"] == "usage.changed" and msg["data"]["five_hour"]["used"] == 7.0
    assert (await (await client.get("/api/usage")).json())["five_hour"]["used"] == 7.0
    await app["runner"].on_rate_limit({"status": "allowed"})  # no windows: nothing to record


async def test_filing_card_carries_live_progress(ready_client, app):
    eid = (await (await ready_client.post("/api/entries", json={"text": "x", "source": "pc"})).json())["entry_id"]
    app["worker"].progress[eid] = {"started": "2026-10-23T10:00:00", "step": "reading a.md"}
    card = await (await ready_client.get(f"/api/filings/{eid}")).json()
    assert card["progress"] == {"started": "2026-10-23T10:00:00", "step": "reading a.md"}
    del app["worker"].progress[eid]
    assert (await (await ready_client.get(f"/api/filings/{eid}")).json())["progress"] is None
