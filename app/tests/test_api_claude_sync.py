"""GET /api/claude-sync and PUT /api/claude-sync/per-os-keys. The state folder and the shared store
are conftest's temp folders (BRAINHUB_STATE_DIR, BRAINHUB_SHARED_DIR)."""
import json

import pytest

from brainhub import claude_sync as cs
from brainhub import osdirs
from brainhub.server import create_app
from tests.test_server import make_runner


@pytest.fixture
async def client(aiohttp_client, paths, tmp_path):
    app = create_app(paths, runner=make_runner(), start_workers=False,
                     claude_settings_path=tmp_path / "s" / "settings.json")
    return await aiohttp_client(app)


async def test_view_before_any_run_without_a_store(client):
    j = await (await client.get("/api/claude-sync")).json()
    assert j["summary"] is None and j["available"] is False and "not found" in j["reason"]
    assert j["per_os_keys"] == {"default": list(cs.DEFAULT_PER_OS_KEYS), "custom": None}
    assert {"hooks", "statusLine"} <= set(j["per_os_keys"]["default"])


async def test_view_shows_the_last_summary(client):
    cs.init_store(cs.default_shared_dir())
    state = osdirs.state_dir()
    state.mkdir(parents=True)
    (state / cs.SUMMARY_FILE).write_text(json.dumps({"ran_at": "2026-10-22T09:00:00", "available": True}), "utf-8")
    j = await (await client.get("/api/claude-sync")).json()
    assert j["summary"] == {"ran_at": "2026-10-22T09:00:00", "available": True}
    assert j["available"] is True and j["per_os_keys"]["custom"] == []


async def test_put_per_os_keys(client):
    shared = cs.init_store(cs.default_shared_dir())
    (shared / cs.STORE_SETTINGS).write_text(json.dumps({"settings": {
        "theme": {"value": "dark", "changed_at": "2026-10-22T09:00:00", "deleted": False},
        "model": {"value": "opus", "changed_at": "2026-10-22T09:00:00", "deleted": False}}}), "utf-8")
    r = await client.put("/api/claude-sync/per-os-keys", json={"keys": ["theme", " hooks ", "theme", ""]})
    assert r.status == 200
    assert (await r.json())["per_os_keys"]["custom"] == ["theme"]
    saved = json.loads((shared / cs.STORE_SETTINGS).read_text("utf-8"))
    assert saved["per_os_keys"] == ["theme"] and set(saved["settings"]) == {"model"}  # theme no longer shared
    assert b"\r\n" not in (shared / cs.STORE_SETTINGS).read_bytes()


async def test_put_per_os_keys_needs_the_store(client):
    r = await client.put("/api/claude-sync/per-os-keys", json={"keys": ["theme"]})
    assert r.status == 409 and "not found" in (await r.json())["error"]
    assert not cs.default_shared_dir().exists()  # never created


async def test_put_per_os_keys_refused_while_a_sync_runs(client):
    cs.init_store(cs.default_shared_dir())
    fd = cs.take_lock(osdirs.state_dir())
    try:
        r = await client.put("/api/claude-sync/per-os-keys", json={"keys": ["theme"]})
        assert r.status == 409 and "running" in (await r.json())["error"]
    finally:
        cs.release_lock(fd)


async def test_put_per_os_keys_validates(client):
    cs.init_store(cs.default_shared_dir())
    r = await client.put("/api/claude-sync/per-os-keys", json={"keys": "theme"})
    assert r.status == 400
    r = await client.put("/api/claude-sync/per-os-keys", json={"keys": ["has space"]})
    assert r.status == 400
    r = await client.put("/api/claude-sync/per-os-keys", data='{"keys": []}')
    assert r.status == 415
    r = await client.put("/api/claude-sync/per-os-keys", json={"keys": []},
                         headers={"Origin": "http://evil.example"})
    assert r.status == 403
