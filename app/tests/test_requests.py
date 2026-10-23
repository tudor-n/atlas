"""Phase 3: measurement requests (spec §7.3): the API, brainhub-request, and its installation."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from brainhub import claude_integration as ci
from brainhub import osdirs
from brainhub.brainmap import add_node, default_map, save_map
from brainhub.server import create_app
from tests.test_server import MAP, make_runner

CLIENT = Path(__file__).resolve().parent.parent / "brainhub" / "request_client.py"


@pytest.fixture
async def client(aiohttp_client, paths):
    app = create_app(paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(app)
    assert (await c.post("/api/setup", json={"map": MAP, "backup_remote": None})).status == 200
    return c


async def test_post_request_resolves_the_project_and_exports(client, paths, tmp_path):
    arm = tmp_path / "robotarm"
    arm.mkdir()
    m = add_node(default_map(), "project", "Robot arm", None)
    m.projects[-1]["os_folders"] = {osdirs.os_name(): [str(arm)]}
    save_map(paths.brain, m)
    v = client.server.app["feed"].version
    r = await client.post("/api/requests", json={"title": "Arm calibration",
                                                 "items": ["ArUco ID 3 → arm base", " ID 3 → ID 7 "],
                                                 "cwd": str(arm / "src")})
    assert r.status == 201
    req = (await r.json())["request"]
    assert req["items"] == ["ArUco ID 3 → arm base", "ID 3 → ID 7"] and req["status"] == "open"
    assert req["project_name"] == "Robot arm" and client.server.app["feed"].version > v
    exported = json.loads((paths.brain / "_items" / "requests.json").read_text(encoding="utf-8"))
    assert [x["id"] for x in exported] == [req["id"]]
    listed = await (await client.get("/api/requests")).json()
    assert listed[0]["id"] == req["id"] and listed[0]["hidden"] is False


async def test_request_without_cwd_has_no_project(client):
    r = await client.post("/api/requests", json={"title": "Bench check", "items": ["voltage at J3"]})
    assert r.status == 201 and (await r.json())["request"]["project"] is None


@pytest.mark.parametrize("body", [
    {"items": ["a"]}, {"title": " ", "items": ["a"]}, {"title": "x" * 81, "items": ["a"]},
    {"title": "t", "items": []}, {"title": "t", "items": "a"}, {"title": "t", "items": ["a"] * 21},
    {"title": "t", "items": ["x" * 121]}, {"title": "t", "items": ["two\nlines"]},
    {"title": "t\x07", "items": ["a"]}, {"title": "t", "items": [3]},
])
async def test_bad_requests_are_400(client, body):
    r = await client.post("/api/requests", json=body)
    assert r.status == 400, await r.text()


async def test_requests_need_setup(aiohttp_client, paths):
    c = await aiohttp_client(create_app(paths, runner=make_runner(), start_workers=False))
    r = await c.post("/api/requests", json={"title": "t", "items": ["a"]})
    assert r.status == 409
    assert not (paths.brain / "_items").exists()  # nothing written under a root before setup


async def test_ignore_and_restore_a_request(client, paths):
    rid = (await (await client.post("/api/requests", json={"title": "t", "items": ["a"]})).json())["request"]["id"]
    assert (await client.post(f"/api/requests/{rid}/ignore")).status == 204
    assert (await (await client.get("/api/requests")).json())[0]["hidden"] is True
    hidden = json.loads((paths.brain / "_items" / "tablet.json").read_text(encoding="utf-8"))["hidden"]
    assert [(h["kind"], h["ref"]) for h in hidden] == [("request", rid)]
    assert (await client.post(f"/api/requests/{rid}/restore")).status == 204
    assert (await (await client.get("/api/requests")).json())[0]["hidden"] is False
    assert (await client.post("/api/requests/r-nope/ignore")).status == 404


# -- brainhub-request, as a real subprocess -----------------------------------------------------
def _run(*args, cwd=None):
    return subprocess.run([sys.executable, "-I", "-S", str(CLIENT), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=30, cwd=cwd)


async def test_client_sends_a_request(aiohttp_server, paths, tmp_path):
    app = create_app(paths, runner=make_runner(), start_workers=False)
    server = await aiohttp_server(app)
    import aiohttp
    async with aiohttp.ClientSession() as s:
        await s.post(f"http://127.0.0.1:{server.port}/api/setup", json={"map": MAP, "backup_remote": None})
    import asyncio
    p = await asyncio.to_thread(_run, "--port", str(server.port), "Arm calibration", "ID 3 → ID 7", cwd=tmp_path)
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("Sent to the workbench tablet (r-") and "not back to you" in p.stdout
    reqs = app["store"].list_requests()
    assert reqs[0]["title"] == "Arm calibration" and reqs[0]["items"] == ["ID 3 → ID 7"]
    assert reqs[0]["project"] == tmp_path.name  # not mapped: the folder's name
    p = await asyncio.to_thread(_run, "--port", str(server.port), "t", "two\nlines")
    assert p.returncode == 2 and "nothing was sent" in p.stderr


def test_client_with_the_hub_down_says_so(tmp_path):
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    p = _run("--port", str(port), "t", "a")
    assert p.returncode == 1 and "isn't running" in p.stderr and p.stdout == ""


@pytest.mark.parametrize("args", [[], ["only a title"], ["--help"], ["--port", "x", "t", "a"]])
def test_client_usage(args):
    p = _run(*args)
    assert p.returncode == 2 and p.stderr.startswith("usage: brainhub-request")


# -- installation: the wrapper and the CLAUDE.md line (temp folders only, rule 12) ---------------
def _settings(tmp_path):
    s = tmp_path / "claude" / "settings.json"
    s.parent.mkdir(parents=True)
    s.write_text("{}", encoding="utf-8")
    return s


def test_install_writes_wrapper_and_one_claude_md_line(tmp_path):
    settings = _settings(tmp_path)
    md = settings.parent / "CLAUDE.md"
    md.write_text("# My rules\n\nBe brief.\n", encoding="utf-8")
    state = osdirs.state_dir()
    python, script = ci.hook_paths(state)
    st = ci.install(settings, python=python, script=script, port=8090, state_dir=state)
    assert st["request_command"] is True
    text = md.read_text(encoding="utf-8")
    assert text.startswith("# My rules\n\nBe brief.\n\n") and text.count(ci.MD_BEGIN) == 1
    assert ci.MD_LINE in text and "brainhub-request" in text
    assert list(settings.parent.glob("CLAUDE.md.brainhub-backup-*"))  # backed up first
    wrapper = ci.wrapper_path(osdirs.bin_dir())
    body = wrapper.read_text(encoding="utf-8")
    assert ci.WRAPPER_MARK in body and "request_client.py" in body and "--port 8090" in body
    if sys.platform != "win32":
        assert os.access(wrapper, os.X_OK)
    ci.install(settings, python=python, script=script, port=8091, state_dir=state)  # again: still one line
    assert md.read_text(encoding="utf-8").count(ci.MD_BEGIN) == 1
    assert "--port 8091" in wrapper.read_text(encoding="utf-8")
    ci.uninstall(settings, state_dir=state)
    assert md.read_text(encoding="utf-8") == "# My rules\n\nBe brief.\n" and not wrapper.exists()
    assert ci.status(settings, port=8090, state_dir=state)["request_command"] is False


def test_uninstall_removes_a_claude_md_that_held_only_our_line(tmp_path):
    settings = _settings(tmp_path)
    state = osdirs.state_dir()
    ci.install(settings, python=sys.executable, script="x.py", port=8090, state_dir=state)
    assert (settings.parent / "CLAUDE.md").exists()
    ci.uninstall(settings, state_dir=state)
    assert not (settings.parent / "CLAUDE.md").exists()


def test_install_refuses_a_foreign_wrapper(tmp_path):
    settings = _settings(tmp_path)
    wrapper = ci.wrapper_path(osdirs.bin_dir())
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    with pytest.raises(ci.IntegrationError, match="isn't BrainHub's"):
        ci.install_request_command(settings.parent / "CLAUDE.md", osdirs.bin_dir(),
                                   python="py", script="s", port=1)
    ci.uninstall_request_command(settings.parent / "CLAUDE.md", osdirs.bin_dir())
    assert wrapper.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"  # never removed


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX wrapper (the .cmd one runs in CI on Windows)")
def test_wrapper_runs_the_client(tmp_path):
    bin_dir = tmp_path / "wbin"
    ci.install_request_command(tmp_path / "CLAUDE.md", bin_dir, python=sys.executable, script=str(CLIENT), port=9)
    p = subprocess.run([str(ci.wrapper_path(bin_dir)), "--help"], capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and p.stderr.startswith("usage: brainhub-request")


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows wrapper")
def test_cmd_wrapper_runs_the_client(tmp_path):
    bin_dir = tmp_path / "wbin"
    ci.install_request_command(tmp_path / "CLAUDE.md", bin_dir, python=sys.executable, script=str(CLIENT), port=9)
    p = subprocess.run([str(ci.wrapper_path(bin_dir)), "--help"], capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "usage: brainhub-request" in p.stderr


def test_request_paths_in_runtime_mode(tmp_path):
    state = osdirs.state_dir()
    assert ci.request_paths(state)[1].endswith("request_client.py")  # dev: the package's copy
    (state / "runtime").mkdir(parents=True)
    (state / "runtime" / "current.json").write_text("{}", encoding="utf-8")
    (state / "hook").mkdir()
    (state / "hook" / "request_client.py").write_text("", encoding="utf-8")
    python, script = ci.request_paths(state)
    assert script == str(state / "hook" / "request_client.py") and python == str(ci.base_python())


# -- review: the CLAUDE.md block, edited only when it is exactly one whole-line pair -------------
def test_a_damaged_block_is_refused_and_nothing_is_eaten(tmp_path):
    md = tmp_path / "CLAUDE.md"
    text = f"# rules\n{ci.MD_BEGIN}\n{ci.MD_LINE}\n(I removed the end marker)\nmore of my own notes\n"
    md.write_text(text, encoding="utf-8")
    with pytest.raises(ci.IntegrationError, match="CLAUDE.md"):
        ci.install_request_command(md, tmp_path / "b", python="py", script="s", port=1)
    with pytest.raises(ci.IntegrationError, match="CLAUDE.md"):
        ci.uninstall_request_command(md, tmp_path / "b")
    assert md.read_text(encoding="utf-8") == text


def test_a_marker_quoted_in_my_own_text_is_left_alone(tmp_path):
    md = tmp_path / "CLAUDE.md"
    text = f"I once saw `{ci.MD_BEGIN}` in a file.\n"
    md.write_text(text, encoding="utf-8")
    ci.install_request_command(md, tmp_path / "b", python="py", script="s", port=1)
    ci.uninstall_request_command(md, tmp_path / "b")
    assert md.read_text(encoding="utf-8") == text


def test_the_block_keeps_its_place_and_the_file_its_line_endings(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_bytes(b"# top\r\n\r\n")
    ci.install_request_command(md, tmp_path / "b", python="py", script="s", port=1)
    with open(md, "a", encoding="utf-8", newline="") as f:
        f.write("# below, written later\r\n")
    ci.install_request_command(md, tmp_path / "b", python="py", script="s", port=2)
    raw = md.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")  # still CRLF throughout
    lines = raw.decode("utf-8").split("\r\n")
    assert lines.index(ci.MD_BEGIN) < lines.index("# below, written later")  # not moved to the end
    assert raw.decode("utf-8").count(ci.MD_BEGIN) == 1


def test_a_foreign_wrapper_is_found_before_settings_are_touched(tmp_path):
    settings = _settings(tmp_path)
    settings.write_text('{"theme": "dark"}', encoding="utf-8")
    wrapper = ci.wrapper_path(osdirs.bin_dir())
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    with pytest.raises(ci.IntegrationError, match="isn't BrainHub's"):
        ci.install(settings, python=sys.executable, script="x.py", port=8090, state_dir=osdirs.state_dir())
    assert settings.read_text(encoding="utf-8") == '{"theme": "dark"}'
    assert not list(settings.parent.glob("settings.json.brainhub-backup-*"))


def test_windows_wrappers_and_percent_signs():
    """Claude Code's Bash tool on Windows is Git Bash, which never tries .cmd for a bare name: an
    extensionless sh shim goes beside the .cmd. A % in a path is doubled for cmd."""
    files = ci.wrapper_files(r"C:\Py 3\python.exe", r"C:\100%\request_client.py", 8090, windows=True)
    assert set(files) == {"brainhub-request.cmd", "brainhub-request"}
    assert '"C:\\100%%\\request_client.py"' in files["brainhub-request.cmd"]
    assert files["brainhub-request"].startswith("#!/bin/sh\n") and "'C:/Py 3/python.exe'" in files["brainhub-request"]
    assert list(ci.wrapper_files("/usr/bin/python3", "/s.py", 1, windows=False)) == ["brainhub-request"]
