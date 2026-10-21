import pytest

from brainhub.config import load_settings, save_settings
from brainhub.server import create_app
from tests.test_server import MAP, make_runner


@pytest.fixture
def app(paths):
    app = create_app(paths, runner=make_runner(), start_workers=False)
    app["opened"] = opened = []
    app["opener"] = opened.append
    return app


@pytest.fixture
async def client(aiohttp_client, app):
    return await aiohttp_client(app)


async def test_open_targets(client, app, paths):
    s = load_settings(paths)
    r = await client.post("/api/open", json={"target": "app", "view": "agents"})
    assert r.status == 200
    assert await r.json() == {"ok": True, "url": f"http://127.0.0.1:{s['port']}/#/agents"}
    assert (await client.post("/api/open", json={"target": "fable_usage"})).status == 200
    assert (await client.post("/api/open", json={"target": "arm"})).status == 200
    assert app["opened"] == [f"http://127.0.0.1:{s['port']}/#/agents", s["fable_usage_url"], s["arm_url"]]
    assert (await client.post("/api/open", json={"target": "file:///c:/x"})).status == 400
    assert (await client.post("/api/open", json={"target": "app", "view": "../x"})).status == 400
    assert (await client.post("/api/open", json={})).status == 400
    assert len(app["opened"]) == 3


async def test_widget_endpoint_and_page(client, app, paths):
    assert (await client.post("/api/setup", json={"map": MAP, "backup_remote": None})).status == 200
    r = await client.get("/api/widget")
    assert r.status == 200
    body = await r.json()
    assert isinstance(body["synopsis"], str) and isinstance(body["arm"]["reachable"], bool)
    assert set(body["tiles"]) <= {"today", "usage", "agents", "people", "deadlines", "review", "projects", "arm"}
    page = await client.get("/widget")
    if (paths.web / "widget.html").exists():
        assert page.status == 200 and "text/html" in page.headers["Content-Type"]
    else:
        assert page.status == 404
