"""The hub uses the port it actually listens on, and refuses to start on a taken one (final review #3, #11)."""
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import atlas.__main__ as main_mod
from atlas.config import load_settings
from atlas.server import create_app
from tests.test_server import make_runner

APP_DIR = Path(__file__).resolve().parent.parent


def test_create_app_defaults_port_to_settings(paths):
    app = create_app(paths, runner=make_runner(), start_workers=False)
    assert app["port"] == load_settings(paths)["port"]


def test_run_server_stores_bound_port(tmp_path, monkeypatch):
    seen = {}
    root = tmp_path / "root"
    root.mkdir()  # a fresh root: the hub never creates one itself
    lock = root / "data" / "hub.lock"

    def fake_serve(app, host, port):
        seen.update(app=app, host=host, port=port, locked=lock.exists())
        return 0

    monkeypatch.setattr(main_mod, "serve", fake_serve)
    main_mod.run_server(["--root", str(root), "--port", "8123"])
    assert seen["port"] == 8123 and seen["host"] == "127.0.0.1"
    assert seen["app"]["port"] == 8123
    assert load_settings(seen["app"]["paths"])["port"] != 8123  # the settings port is not what it uses
    assert seen["locked"] and not lock.exists()  # held while serving, released on the way out


def test_port_with_a_listener_is_not_free():
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    try:
        assert not main_mod.port_free("127.0.0.1", held.getsockname()[1])
    finally:
        held.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX TIME_WAIT rules")
def test_port_free_after_previous_hub_closed_connections():
    """A hub restarted at once (exit 75) finds its port free although the old hub's closed
    connections are still in TIME_WAIT."""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as aiohttp binds
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = listener.accept()
    conn.close()  # the server closes first: its side of the connection goes to TIME_WAIT
    client.recv(1)
    client.close()
    listener.close()
    assert main_mod.port_free("127.0.0.1", port)


def test_second_hub_on_taken_port_exits_before_startup(tmp_path):
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    port = held.getsockname()[1]
    root = tmp_path / "root"
    try:
        r = subprocess.run([sys.executable, "-m", "atlas", "--root", str(root), "--port", str(port)],
                           cwd=APP_DIR, capture_output=True, text=True, timeout=60)
    finally:
        held.close()
    assert r.returncode != 0
    assert f"port {port}" in r.stderr and "already in use" in r.stderr
    assert not (root / "data").exists()  # no startup work (store, git, log scan) happened


@pytest.fixture
def app(paths):
    app = create_app(paths, runner=make_runner(), start_workers=False,
                     claude_settings_path=paths.root / "s" / "settings.json")
    app["port"] = 8123  # the hub listens here; the settings still say 8090
    app["opener"] = app.setdefault("opened", []).append
    return app


async def test_open_app_uses_bound_port(aiohttp_client, app):
    client = await aiohttp_client(app)
    r = await client.post("/api/open", json={"target": "app", "view": "agents"})
    assert (await r.json())["url"] == "http://127.0.0.1:8123/#/agents"
    assert app["opened"] == ["http://127.0.0.1:8123/#/agents"]


async def test_integration_install_uses_bound_port(aiohttp_client, app, paths):
    client = await aiohttp_client(app)
    r = await client.post("/api/integration/install", json={})
    assert r.status == 200 and (await r.json())["hooks_installed"] is True
    text = (paths.root / "s" / "settings.json").read_text(encoding="utf-8")
    data = json.loads(text)
    assert "8123" in data["statusLine"]["command"] and "8090" not in text
