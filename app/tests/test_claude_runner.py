from pathlib import Path

import pytest

from brainhub.claude_runner import ClaudeError, ClaudeRunner, build_args, child_env

from tests.fakes import fake_cli, kill_pid_file, pid_gone


def test_child_env_strips_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert "ANTHROPIC_API_KEY" not in child_env()


def test_child_env_disables_auto_memory(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", raising=False)
    assert child_env()["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


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


def test_claude_found_in_local_bin_fallback(tmp_path, monkeypatch):
    # The native installer puts claude in ~/.local/bin, which a hub started at login may not
    # have on PATH.
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert ClaudeRunner("claude").claude_path is None
    exe = tmp_path / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude")
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    assert ClaudeRunner("claude").claude_path == str(exe)


# Live-process tests against a stand-in "claude" (a Python script), no real CLI needed.
import sys


def _fake_cli(tmp_path, body):
    return fake_cli(tmp_path, body)


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


ISOLATION = ["--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]


@pytest.mark.parametrize("mode,tools,expected", [
    ("quick recall", [], ""),
    ("dig deeper", ["Read", "Glob", "Grep"], "Read,Glob,Grep"),
    ("filing", ["Read", "Write", "Edit", "Glob", "Grep"], "Read,Write,Edit,Glob,Grep"),
])
def test_build_args_isolates_each_mode(mode, tools, expected):
    a = build_args(model="haiku", system_prompt_file=Path("p.md"), allowed_tools=tools, max_turns=3,
                   json_schema={"type": "object"} if mode == "filing" else None, stream=mode != "filing")
    assert a[a.index("--tools") + 1] == expected
    assert all(flag in a for flag in ISOLATION)
    assert a[a.index("--setting-sources") + 1] == ""
    assert "--bare" not in a and "--mcp-config" not in a
    if tools:
        i = a.index("--allowedTools")
        assert a[i + 1:i + 1 + len(tools)] == tools
    else:
        assert "--allowedTools" not in a



@pytest.mark.skipif(sys.platform != "win32", reason="Windows console flags")
async def test_children_get_no_console_window(tmp_path, monkeypatch):
    """claude and taskkill start with CREATE_NO_WINDOW, so a hub without a visible console
    (the desktop shell's) never pops one up (final review #1)."""
    import asyncio
    import subprocess
    calls = []
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kw):
        calls.append((args[0], kw.get("creationflags", 0)))
        return await real(*args, **kw)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    r = ClaudeRunner(_fake_cli(tmp_path, "import time\ntime.sleep(60)\n"))
    with pytest.raises(ClaudeError, match="timed out"):
        await r.run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                         json_schema={}, timeout_s=1, **_KW)
    assert [Path(c[0]).name.lower() for c in calls] == ["fake_claude.bat", "taskkill"]
    assert all(flags & subprocess.CREATE_NO_WINDOW for _, flags in calls), calls


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups (Windows kills the tree with taskkill /T)")
async def test_timeout_kills_process_group(tmp_path):
    """A run cut off by its timeout takes its tool children with it (dual-boot design §6.5)."""
    pid_file = tmp_path / "child.pid"
    body = ("import subprocess, time\n"
            "child = subprocess.Popen(['sleep', '30'])\n"
            f"with open({str(pid_file)!r}, 'w') as f:\n"
            "    f.write(str(child.pid))\n"
            "time.sleep(60)\n")
    r = ClaudeRunner(_fake_cli(tmp_path, body))
    try:
        with pytest.raises(ClaudeError, match="timed out"):
            await r.run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                             json_schema={}, timeout_s=2, **_KW)
        assert pid_gone(int(pid_file.read_text()), timeout=3)
    finally:
        kill_pid_file(pid_file)


# -- usage from the hub's own runs (owner feedback 2026-10-22) -----------------------------------
_RATE = {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.07, "resetsAt": 1800003600}}}


def _lines(*events, exit_code=0):
    """A fake claude that prints these stream-json events, one per line (shapes from 2.1.309)."""
    body = "import sys,json\nsys.stdin.read()\n"
    body += "".join(f"print(json.dumps({e!r}))\n" for e in events)
    return body + (f"sys.exit({exit_code})\n" if exit_code else "")


def test_build_args_schema_run_streams_without_partial_messages():
    """Filing reads the stream too, for its rate_limit_event; it needs no partial text."""
    a = build_args(model="sonnet", system_prompt_file=Path("p.md"), allowed_tools=["Read"],
                   max_turns=20, json_schema={"type": "object"}, stream=False)
    assert a[a.index("--output-format") + 1] == "stream-json" and "--verbose" in a
    assert "--include-partial-messages" not in a


async def test_run_json_reads_the_stream_and_reports_usage(tmp_path):
    seen = []

    async def on_rate_limit(info):
        seen.append(info)

    cli = _fake_cli(tmp_path, _lines(
        {"type": "system", "subtype": "init"}, {"type": "assistant", "message": {}},
        {"type": "rate_limit_event", "rate_limit_info": _RATE},
        {"type": "result", "subtype": "success", "structured_output": {"n": 7}}))
    out = await ClaudeRunner(cli, on_rate_limit=on_rate_limit).run_json(
        "x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md", json_schema={}, timeout_s=20, **_KW)
    assert out == {"n": 7} and seen == [_RATE]


async def test_run_json_reports_usage_from_a_failed_run(tmp_path):
    """A run turned away by the limit is exactly when the meter matters."""
    seen = []

    async def on_rate_limit(info):
        seen.append(info)

    cli = _fake_cli(tmp_path, _lines({"type": "rate_limit_event", "rate_limit_info": _RATE}, exit_code=1))
    with pytest.raises(ClaudeError, match="exited 1"):
        await ClaudeRunner(cli, on_rate_limit=on_rate_limit).run_json(
            "x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md", json_schema={}, timeout_s=20, **_KW)
    assert seen == [_RATE]


async def test_run_json_unparsable_output(tmp_path):
    cli = _fake_cli(tmp_path, "import sys\nsys.stdin.read()\nprint('not json')\nprint('[1]')\n")
    with pytest.raises(ClaudeError, match="unparsable"):
        await ClaudeRunner(cli).run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                         json_schema={}, timeout_s=20, **_KW)


async def test_stream_text_reports_usage(tmp_path):
    seen = []

    async def on_rate_limit(info):
        seen.append(info)

    delta = {"type": "stream_event", "event": {"type": "content_block_delta",
                                                "delta": {"type": "text_delta", "text": "ok"}}}
    cli = _fake_cli(tmp_path, _lines(delta, {"type": "rate_limit_event", "rate_limit_info": _RATE},
                                     {"type": "rate_limit_event"}, {"type": "result"}))
    got = [c async for c in ClaudeRunner(cli, on_rate_limit=on_rate_limit).stream_text(
        "x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md", timeout_s=20, **_KW)]
    assert got == ["ok"] and seen == [_RATE]


async def test_a_failing_usage_callback_never_breaks_a_run(tmp_path):
    async def on_rate_limit(info):
        raise OSError("disk")

    cli = _fake_cli(tmp_path, _lines({"type": "rate_limit_event", "rate_limit_info": _RATE},
                                     {"type": "result", "structured_output": {"n": 1}}))
    r = ClaudeRunner(cli, on_rate_limit=on_rate_limit)
    assert await r.run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                            json_schema={}, timeout_s=20, **_KW) == {"n": 1}
    assert [c async for c in r.stream_text("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                           timeout_s=20, **_KW)] == []


async def test_run_json_keeps_unicode_line_separators(tmp_path):
    """JSON.stringify leaves U+2028, U+2029 and U+0085 raw; only \\n ends a stream line (review)."""
    body = ("import sys,json\nsys.stdin.read()\n"
            "d={'type':'result','structured_output':{'t':'a\\u2028b\\u2029c\\x85d'}}\n"
            "sys.stdout.buffer.write(json.dumps(d,ensure_ascii=False).encode('utf-8')+b'\\n')\n")
    out = await ClaudeRunner(_fake_cli(tmp_path, body)).run_json(
        "x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md", json_schema={}, timeout_s=20, **_KW)
    assert out == {"t": "a\u2028b\u2029c\x85d"}


async def test_run_json_reports_usage_seen_before_a_timeout(tmp_path):
    seen = []

    async def on_rate_limit(info):
        seen.append(info)

    body = ("import sys,json,time\nsys.stdin.read()\n"
            f"print(json.dumps({{'type':'rate_limit_event','rate_limit_info':{_RATE!r}}}),flush=True)\n"
            "time.sleep(60)\n")
    with pytest.raises(ClaudeError, match="timed out"):
        await ClaudeRunner(_fake_cli(tmp_path, body), on_rate_limit=on_rate_limit).run_json(
            "x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md", json_schema={}, timeout_s=2, **_KW)
    assert seen == [_RATE]


async def test_failed_run_names_the_clis_own_error(tmp_path):
    """In stream-json mode the CLI's error text is in its result event, not on stderr."""
    cli = _fake_cli(tmp_path, _lines({"type": "result", "subtype": "error_during_execution", "is_error": True,
                                      "result": "API Error: overloaded"}, exit_code=1))
    with pytest.raises(ClaudeError, match="exited 1.*overloaded"):
        await ClaudeRunner(cli).run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                         json_schema={}, timeout_s=20, **_KW)
    with pytest.raises(ClaudeError, match="exited 1.*overloaded"):
        [c async for c in ClaudeRunner(cli).stream_text("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                                        timeout_s=20, **_KW)]


# -- speed (owner feedback 2026-10-22) -------------------------------------------------------------
def test_child_env_skips_nonessential_traffic(monkeypatch):
    """Measured with 2.1.309: start-up 0.5-0.9 s -> 0.2 s, and the exit after the result 0.7-0.9 s
    -> 0.02 s (no telemetry flush, update check or error reporting in the hub's headless runs)."""
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", raising=False)
    assert child_env()["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


async def test_run_json_reports_each_tool_step(tmp_path):
    steps = []

    async def on_step(tool, tool_input):
        steps.append((tool, tool_input))

    msg = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Let me look."},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/w/a.md"}},
        {"type": "tool_use", "id": "t2", "name": "Glob", "input": {"pattern": "**/*.md"}}]}}
    odd = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": 3}, "x"]}}
    cli = _fake_cli(tmp_path, _lines(msg, odd, {"type": "assistant"},
                                     {"type": "result", "structured_output": {"n": 1}}))
    out = await ClaudeRunner(cli).run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                           json_schema={}, timeout_s=20, on_step=on_step, **_KW)
    assert out == {"n": 1}
    assert steps == [("Read", {"file_path": "/w/a.md"}), ("Glob", {"pattern": "**/*.md"})]


async def test_a_failing_step_callback_never_breaks_a_filing(tmp_path):
    async def on_step(tool, tool_input):
        raise RuntimeError("socket gone")

    msg = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}}
    cli = _fake_cli(tmp_path, _lines(msg, {"type": "result", "structured_output": {"n": 1}}))
    assert await ClaudeRunner(cli).run_json("x", cwd=tmp_path, system_prompt_file=tmp_path / "p.md",
                                            json_schema={}, timeout_s=20, on_step=on_step, **_KW) == {"n": 1}
