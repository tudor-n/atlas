import subprocess

import pytest

from atlas import osdirs
from atlas.brainmap import load_map
from atlas.server import create_app
from tests.test_agents import make_repo, other_os_folder
from tests.test_server import MAP, make_runner


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


async def _setup_robotarm(client) -> str:
    """Set up a brain whose robotarm project has a folder on the other OS only; returns that folder."""
    other, there = other_os_folder()
    projects = [{"slug": "robotarm", "name": "RobotArm", "is_company": False, "os_folders": {other: [there]}}]
    r = await client.post("/api/setup", json={"map": {**MAP, "projects": projects}, "backup_remote": None})
    assert r.status == 200
    return there


async def test_api_link_adds_folder_and_commits(aiohttp_client, app, tmp_path):
    commits = []
    on_commit = app["on_commit"]
    app["on_commit"] = lambda sha: (commits.append(sha), on_commit(sha))
    client = await aiohttp_client(app)
    there = await _setup_robotarm(client)
    top = make_repo(tmp_path / "dev" / "RobotArmControl")
    (top / "src").mkdir()
    await client.post("/api/hooks/event", json=_start(top / "src"))
    row = (await (await client.get("/api/agents")).json())[0]
    assert row["mapped"] is False
    assert row["suggest"] == {"slug": "robotarm", "name": "RobotArm", "folder": str(top)}
    app["sent"].clear()
    commits.clear()
    r = await client.post("/api/agents/s1/link", data='{"slug": "robotarm"}', headers={"Content-Type": "text/plain"})
    assert r.status == 415
    r = await client.post("/api/agents/s1/link", json={"slug": "robotarm"})
    assert r.status == 200
    row = await r.json()
    assert (row["project"], row["project_name"], row["mapped"], row["suggest"]) == ("robotarm", "RobotArm", True, None)
    other, _ = other_os_folder()
    assert load_map(app["paths"].brain).projects[0]["os_folders"] == {other: [there], osdirs.os_name(): [str(top)]}
    head = subprocess.run(["git", "-C", str(app["paths"].brain), "log", "-1", "--format=%H %s"],
                          capture_output=True, text=True, check=True).stdout.split()
    assert head[1:] == ["map", "edit"] and commits == [head[0]]
    assert ("map.changed", {}) in app["sent"] and ("agents.changed", {}) in app["sent"]
    assert (await (await client.get("/api/agents")).json())[0]["mapped"] is True


async def test_api_link_409_without_suggestion(client, app, tmp_path):
    top = make_repo(tmp_path / "dev" / "RobotArmControl")
    elsewhere = tmp_path / "dev" / "Elsewhere"
    elsewhere.mkdir()
    await client.post("/api/hooks/event", json=_start(top))
    assert (await client.post("/api/agents/s1/link", json={"slug": "robotarm"})).status == 409  # no map yet
    await _setup_robotarm(client)
    await client.post("/api/hooks/event", json=_start(elsewhere, session_id="s2"))
    assert (await client.post("/api/agents/s2/link", json={"slug": "robotarm"})).status == 409
    assert (await client.post("/api/agents/s1/link", json={"slug": "web"})).status == 409
    assert (await client.post("/api/agents/s1/link", json={})).status == 400
    assert (await client.post("/api/agents/s1/link", json={"slug": 5})).status == 400
    assert (await client.post("/api/agents/nope/link", json={"slug": "robotarm"})).status == 404
    assert (await client.post("/api/agents/s1/link", json={"slug": "robotarm"})).status == 200
    assert (await client.post("/api/agents/s1/link", json={"slug": "robotarm"})).status == 409
    assert load_map(app["paths"].brain).projects[0]["os_folders"][osdirs.os_name()] == [str(top)]
