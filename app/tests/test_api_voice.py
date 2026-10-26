"""The voice in the hub, end to end through the API, with the real worker process in fake mode."""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from atlas import voice_files as vf
from atlas.claude_runner import ClaudeError
from atlas.rootguard import RootStatus
from atlas.lines import ensure_lines
from atlas.server import create_app
from tests.fakes import FakeRunner
from tests.test_filing import GOOD_RESULT
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
    # These tests check the plumbing with the fixed lines; the varied wordings are test_lines.py's.
    (paths.brain / "_voice" / "lines.md").unlink()
    # no quiet hours: these tests speak whatever the clock says (CI runs at any hour, in UTC)
    assert (await c.put("/api/settings", json={"quiet_hours": ["00:00", "00:00"], "voice_lead_in_ms": 0})).status == 200
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
    _lines(said, 1)
    time.sleep(0.5)
    got = _lines(said, 1)
    # the resend that had already landed is not read twice; the two came together, so one clip
    assert [x["parts"] for x in got] == [["Note saved. buy m3 screws.", "1200 millimetres. Saved."]]


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
    assert (await client.post("/api/voice/edit-lines", json={})).status == 404  # the fixture removed it
    ensure_lines(paths.brain)
    assert (await client.post("/api/voice/edit-lines", json={})).status == 204
    assert client.server.app["opened"][-1] == paths.brain / "_voice" / "lines.md"


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


# -- ATLAS's replies after the one input (VI4) ---------------------------------------------------------
def _record(app):
    sent, original = [], app["broadcast"]

    async def record(type_, data):
        sent.append((type_, data))
        await original(type_, data)
    app["broadcast"] = record
    return sent


def _replies(sent):
    return [d for k, d in sent if k == "atlas.reply"]


async def test_a_note_from_the_one_input_gets_a_reply_once_it_is_filed(client, paths, said):
    _install(paths)
    app = client.server.app
    sent = _record(app)
    r = await client.post("/api/entries", json={"text": "check the wrist servo", "source": "pc", "reply": True})
    eid = (await r.json())["entry_id"]
    assert eid in app["worker"].replies
    await app["worker"].file_entry(eid)
    (reply,) = _replies(sent)
    assert reply["kind"] == "tasks" and reply["entry_id"] == eid
    assert reply["text"].startswith("I pulled out one possible task. ")  # sass is on by default: a quip follows
    line = _lines(said)[0]
    assert line["parts"][0] == "I pulled out one possible task." and " ".join(line["parts"]) == reply["text"]
    assert "wrist servo" not in json.dumps(line)  # never the proposal's text
    await app["worker"].file_entry(eid)  # a later re-run nobody asked to hear about
    assert len(_replies(sent)) == 1 and eid not in app["worker"].replies


async def test_a_capture_without_the_flag_and_a_restarts_requeue_stay_quiet(client, paths):
    app = client.server.app
    sent = _record(app)
    eid = (await (await client.post("/api/entries", json={"text": "x", "source": "pc"})).json())["entry_id"]
    await app["worker"].file_entry(eid)
    assert _replies(sent) == [] and app["worker"].replies == set()
    assert eid not in await asyncio.to_thread(app["worker"].requeue_unfinished) and app["worker"].replies == set()


async def test_retry_replies_and_a_failed_filing_says_the_words_are_safe(client, paths):
    app = client.server.app
    sent = _record(app)
    eid = (await (await client.post("/api/entries", json={"text": "x", "source": "pc"})).json())["entry_id"]
    app["worker"].runner = FakeRunner(raise_error=ClaudeError("boom"))
    await app["worker"].file_entry(eid)  # no flag: it fails quietly
    assert _replies(sent) == []
    assert (await client.post(f"/api/filings/{eid}/retry", json={})).status == 202
    await app["worker"].file_entry(eid)
    assert [(r["kind"], r["text"]) for r in _replies(sent)] == [
        ("failed", "That one didn't file. Your words are safe in the log.")]


async def test_a_filing_with_no_tasks_says_it_is_filed(client, paths):
    app = client.server.app
    sent = _record(app)
    app["worker"].runner = FakeRunner(json_result={**GOOD_RESULT, "items": []})
    await client.put("/api/settings", json={"voice_sass": False})
    r = await client.post("/api/entries", json={"text": "a thought", "source": "pc", "reply": True})
    await app["worker"].file_entry((await r.json())["entry_id"])
    assert [(r["kind"], r["text"]) for r in _replies(sent)] == [("filed", "Filed it. Nothing to confirm.")]


async def test_a_question_from_the_one_input_gets_a_reply_event_and_a_broadcast(client, paths, said):
    _install(paths)
    app = client.server.app
    sent = _record(app)
    await client.put("/api/settings", json={"voice_sass": False})
    await client.post("/api/entries", json={"text": "the wrist servo buzzes", "source": "pc"})
    body = await (await client.get("/api/ask", params={"q": "wrist servo", "reply": "1"})).text()
    assert body.index("event: delta") < body.index("event: reply") < body.index("event: done")
    assert 'event: reply\ndata: "I found one entry on that."' in body
    assert _replies(sent) == [{"kind": "found", "text": "I found one entry on that.", "entry_id": None}]
    assert _lines(said)[0]["parts"] == ["I found one entry on that."]
    body = await (await client.get("/api/ask", params={"q": "zebra", "reply": "1"})).text()
    assert 'event: reply\ndata: "Nothing on that."' in body
    body = await (await client.get("/api/ask", params={"q": "wrist servo"})).text()  # the old Ask: no reply
    assert "event: reply" not in body and len(_replies(sent)) == 2


async def test_no_reply_after_an_answer_that_failed(aiohttp_client, paths):
    app = create_app(paths, runner=FakeRunner(raise_error=ClaudeError("boom")), start_workers=False)
    c = await aiohttp_client(app)
    await c.post("/api/setup", json={"map": MAP})
    sent = _record(app)
    await c.post("/api/entries", json={"text": "the wrist servo buzzes", "source": "pc"})
    body = await (await c.get("/api/ask", params={"q": "wrist", "reply": "1"})).text()
    assert "event: error" in body and "event: reply" not in body and _replies(sent) == []


async def test_a_read_only_root_still_captions_the_plain_line(client):
    from atlas.routes import atlas_reply
    app = client.server.app
    sent = _record(app)
    app["voice"] = None
    assert await atlas_reply(app, "found", 2) == "I found two entries on that."
    assert _replies(sent) == [{"kind": "found", "text": "I found two entries on that.", "entry_id": None}]


async def test_the_page_hears_when_a_line_sounds_and_ends(client, paths):
    _install(paths)
    sent = _record(client.server.app)
    assert (await client.post("/api/voice/sample", json={})).status == 204
    deadline = time.monotonic() + 15
    while "voice.quiet" not in [k for k, _ in sent] and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    kinds = [k for k, _ in sent if k.startswith("voice.")]
    assert kinds[:2] == ["voice.speaking", "voice.quiet"]
    speaking = next(d for k, d in sent if k == "voice.speaking")
    assert speaking["frame_ms"] == 40 and len(speaking["env"]) == 50  # the sample's two parts, a second each


# -- chatter (2026-10-26) ---------------------------------------------------------------------------
async def test_chatter_endpoints(client, paths):
    _install(paths)
    app = client.server.app
    assert (await client.post("/api/presence", json={})).status == 204
    assert app["presence"].state() == "present"
    view = await (await client.get("/api/chatter")).json()
    assert view["available"] and view["level"] == "medium" and view["per_hour"] == 4
    assert view["presence"]["state"] == "present" and isinstance(view["recent"], list)
    r = await client.post("/api/chatter/react", json={"kind": "flip_ask"})
    assert r.status == 200 and (await r.json())["said"]["text"]
    assert (await client.post("/api/chatter/react", json={"kind": "nope"})).status == 400
    assert (await client.post("/api/chatter/try", json={"what": "musing"})).status == 409  # only at Very high
    assert (await client.put("/api/settings", json={"voice_chattiness": "debug"})).status == 200
    assert app["voice"].chatter_state()["debug"]["before"] == "medium"
    r = await client.post("/api/chatter/try", json={"what": "musing"})
    assert r.status == 200 and (await r.json())["said"]["spoken"] is True
    assert (await client.post("/api/chatter/try", json={"what": "nope"})).status == 400


async def test_a_pc_write_is_the_owner_and_a_hook_is_not(client):
    app = client.server.app
    seen = []
    app["presence"].seen = seen.append
    await client.post("/api/hooks/event", json={"session_id": "s", "hook_event_name": "Stop", "cwd": "/tmp"})
    assert seen == []
    await client.put("/api/settings", json={"voice_volume": 40})
    import asyncio
    await asyncio.sleep(0.05)  # it runs off the loop
    assert seen == ["app"]


async def test_the_week_endpoints(client):
    assert await (await client.get("/api/chatter/week")).json() == {"week": None}
    assert (await client.post("/api/chatter/week/w-1/confirm", json={})).status == 400
    assert (await client.post("/api/chatter/week/w-1/maybe", json={})).status in (404, 405)


async def test_a_refused_request_is_not_the_owner(client):
    import asyncio
    app = client.server.app
    seen = []
    app["presence"].seen = seen.append
    r = await client.put("/api/settings", json={"voice_volume": 40}, headers={"Origin": "http://evil.example"})
    assert r.status == 403
    r = await client.post("/api/nope", json={})
    assert r.status == 404
    await asyncio.sleep(0.05)
    assert seen == []


def test_the_running_commit_comes_from_the_runtime_slot(monkeypatch, tmp_path):
    from atlas import voice_routes as vr
    monkeypatch.setattr(vr.runtime, "hub_view", lambda state: {"commit": "abc123"})
    assert vr._running_commit(tmp_path) == "abc123"
    paths = type("P", (), {"root": tmp_path / "live"})()
    assert vr._code_repo(paths, tmp_path / "code") == tmp_path / "live"
    monkeypatch.setattr(vr.runtime, "hub_view", lambda state: None)
    monkeypatch.setattr(vr, "git_commit", lambda code: "dev456")
    assert vr._running_commit(tmp_path) == "dev456" and vr._code_repo(paths, tmp_path / "code") == tmp_path / "code"
