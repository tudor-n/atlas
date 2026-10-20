from pathlib import Path

import pytest

from brainhub.claude_runner import ClaudeError, ClaudeRunner, build_args, child_env


def test_child_env_strips_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert "ANTHROPIC_API_KEY" not in child_env()


def test_build_args_json():
    a = build_args(model="sonnet", system_prompt_file=Path("p.md"), allowed_tools=["Read", "Edit"],
                   max_turns=20, json_schema={"type": "object"}, stream=False)
    assert a[:1] == ["-p"] and "--json-schema" in a and "--bare" not in a
    assert a[a.index("--permission-mode") + 1] == "acceptEdits" and a[a.index("--model") + 1] == "sonnet"


def test_build_args_no_tools_no_permission_mode():
    a = build_args(model="haiku", system_prompt_file=Path("p.md"), allowed_tools=[], max_turns=1,
                   json_schema=None, stream=True)
    assert "--allowedTools" not in a and "--permission-mode" not in a and "stream-json" in a


async def test_missing_cli_raises(tmp_path):
    r = ClaudeRunner("definitely-not-claude-xyz")
    with pytest.raises(ClaudeError):
        await r.run_json("hi", cwd=tmp_path, model="haiku", system_prompt_file=tmp_path / "p.md",
                         allowed_tools=[], json_schema={}, max_turns=1, timeout_s=5)


# Live-process tests against a stand-in "claude" (a Python script), no real CLI needed.
import sys


def _fake_cli(tmp_path, body):
    script = tmp_path / "fake_claude.py"
    script.write_text(body, encoding="utf-8")
    bat = tmp_path / "fake_claude.bat"
    bat.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, script), encoding="utf-8")
    return str(bat)


_KW = dict(model="haiku", allowed_tools=[], max_turns=1)


async def test_run_json_returns_structured_output(tmp_path):
    cli = _fake_cli(tmp_path, "import sys,json\np=sys.stdin.buffer.read().decode('utf-8')\n"
                    "print(json.dumps({'type':'result','structured_output':{'echo':p}}))\n")
    out = await ClaudeRunner(cli).run_json("héllo", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                           json_schema={}, timeout_s=20, **_KW)
    assert out == {"echo": "héllo"}


async def test_run_json_nonzero_and_missing_structured(tmp_path):
    bad = ClaudeRunner(_fake_cli(tmp_path, "import sys\nsys.stderr.write('boom')\nsys.exit(3)\n"))
    with pytest.raises(ClaudeError, match="boom"):
        await bad.run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                           json_schema={}, timeout_s=20, **_KW)
    none = ClaudeRunner(_fake_cli(tmp_path, "print('{\"result\":\"x\"}')\n"))
    with pytest.raises(ClaudeError, match="structured"):
        await none.run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                            json_schema={}, timeout_s=20, **_KW)


async def test_run_json_timeout_kills(tmp_path):
    r = ClaudeRunner(_fake_cli(tmp_path, "import time\ntime.sleep(60)\n"))
    with pytest.raises(ClaudeError, match="timed out"):
        await r.run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                         json_schema={}, timeout_s=1, **_KW)


_STREAM = (
    "import sys,json\nsys.stdin.read()\n"
    "sys.stderr.write('e'*1000000)\n"
    "def ev(d): print(json.dumps({'type':'stream_event','event':{'type':'content_block_delta','delta':d}}))\n"
    "ev({'type':'text_delta','text':'Hel'})\n"
    "ev({'type':'thinking_delta','thinking':'zzz'})\n"
    "ev({'type':'text_delta','text':'lo'})\n"
    "print(json.dumps({'type':'result'}))\n")


async def test_stream_text_yields_deltas_and_survives_big_stderr(tmp_path):
    r = ClaudeRunner(_fake_cli(tmp_path, _STREAM))
    got = [c async for c in r.stream_text("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                          timeout_s=30, **_KW)]
    assert got == ["Hel", "lo"]


async def test_stream_text_error_and_timeout(tmp_path):
    bad = ClaudeRunner(_fake_cli(tmp_path, "import sys\nsys.stderr.write('kaput')\nsys.exit(2)\n"))
    with pytest.raises(ClaudeError, match="kaput"):
        [c async for c in bad.stream_text("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                          timeout_s=20, **_KW)]
    slow = ClaudeRunner(_fake_cli(tmp_path, "import time\ntime.sleep(60)\n"))
    with pytest.raises(ClaudeError, match="timed out"):
        [c async for c in slow.stream_text("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                           timeout_s=1, **_KW)]


async def test_fake_runner_records_and_behaves(tmp_path):
    from tests.fakes import FakeRunner
    seen = []
    f = FakeRunner(json_result={"a": 1}, stream_chunks=["x", "y"], on_run=seen.append)
    assert await f.run_json("p", cwd=tmp_path, model="m") == {"a": 1}
    assert [c async for c in f.stream_text("q", cwd=tmp_path)] == ["x", "y"]
    assert f.calls[0] == {"prompt": "p", "cwd": tmp_path, "model": "m"} and seen == [tmp_path, tmp_path]
    boom = FakeRunner(raise_error=ClaudeError("no"))
    with pytest.raises(ClaudeError):
        await boom.run_json("p", cwd=tmp_path)


_JUNK = (
    "import sys,json\nsys.stdin.read()\n"
    "print('42')\nprint('[1]')\nprint('null')\nprint('not json')\nprint('{\"type\":')\n"
    "print(json.dumps({'type':'stream_event','event':'oops'}))\n"
    "print(json.dumps({'type':'stream_event','event':{'type':'content_block_delta','delta':[1]}}))\n"
    "print(json.dumps({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'ok'}}}))\n")


async def test_stream_text_ignores_malformed_lines(tmp_path):
    r = ClaudeRunner(_fake_cli(tmp_path, _JUNK))
    got = [c async for c in r.stream_text("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                          timeout_s=20, **_KW)]
    assert got == ["ok"]


async def test_stream_text_line_too_long(tmp_path, monkeypatch):
    import brainhub.claude_runner as cr
    monkeypatch.setattr(cr, "_STREAM_LIMIT", 1024)
    r = ClaudeRunner(_fake_cli(tmp_path, "import sys,time\nsys.stdin.read()\nprint('x'*5000,flush=True)\ntime.sleep(30)\n"))
    with pytest.raises(ClaudeError, match="too long"):
        [c async for c in r.stream_text("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                        timeout_s=20, **_KW)]


async def test_run_json_error_subtype_in_message(tmp_path):
    r = ClaudeRunner(_fake_cli(tmp_path, "print('{\"type\":\"result\",\"subtype\":\"error_max_turns\",\"is_error\":true,\"result\":\"ran out\"}')\n"))
    with pytest.raises(ClaudeError, match="error_max_turns"):
        await r.run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                         json_schema={}, timeout_s=20, **_KW)
