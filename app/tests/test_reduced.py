"""Reduced and read-only modes (dual-boot design §6.5 items 2 and 3, Plan decision P8): a missing,
foreign, newer or read-only root never gets a brain created, never has hub.db moved aside, and
never loses a capture; once the root is usable again the hub stops with exit code 75."""
import _thread
import asyncio
import os
import socket
import sqlite3
import sys
import threading
from datetime import datetime

import pytest

import brainhub.__main__ as main_mod
from brainhub import holding, osdirs, rootguard
from brainhub import store as store_mod
from brainhub.brainmap import default_map, save_map
from brainhub.config import Paths
from brainhub.filing import MAX_AUTO_REQUEUE
from brainhub.rawlog import read_log
from brainhub.rootguard import check_root
from brainhub.server import _corrupt, create_app, create_reduced_app
from brainhub.store import Store
from tests.test_server import MAP, make_runner

not_superuser = pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                                   reason="chmod doesn't stop root (and is POSIX)")


def _brain(tmp_path) -> Paths:
    """A marked brain with a map, outside tmp_path itself (the tests' state folder lives there)."""
    paths = Paths(tmp_path / "BrainHub")
    save_map(paths.brain, default_map())
    rootguard.create_marker(paths)
    return paths


def _reduced(paths, **kw):
    return create_reduced_app(paths, check_root(paths), **kw)


def _snapshot(root):
    """Every path under the root, with size and mtime for files: any write shows up here."""
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.stat()
        out[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns) if p.is_file() else "dir"
    return out


def _state_dir():
    return osdirs.state_dir()


async def _until(cond, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


# -- reduced app ---------------------------------------------------------------
async def test_missing_root_serves_banner_and_holds_capture(aiohttp_client, tmp_path):
    paths = Paths(tmp_path / "drive" / "BrainHub")
    status = check_root(paths)
    assert status.state == "missing"
    c = await aiohttp_client(create_reduced_app(paths, status))
    state = await (await c.get("/api/state")).json()
    assert state["setup_done"] is None  # the UI checks drive first: setup is never offered
    assert state["drive"] == {"state": "missing", "message": status.message, "held": 0}
    assert state["os"] == osdirs.os_name() and state["warnings"] == []
    r = await c.post("/api/entries", json={"text": "don't lose me\r\n", "source": "widget"})
    assert r.status == 200
    body = await r.json()
    assert body["held"] is True
    [held] = holding.held(_state_dir())
    assert held["id"] == body["entry_id"] and held["text"] == "don't lose me\r\n" and held["source"] == "widget"
    assert (await (await c.get("/api/state")).json())["drive"]["held"] == 1
    r = await c.get("/api/today")
    assert r.status == 503
    assert await r.json() == {"error": status.message, "drive": {**state["drive"], "held": 1}}
    for method, path in (("GET", "/api/entries"), ("POST", "/api/setup"), ("PUT", "/api/settings"),
                         ("POST", "/api/proposals/confirm"), ("GET", "/api/agents"), ("POST", "/api/state")):
        assert (await c.request(method, path, json={})).status == 503, (method, path)
    # The usual JSON and local_only rules.
    assert (await c.post("/api/entries", data="x", headers={"Content-Type": "text/plain"})).status == 415
    assert (await c.post("/api/entries", json={"text": " ", "source": "pc"})).status == 400
    assert (await c.post("/api/entries", json={"text": "x", "source": "tablet"})).status == 400
    assert (await c.get("/api/state", headers={"Host": "evil.example"})).status == 403
    w = await (await c.get("/api/widget")).json()
    assert w["synopsis"] == status.message and w["drive"]["held"] == 1 and w["tiles"] == []
    assert (await c.get("/")).status == 200 and (await c.get("/widget")).status == 200
    assert (await c.get("/static/js/app.js")).status == 200
    assert len(holding.held(_state_dir())) == 1
    assert not (tmp_path / "drive").exists()  # never created


async def test_hook_events_ignored_in_reduced_mode(aiohttp_client, tmp_path):
    paths = Paths(tmp_path / "missing")
    c = await aiohttp_client(_reduced(paths))
    event = {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path)}
    assert (await c.post("/api/hooks/event", json=event)).status == 204
    assert (await c.post("/api/usage", json={"rate_limits": {}})).status == 204
    assert (await c.post("/api/hooks/event", data="{}", headers={"Content-Type": "text/plain"})).status == 415
    assert not paths.root.exists() and not _state_dir().exists()  # nothing written anywhere


async def test_open_still_works_in_reduced_mode(aiohttp_client, tmp_path):
    app = _reduced(Paths(tmp_path / "missing"))
    app["port"], app["opener"] = 8123, app.setdefault("opened", []).append
    c = await aiohttp_client(app)
    r = await c.post("/api/open", json={"target": "app"})
    assert r.status == 200 and app["opened"] == ["http://127.0.0.1:8123/"]


async def test_foreign_root_is_never_written(aiohttp_client, tmp_path):
    paths = Paths(tmp_path / "BrainHub")
    paths.brain.mkdir(parents=True)
    (paths.brain / "notes.md").write_text("someone else's notes", encoding="utf-8")
    before = _snapshot(paths.root)
    app = _reduced(paths, recheck_interval=0.02)
    assert app["root_status"].state == "foreign"
    c = await aiohttp_client(app)
    assert (await c.post("/api/entries", json={"text": "held", "source": "pc"})).status == 200
    assert (await c.get("/api/map")).status == 503
    await asyncio.sleep(0.1)  # a few re-checks
    assert app["exit_code"] == 0 and _snapshot(paths.root) == before


async def test_newer_root_is_not_watched(aiohttp_client, tmp_path):
    paths = _brain(tmp_path)
    marker = paths.data / rootguard.MARKER_NAME
    marker.write_text('{"id": "x", "brain_format": 99}', encoding="utf-8")
    app = _reduced(paths, recheck_interval=0.02)
    assert app["root_status"].state == "newer"
    await aiohttp_client(app)
    # Newer data never turns older while this code runs; an update restarts the hub anyway.
    assert app["tasks"] == []


# -- recovery (P8) -------------------------------------------------------------
async def test_recovery_requests_exit_75(aiohttp_client, tmp_path):
    paths = Paths(tmp_path / "BrainHub")
    app = _reduced(paths, recheck_interval=0.02)
    await aiohttp_client(app)
    await asyncio.sleep(0.1)
    assert app["exit_code"] == 0 and not app["stop"].is_set()  # still missing: keeps waiting
    save_map(paths.brain, default_map())  # the drive is back, with the brain on it
    rootguard.create_marker(paths)
    assert await _until(app["stop"].is_set)
    assert app["exit_code"] == 75


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_stops_with_exit_75_when_the_root_returns(tmp_path):
    paths = Paths(tmp_path / "BrainHub")
    app = _reduced(paths, recheck_interval=0.02)
    save_map(paths.brain, default_map())
    rootguard.create_marker(paths)
    guard = threading.Timer(20, _thread.interrupt_main)  # never hang the suite: Ctrl+C after 20 s
    guard.start()
    try:
        code = main_mod.serve(app, "127.0.0.1", _free_port())
    finally:
        guard.cancel()
    assert code == 75


@pytest.fixture
def no_server(monkeypatch):
    monkeypatch.setattr(main_mod, "port_free", lambda host, port: True)
    served = []

    def serve(app, host, port):
        served.append(app)
        return app["exit_code"]
    monkeypatch.setattr(main_mod, "serve", serve)
    return served


def test_run_server_exits_75_after_recovery(tmp_path, no_server, capsys, monkeypatch):
    paths = Paths(tmp_path / "BrainHub")
    real = main_mod.create_reduced_app

    def reduced(p, status):
        app = real(p, status)
        app["exit_code"] = 75  # as the watch sets it once the root is back
        return app
    monkeypatch.setattr(main_mod, "create_reduced_app", reduced)
    with pytest.raises(SystemExit) as e:
        main_mod.run_server(["--root", str(paths.root), "--port", "8123"])
    assert e.value.code == 75
    out = capsys.readouterr().out
    assert "reduced mode" in out and "start it again" in out


# -- read-only root ----------------------------------------------------------
async def _make_brain_with_proposals(aiohttp_client, paths):
    paths.root.mkdir()
    app = create_app(paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(app)
    assert (await c.post("/api/setup", json={"map": MAP})).status == 200
    eid = (await (await c.post("/api/entries", json={"text": "fix the gripper", "source": "pc"})).json())["entry_id"]
    await app["worker"].file_entry(eid)
    ids = [p["id"] for p in (await (await c.get(f"/api/filings/{eid}")).json())["proposals"]]
    assert ids
    await c.close()  # the store closes: WAL checkpointed, as after a clean stop
    return eid, ids


@not_superuser
async def test_readonly_root_keeps_hub_db(aiohttp_client, tmp_path):
    paths = Paths(tmp_path / "BrainHub")
    eid, ids = await _make_brain_with_proposals(aiohttp_client, paths)
    before, db = _snapshot(paths.root), paths.db.stat()
    try:
        paths.root.chmod(0o555)
        app = create_app(paths, runner=make_runner(), start_workers=False)
        assert app["root_status"].state == "readonly" and app["store"].readonly
        c = await aiohttp_client(app)
        today = await c.get("/api/today")
        assert today.status == 200 and (await today.json())["review"]["proposals"] == len(ids)
        assert (await (await c.get("/api/entries")).json())[0]["id"] == eid
        assert (await c.get("/api/agents")).status == 200 and (await c.get("/api/widget")).status == 200
        state = await (await c.get("/api/state")).json()
        assert state["setup_done"] is True and state["drive"]["state"] == "readonly"
        r = await c.post("/api/proposals/confirm", json={"ids": ids})
        assert r.status == 503 and (await r.json())["error"] == app["root_status"].message
        for method, path in (("PUT", "/api/settings"), ("PATCH", "/api/items/x"),
                             ("POST", f"/api/filings/{eid}/retry"), ("POST", "/api/integration/install")):
            assert (await c.request(method, path, json={})).status == 503, (method, path)
        r = await c.post("/api/entries", json={"text": "typed while read-only", "source": "pc"})
        assert r.status == 200 and (await r.json())["held"] is True
        event = {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path)}
        assert (await c.post("/api/hooks/event", json=event)).status == 204
        assert (await (await c.get("/api/state")).json())["drive"]["held"] == 1
        await c.close()
        after = paths.db.stat()
        assert (after.st_size, after.st_mtime_ns) == (db.st_size, db.st_mtime_ns)
        assert _snapshot(paths.root) == before  # nothing moved, created or changed under the root
    finally:
        paths.root.chmod(0o755)
    assert len(holding.held(_state_dir())) == 1


@not_superuser
async def test_readonly_recovery_requests_exit_75(aiohttp_client, tmp_path):
    paths = _brain(tmp_path)
    s = Store(paths.db)
    s.init_schema()
    s.close()
    try:
        paths.root.chmod(0o555)
        app = create_app(paths, runner=make_runner(), start_workers=False, recheck_interval=0.02)
        assert app["root_status"].state == "readonly"
        await aiohttp_client(app)
        await asyncio.sleep(0.1)
        assert app["exit_code"] == 0
    finally:
        paths.root.chmod(0o755)
    assert await _until(app["stop"].is_set)
    assert app["exit_code"] == 75


@not_superuser
async def test_readonly_root_without_index_serves_reduced_app(tmp_path, no_server):
    paths = _brain(tmp_path)  # no hub.db: it can't be created on a read-only root
    try:
        paths.root.chmod(0o555)
        main_mod.run_server(["--root", str(paths.root), "--port", "8123"])
    finally:
        paths.root.chmod(0o755)
    [app] = no_server
    assert "store" not in app and app["root_status"].state == "readonly"
    assert "can't be read" in app["root_status"].message
    assert not paths.db.exists() and not (paths.data / rootguard.LOCK_NAME).exists()


def test_readonly_store_never_writes(tmp_path):
    db = tmp_path / "hub.db"
    s = Store(db)
    s.init_schema()
    s.close()
    before = db.read_bytes()
    ro = Store(db, readonly=True)
    ro.init_schema()  # only checks
    ro.delete_agents(["s1"])  # pruning on a read: skipped
    with pytest.raises(sqlite3.OperationalError):
        ro.add_entry(_entry())
    ro.close()
    assert db.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir()) == ["hub.db"]


def test_readonly_store_refuses_a_schema_it_would_migrate(tmp_path, monkeypatch):
    db = tmp_path / "hub.db"
    s = Store(db)
    s.init_schema()
    s.close()
    sqlite3.connect(tmp_path / "empty.db").close()  # an empty file: version 0, no tables
    monkeypatch.setattr(store_mod, "SCHEMA_VERSION", store_mod.SCHEMA_VERSION + 1)  # the code moved on
    for path in (db, tmp_path / "empty.db"):
        before = path.read_bytes()
        ro = Store(path, readonly=True)
        with pytest.raises(sqlite3.OperationalError, match="writable root"):
            ro.init_schema()  # migrating writes: refused, so the hub serves the reduced app
        ro.close()
        assert path.read_bytes() == before


def _entry():
    from brainhub.models import Entry
    return Entry("e-20261020-090000-abcd", datetime(2026, 10, 20, 9, 0, 0), "pc", "x")


# -- hub.db damage handler -----------------------------------------------------
def test_locked_db_is_not_moved_aside(tmp_path, monkeypatch, no_server):
    paths = _brain(tmp_path)
    s = Store(paths.db)
    s.init_schema()
    s.close()
    monkeypatch.setattr(store_mod, "BUSY_TIMEOUT", 0.1)
    other = sqlite3.connect(paths.db, timeout=0.1)
    other.execute("BEGIN EXCLUSIVE")  # another process holds the write lock
    try:
        before = paths.db.read_bytes()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            create_app(paths, runner=make_runner(), start_workers=False)
        with pytest.raises(SystemExit) as e:
            main_mod.run_server(["--root", str(paths.root), "--port", "8123"])
        assert "can't be opened" in str(e.value.code) and "nothing was moved" in str(e.value.code)
        assert paths.db.read_bytes() == before
        assert not list(paths.data.glob("hub.db.damaged-*"))
        assert not (paths.data / rootguard.LOCK_NAME).exists() and no_server == []
    finally:
        other.rollback()
        other.close()


async def test_quick_check_damage_is_moved_aside(aiohttp_client, tmp_path, monkeypatch):
    paths = _brain(tmp_path)
    s = Store(paths.db)
    s.init_schema()
    s.close()
    monkeypatch.setattr(Store, "quick_check", lambda self: ["row 3 missing from index entries_ts"])
    app = create_app(paths, runner=make_runner(), start_workers=False)
    assert any("quick_check" in x and "moved it to" in x for x in app["startup_errors"])
    assert list(paths.data.glob("hub.db.damaged-*"))


def test_only_real_corruption_counts_as_damage():
    for msg in ("database disk image is malformed", "file is not a database",
                "file is encrypted or is not a database", "malformed database schema (x)"):
        assert _corrupt(sqlite3.DatabaseError(msg)), msg
    for msg in ("database is locked", "attempt to write a readonly database", "unable to open database file",
                "disk I/O error"):
        assert not _corrupt(sqlite3.OperationalError(msg)), msg


# -- flush at an ok start ------------------------------------------------------
async def test_held_captures_flushed_and_queued_at_ok_start(aiohttp_client, tmp_path):
    paths = Paths(tmp_path / "BrainHub")
    paths.root.mkdir()
    app = create_app(paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(app)
    assert (await c.post("/api/setup", json={"map": MAP})).status == 200
    await c.close()
    # More than the cap on never-filed entries: held captures are filed all the same.
    held = [holding.hold(_state_dir(), f"held {i}", "widget", datetime(2026, 10, 20, 9, 0, i))
            for i in range(MAX_AUTO_REQUEUE + 1)]
    app = create_app(paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(app)
    state = await (await c.get("/api/state")).json()
    assert state["startup_error"] is None and state["drive"] is None
    assert [e.id for e in read_log(paths.brain)] == [h["id"] for h in held]
    assert holding.held(_state_dir()) == []
    for h in held:
        assert app["store"].get_entry(h["id"]).text == h["text"]
        assert app["store"].get_filing(h["id"]).status == "queued"
    queued = [app["worker"].queue.get_nowait() for _ in range(app["worker"].queue.qsize())]
    assert queued == [h["id"] for h in held]
    hits = await (await c.get("/api/search", params={"q": "held"})).json()
    assert hits and all(h["ref"] in {x["id"] for x in held} for h in hits)  # indexed for recall
