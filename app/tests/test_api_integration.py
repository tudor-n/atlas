import json
import sys

import pytest

from brainhub import claude_integration as ci
from brainhub import osdirs
from brainhub.server import create_app
from tests.test_server import make_runner


@pytest.fixture
async def client(aiohttp_client, paths, tmp_path):
    app = create_app(paths, runner=make_runner(), start_workers=False,
                     claude_settings_path=tmp_path / "s" / "settings.json")
    return await aiohttp_client(app)


async def test_integration_roundtrip(client, tmp_path):
    j = await (await client.get("/api/integration")).json()
    assert j["hooks_installed"] is False and j["last_hook_at"] is None and j["last_usage_at"] is None
    r = await client.post("/api/integration/install", json={})
    assert r.status == 200 and (await r.json())["hooks_installed"] is True
    assert (await (await client.get("/api/integration")).json())["hooks_installed"] is True
    r = await client.post("/api/integration/uninstall", json={})
    assert (await r.json())["hooks_installed"] is False


async def test_integration_state_goes_to_the_state_folder(client, paths, tmp_path):
    f = tmp_path / "s" / "settings.json"
    f.parent.mkdir()
    f.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo mine"}}), "utf-8")
    assert (await client.post("/api/integration/install", json={})).status == 200
    state = tmp_path / "state"  # BRAINHUB_STATE_DIR from conftest
    assert (state / ci.STATE_FILE).exists() and (state / ci.PASSTHROUGH_FILE).read_text("utf-8") == "echo mine"
    assert not any((paths.data / name).exists() for name in (ci.STATE_FILE, ci.PASSTHROUGH_FILE))
    assert (await client.post("/api/integration/uninstall", json={})).status == 200
    assert json.loads(f.read_text("utf-8"))["statusLine"] == {"type": "command", "command": "echo mine"}


@pytest.mark.parametrize("os_name", ["windows", "linux"])
async def test_integration_view_never_moves_legacy_files(client, paths, tmp_path, monkeypatch, os_name):
    """A GET must not move the passthrough file: the installed status line still names it."""
    monkeypatch.setattr(osdirs, "os_name", lambda: os_name)
    (paths.data / ci.STATE_FILE).write_text('{"previous_statusline": null, "installed_at": "2026-10-01T10:00:00"}')
    (paths.data / ci.PASSTHROUGH_FILE).write_text("echo old")
    assert (await client.get("/api/integration")).status == 200
    assert (paths.data / ci.PASSTHROUGH_FILE).exists() and (paths.data / ci.STATE_FILE).exists()
    assert not (tmp_path / "state" / ci.PASSTHROUGH_FILE).exists()


@pytest.mark.parametrize("os_name", ["windows", "linux"])
async def test_integration_install_adopts_legacy_on_windows_only(client, paths, tmp_path, monkeypatch, os_name):
    monkeypatch.setattr(osdirs, "os_name", lambda: os_name)
    (paths.data / ci.STATE_FILE).write_text('{"previous_statusline": null, "installed_at": "2026-10-01T10:00:00"}')
    (paths.data / ci.PASSTHROUGH_FILE).write_text("echo old")
    assert (await client.post("/api/integration/install", json={})).status == 200
    moved = os_name == "windows"
    assert (paths.data / ci.PASSTHROUGH_FILE).exists() is not moved
    assert (paths.data / ci.STATE_FILE).exists() is not moved


async def test_integration_install_in_runtime_mode_uses_stable_paths(client, tmp_path):
    state = tmp_path / "state"  # BRAINHUB_STATE_DIR from conftest
    (state / "runtime").mkdir(parents=True)
    (state / "runtime" / "current.json").write_text('{"slot": "a", "commit": "c", "deps": "d"}')
    stable = ci.stable_hook(state)
    stable.parent.mkdir()
    stable.write_bytes(ci.hook_script().read_bytes())
    r = await client.post("/api/integration/install", json={})
    assert r.status == 200 and (await r.json())["hooks_installed"] is True
    settings = json.loads((tmp_path / "s" / "settings.json").read_text("utf-8"))
    hook = settings["hooks"]["SessionStart"][0]["hooks"][0]
    assert hook["command"] == str(ci.base_python()) and hook["args"][2] == str(stable)
    view = await (await client.get("/api/integration")).json()
    assert view["hooks_installed"] and view["statusline_installed"] and view["port_matches"]
    assert view["paths_current"] is True
    r = await client.post("/api/integration/uninstall", json={})
    assert (await r.json())["hooks_installed"] is False
    assert json.loads((tmp_path / "s" / "settings.json").read_text("utf-8")) == {}


async def test_integration_invalid_json_is_400(client, tmp_path):
    f = tmp_path / "s" / "settings.json"
    f.parent.mkdir()
    f.write_text("{ bad")
    assert (await client.post("/api/integration/install", json={})).status == 400


async def test_integration_view_says_when_paths_are_old(client, tmp_path, monkeypatch):
    """Installed in dev mode, then `runtime init`: the view says the paths are old (Settings offers
    the reinstall). With the runtime's base interpreter gone, the view still answers, and install
    refuses with what to do."""
    assert (await client.post("/api/integration/install", json={})).status == 200
    assert (await (await client.get("/api/integration")).json())["paths_current"] is True
    state = tmp_path / "state"  # BRAINHUB_STATE_DIR from conftest
    (state / "runtime").mkdir(parents=True)
    (state / "runtime" / "current.json").write_text('{"slot": "a", "commit": "c", "deps": "d"}')
    stable = ci.stable_hook(state)
    stable.parent.mkdir()
    stable.write_bytes(ci.hook_script().read_bytes())
    view = await (await client.get("/api/integration")).json()
    assert view["hooks_installed"] and view["paths_current"] is False
    monkeypatch.setattr(sys, "_base_executable", str(tmp_path / "gone" / "python3"))
    r = await client.get("/api/integration")
    assert r.status == 200 and (await r.json())["paths_current"] is False
    r = await client.post("/api/integration/install", json={})
    assert r.status == 400 and "runtime init" in (await r.json())["error"]


async def test_last_usage_update_means_the_status_line(client):
    """The hub's own runs feed the meter too (ruling 21), but Settings' "Last usage update" must keep
    saying whether the status line integration is posting."""
    from brainhub.routes import record_usage
    payload = {"rate_limits": {"five_hour": {"used_percentage": 7, "resets_at": 4_000_000_000}}}
    await record_usage(client.server.app, payload)  # what a claude run does
    assert (await (await client.get("/api/integration")).json())["last_usage_at"] is None
    assert (await client.post("/api/usage", json=payload)).status == 204  # a status line post (stale here)
    assert (await (await client.get("/api/integration")).json())["last_usage_at"] is not None
