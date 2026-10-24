"""The voice in the hub, end to end through the API, with the real worker process in fake mode."""
import json
import os
import time
from pathlib import Path

import pytest

from atlas import voice_files as vf
from atlas.rootguard import RootStatus
from atlas.server import create_app
from tests.test_server import MAP, make_runner

APP = Path(__file__).resolve().parents[1]


def _install(paths, model="int8"):
    """Stand-ins of the right size (sparse: instant, no disk); the fake worker never reads them."""
    folder = vf.voice_dir(paths.data)
    folder.mkdir(parents=True, exist_ok=True)
    for f in vf.needed(model):
        with open(folder / f.name, "wb") as out:
            out.truncate(f.size)


@pytest.fixture
def said(tmp_path):
    return tmp_path / "said.jsonl"


@pytest.fixture
async def client(aiohttp_client, paths, said):
    env = {**os.environ, "ATLAS_VOICE_TRACE": "1", "ATLAS_VOICE_FAKE": str(said), "PYTHONPATH": str(APP)}
    app = create_app(paths, runner=make_runner(), start_workers=False, voice_env=env)
    opened = []
    app["file_opener"] = opened.append
    app["opened"] = opened
    c = await aiohttp_client(app)
    assert (await c.post("/api/setup", json={"map": MAP, "backup_remote": None})).status == 200
    # no quiet hours: these tests speak whatever the clock says (CI runs at any hour, in UTC)
    assert (await c.put("/api/settings", json={"quiet_hours": ["00:00", "00:00"]})).status == 200
    return c


def _lines(said, n=1, secs=15):
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        if said.exists():
            got = [json.loads(x) for x in said.read_text(encoding="utf-8").splitlines()]
            if len(got) >= n:
                return got
        time.sleep(0.05)
    return [json.loads(x) for x in said.read_text(encoding="utf-8").splitlines()] if said.exists() else []


async def test_state_before_the_download(client):
    v = await (await client.get("/api/voice")).json()
    assert v["available"] and v["installed"] is False and v["enabled"] is True and v["model"] == "int8"
    assert v["missing_bytes"] == vf.MODELS["int8"].size + vf.VOICES.size
    assert "bm_george" in v["voices"] and "af_heart" in v["voices"]
    assert v["download"]["running"] is False and v["quote_problems"] == []


async def test_download_starts_the_downloader(client):
    calls = []

    class Fake:
        state = {"running": True, "done": 0, "total": 9, "error": None, "model": "fp32"}

        def start(self, model):
            calls.append(model)
            return True
    client.server.app["voice_download"] = Fake()
    r = await client.post("/api/voice/download", json={"model": "fp32"})
    assert r.status == 200 and (await r.json())["started"] is True and calls == ["fp32"]
    assert (await client.post("/api/voice/download", json={})).status == 200 and calls[-1] == "int8"
    assert (await client.post("/api/voice/download", json={"model": "int4"})).status == 400


async def test_sample_needs_the_files_then_plays_with_unsaved_settings(client, paths, said):
    assert (await client.post("/api/voice/sample", json={})).status == 409
    _install(paths)
    r = await client.post("/api/voice/sample", json={"voice_speed": 1.2, "voice_blend": {"bf_emma": 1}})
    assert r.status == 204
    line = _lines(said)[0]
    assert line["speed"] == 1.2 and line["blend"] == {"bf_emma": 1} and "This is how I'll sound." in line["parts"][0]
    assert (await client.post("/api/voice/sample", json={"voice_speed": 9})).status == 400
    assert (await client.post("/api/voice/sample", json={"claude_path": "x"})).status == 400


async def test_an_agent_that_needs_you_is_spoken(client, paths, said):
    _install(paths)
    ev = {"session_id": "s1", "cwd": str(paths.root / "elsewhere" / "RobotArmControl"), "entrypoint": "cli"}
    assert (await client.post("/api/hooks/event", json={**ev, "hook_event_name": "SessionStart"})).status == 204
    await client.post("/api/hooks/event", json={**ev, "hook_event_name": "Notification",
                                                 "notification_type": "permission_prompt", "message": "rm -rf /?"})
    line = _lines(said)[0]
    assert line["parts"][0] == "Robot Arm Control needs you. It's asking for permission."
    assert "rm -rf" not in json.dumps(line)


async def test_tablet_note_and_answer_are_read_back(client, paths, said):
    _install(paths)
    assert (await client.post("/api/tablet/notes", json={"text": "buy m3 screws", "client_id": "note-0001"})).status == 201
    assert (await client.post("/api/tablet/notes", json={"text": "buy m3 screws", "client_id": "note-0001"})).status == 200
    r = await client.post("/api/requests", json={"title": "Desk", "items": ["Width of the desk", "Height"]})
    rid = (await r.json())["request"]["id"]
    r = await client.post(f"/api/tablet/requests/{rid}/answer", json={"answers": {"0": "1200 mm"}})
    assert r.status == 201
    _lines(said, 2)
    time.sleep(0.5)
    got = _lines(said, 2)
    # the resend that had already landed is not read twice
    assert [x["parts"] for x in got] == [["Note saved. buy m3 screws."], ["1200 millimetres. Saved."]]


async def test_pause_resume_and_bad_bodies(client):
    r = await client.post("/api/voice/pause", json={"minutes": 30})
    assert r.status == 200 and (await r.json())["paused_until"]
    assert (await (await client.get("/api/voice")).json())["paused_until"]
    r = await client.post("/api/voice/pause", json={"until": "morning"})
    assert (await r.json())["paused_until"].endswith(("08:00"))
    r = await client.post("/api/voice/pause", json={"resume": True})
    assert (await r.json())["paused_until"] is None
    for bad in [{}, {"minutes": 0}, {"minutes": "5"}, {"minutes": 2000}, {"until": "never"}]:
        assert (await client.post("/api/voice/pause", json=bad)).status == 400


async def test_stop_and_edit_quotes_want_json(client, paths):
    assert (await client.post("/api/voice/stop", json={})).status == 204
    assert (await client.post("/api/voice/stop", data="x")).status == 415
    assert (await client.post("/api/voice/edit-quotes", json={})).status == 204
    assert client.server.app["opened"] == [paths.brain / "_voice" / "quotes.md"]


async def test_switching_the_voice_off_stops_it(client):
    stops = []
    client.server.app["voice"].stop = lambda: stops.append(1)
    await client.put("/api/settings", json={"voice_volume": 50})
    assert stops == []
    await client.put("/api/settings", json={"voice_enabled": False})
    assert stops == [1]
    await client.put("/api/settings", json={"voice_volume": 60})  # already off: a sample isn't cut
    assert stops == [1]


async def test_a_readonly_root_has_no_voice(aiohttp_client, paths):
    app = create_app(paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(app)
    assert (await c.post("/api/setup", json={"map": MAP, "backup_remote": None})).status == 200
    await c.close()
    ro = create_app(paths, runner=make_runner(), start_workers=False, status=RootStatus("readonly", "read-only"))
    c = await aiohttp_client(ro)
    v = await (await c.get("/api/voice")).json()
    assert v["available"] is False
    assert (await c.post("/api/voice/pause", json={"minutes": 5})).status == 503
