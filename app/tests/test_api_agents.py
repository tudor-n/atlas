import pytest

from brainhub.server import create_app
from tests.test_server import make_runner


@pytest.fixture
def app(paths):
    app = create_app(paths, runner=make_runner(), start_workers=False)
    app["sent"] = sent = []

    async def record(type_, data):
        sent.append((type_, data))

    app["broadcast"] = record
    return app


@pytest.fixture
async def client(aiohttp_client, app):
    return await aiohttp_client(app)


def _start(tmp_path, **kw):
    return {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(tmp_path),
            "source": "startup", "entrypoint": "cli", **kw}


async def test_hook_event_roundtrip(client, tmp_path):
    r = await client.post("/api/hooks/event", json=_start(tmp_path))
    assert r.status == 204 and await r.read() == b""
    rows = await (await client.get("/api/agents")).json()
    assert len(rows) == 1 and rows[0]["status"] == "idle"
    r = await client.patch("/api/agents/s1", json={"part": "wrist"})
    assert r.status == 200 and (await r.json())["part"] == "wrist"
    assert (await client.delete("/api/agents/s1")).status == 204
    assert await (await client.get("/api/agents")).json() == []
    assert (await client.patch("/api/agents/nope", json={"part": "x"})).status == 404


async def test_hook_event_rejects_garbage(client):
    assert (await client.post("/api/hooks/event", json=[])).status == 400
    assert (await client.post("/api/hooks/event", json={})).status == 400
    r = await client.post("/api/hooks/event", data="hi", headers={"Content-Type": "text/plain"})
    assert r.status == 415


async def test_hook_event_ignored_is_204(client, tmp_path):
    r = await client.post("/api/hooks/event", json=_start(tmp_path, entrypoint="sdk-cli"))
    assert r.status == 204 and await r.read() == b""
    assert await (await client.get("/api/agents")).json() == []


async def test_agents_changed_broadcast(client, app, tmp_path):
    await client.post("/api/hooks/event", json=_start(tmp_path))
    app["sent"].clear()
    prompt = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": str(tmp_path)}
    await client.post("/api/hooks/event", json=prompt)
    assert app["sent"] == [("agents.changed", {})]
    tool = {"hook_event_name": "PostToolUse", "session_id": "s1", "cwd": str(tmp_path)}
    await client.post("/api/hooks/event", json=tool)
    n = len(app["sent"])
    await client.post("/api/hooks/event", json=tool)
    assert len(app["sent"]) == n
