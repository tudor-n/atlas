"""Async wrapper around the `claude` CLI in headless mode (`claude -p`).

Runs on the owner's subscription: ANTHROPIC_API_KEY is stripped from the child
env and `--bare` is never used. The prompt goes to stdin, never argv.

Real output shapes (claude 2.1.304, smoke-checked with --model haiku):

* `--output-format json --json-schema S` prints ONE JSON object with
  `type: "result"`, `subtype: "success"`, `is_error`, `num_turns`, `result`
  (the text; with a schema it is the JSON as a string) and a top-level
  `structured_output` holding the schema-validated object, e.g. {"n": 7}.
  `--max-turns 2` still succeeded with num_turns 3 (the schema is delivered
  via an internal tool call), so give schema runs some headroom.
* `--output-format stream-json --verbose --include-partial-messages` prints
  one JSON object per line. Event `type`s seen: `system` (hook_started,
  hook_response, init, status, thinking_tokens), `stream_event`, `assistant`
  (complete message), `rate_limit_event`, `result`. Text deltas are
  `{"type":"stream_event","event":{"type":"content_block_delta",
  "delta":{"type":"text_delta","text":"..."}}}`; thinking arrives as
  `thinking_delta` / `signature_delta` and must be ignored.
* `--max-turns 1` with no tools still returns a normal text answer
  (num_turns 1, subtype success).
* (2.1.309) Schema runs use `stream-json --verbose` too, without partial
  messages: the last line is the same `result` object, `structured_output`
  included. Only the stream carries `rate_limit_event`, whose `rate_limit_info`
  holds the plan usage (`unifiedWindows`); `--output-format json` drops it.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

log = logging.getLogger(__name__)

_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_STDERR_TAIL = 2000
_STREAM_LIMIT = 16 * 1024 * 1024
# claude and taskkill never open a console window, whatever console the hub has (Ruling 13).
_NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW
# POSIX: each run leads its own process group, so stopping it stops its tool children too
# (dual-boot design §6.5 item 6). Windows kills the tree with taskkill /T instead.
_NEW_GROUP = {} if sys.platform == "win32" else {"start_new_session": True}
KILL_GRACE_S = 2.0


class ClaudeError(Exception):
    pass


def child_env() -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    # No auto memory for the hub's agents: --setting-sources "" doesn't turn it off (measured with
    # 2.1.308), so every run would be told about, and create, ~/.claude/projects/<key>/memory for
    # its cwd: a folder outside the filing worktree, and a stray one per filing.
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    # No telemetry, error reports or update checks in the hub's headless runs. Measured with 2.1.309:
    # start-up 0.5-0.9 s -> 0.2 s, and the exit after the result line 0.7-0.9 s -> 0.02 s, so each
    # filing and answer is done about 1.2-1.6 s sooner (owner feedback: they felt slow).
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


# Isolation from the owner's own Claude Code setup (flags checked against `claude --help`,
# 2.1.305): only the built-in tools a run needs exist at all (--tools; "" = none), no MCP
# servers from any config (--strict-mcp-config, and no --mcp-config is given), no skills or
# slash commands, no session files, and no user/project/local settings files, so the owner's
# plugins, hooks and permission allow-rules never reach these agents. Login still works:
# auth is not a setting (smoke-checked with haiku through run_json and stream_text).
ISOLATION_ARGS = ["--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
                  "--setting-sources", ""]


def build_args(*, model: str, system_prompt_file: Path, allowed_tools: list[str], max_turns: int,
               json_schema: dict | None, stream: bool) -> list[str]:
    args = ["-p", "--model", model, "--append-system-prompt-file", str(system_prompt_file),
            "--max-turns", str(max_turns), "--tools", ",".join(allowed_tools), *ISOLATION_ARGS]
    if allowed_tools:  # --tools says what exists; --allowedTools lets them run unprompted
        args += ["--allowedTools", *allowed_tools]
        if _WRITE_TOOLS & set(allowed_tools):
            args += ["--permission-mode", "acceptEdits"]
    # Always the stream: only it carries the run's rate_limit_event (the plan usage).
    args += ["--output-format", "stream-json", "--verbose"]
    if stream:
        args += ["--include-partial-messages"]
    if json_schema is not None:
        args += ["--json-schema", json.dumps(json_schema, separators=(",", ":"))]
    return args


def _local_bin(name: str) -> str | None:
    """The native installer puts claude in ~/.local/bin, which a hub started at login may not
    have on PATH (P6)."""
    try:
        exe = Path.home() / ".local" / "bin" / (name + (".exe" if sys.platform == "win32" else ""))
    except RuntimeError:  # no home folder at all: construction must still never raise
        return None
    return str(exe) if exe.is_file() else None


class ClaudeRunner:
    def __init__(self, claude_path: str,
                 on_rate_limit: Callable[[dict], Awaitable[None]] | None = None):
        self.claude_path = shutil.which(claude_path) or _local_bin(claude_path)
        self.on_rate_limit = on_rate_limit  # gets each rate_limit_event's rate_limit_info

    async def _rate_limit(self, ev: dict) -> None:
        info = ev.get("rate_limit_info")
        if self.on_rate_limit is None or not isinstance(info, dict):
            return
        try:
            await self.on_rate_limit(info)
        except Exception:  # the meter must never break a filing or an answer
            log.warning("could not record usage from a claude run", exc_info=True)

    async def _spawn(self, args: list[str], prompt: str, cwd: Path) -> asyncio.subprocess.Process:
        if not self.claude_path:
            raise ClaudeError("claude CLI not found")
        proc = await asyncio.create_subprocess_exec(
            self.claude_path, *args, cwd=str(cwd), env=child_env(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=_STREAM_LIMIT, **_NO_WINDOW, **_NEW_GROUP)
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the process died early; its exit code and stderr will explain
        return proc

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is None:
            if sys.platform == "win32":
                # Kill the whole tree (claude may spawn tool subprocesses).
                try:
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/T", "/F", "/PID", str(proc.pid),
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **_NO_WINDOW)
                    await killer.wait()
                except OSError:
                    pass
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            else:
                await ClaudeRunner._kill_group(proc)
        try:
            await proc.wait()
        except Exception:
            pass

    @staticmethod
    async def _kill_group(proc: asyncio.subprocess.Process) -> None:
        """POSIX: SIGTERM the run's process group (claude and its tool children), give it
        KILL_GRACE_S to exit, then SIGKILL whatever is left of the group."""
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return  # the group is already gone
        try:
            await asyncio.wait_for(proc.wait(), KILL_GRACE_S)
        except asyncio.TimeoutError:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    @staticmethod
    def _tail(stderr: bytes) -> str:
        return stderr.decode("utf-8", "replace")[-_STDERR_TAIL:].strip()

    async def _events(self, args: list[str], prompt: str, cwd: Path,
                      timeout_s: int) -> AsyncIterator[dict]:
        """Run claude and yield each stdout line that is a JSON object, as it arrives.

        Only "\n" ends a line: the CLI leaves U+2028, U+2029 and U+0085 raw inside its JSON.
        rate_limit_events go to on_rate_limit as they come, so a run that fails or times out still
        reports its usage. A nonzero exit raises ClaudeError naming the CLI's own error (its result
        event) and the stderr tail; the timeout kills the run's process tree and raises too."""
        proc = await self._spawn(args, prompt, cwd)
        stderr_buf = bytearray()
        error_text = ""

        async def drain():
            # Drain stderr concurrently so a chatty child cannot block on a full pipe.
            while chunk := await proc.stderr.read(65536):
                stderr_buf.extend(chunk)
                del stderr_buf[:-_STDERR_TAIL * 4]
        drain_task = asyncio.ensure_future(drain())
        deadline = time.monotonic() + timeout_s
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                except (ValueError, asyncio.LimitOverrunError):
                    raise ClaudeError("stream line too long") from None
                if not line:
                    break
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") == "rate_limit_event":
                    await self._rate_limit(ev)
                elif ev.get("type") == "result" and ev.get("is_error") and isinstance(ev.get("result"), str):
                    error_text = ev["result"][:300]
                yield ev
            await asyncio.wait_for(proc.wait(), max(deadline - time.monotonic(), 0.1))
            await asyncio.wait_for(drain_task, max(deadline - time.monotonic(), 0.1))
            if proc.returncode != 0:
                detail = " | ".join(x for x in (error_text, self._tail(bytes(stderr_buf))) if x)
                raise ClaudeError(f"claude exited {proc.returncode}: {detail}")
        except asyncio.TimeoutError:
            await self._kill(proc)
            raise ClaudeError(f"claude timed out after {timeout_s}s") from None
        finally:
            if proc.returncode is None:
                await self._kill(proc)
            drain_task.cancel()

    @staticmethod
    async def _steps(ev: dict, on_step: Callable[[str, dict], Awaitable[None]]) -> None:
        """Each tool call in a finished assistant message, as (tool name, its input)."""
        msg = ev.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("name"), str):
                try:
                    await on_step(block["name"], block.get("input"))
                except Exception:  # progress must never break a filing
                    log.warning("could not report a claude step", exc_info=True)

    async def run_json(self, prompt: str, *, cwd: Path, model: str, system_prompt_file: Path,
                       allowed_tools: list[str], json_schema: dict, max_turns: int,
                       timeout_s: int,
                       on_step: Callable[[str, dict], Awaitable[None]] | None = None) -> dict:
        args = build_args(model=model, system_prompt_file=system_prompt_file,
                          allowed_tools=allowed_tools, max_turns=max_turns,
                          json_schema=json_schema, stream=False)
        seen, data = False, None
        async with contextlib.aclosing(self._events(args, prompt, cwd, timeout_s)) as events:
            async for ev in events:
                seen = True
                if ev.get("type") == "result":
                    data = ev  # the last one counts
                elif ev.get("type") == "assistant" and on_step is not None:
                    await self._steps(ev, on_step)
        if not seen:
            raise ClaudeError("unparsable claude output: no JSON object")
        if data is None:
            raise ClaudeError("claude returned no structured output (no result event)")
        structured = data.get("structured_output")
        if not isinstance(structured, dict):
            raise ClaudeError(
                f"claude returned no structured output (subtype={data.get('subtype')!r}, "
                f"is_error={data.get('is_error')!r}, result={str(data.get('result'))[:300]!r})")
        return structured

    async def stream_text(self, prompt: str, *, cwd: Path, model: str, system_prompt_file: Path,
                          allowed_tools: list[str], max_turns: int,
                          timeout_s: int) -> AsyncIterator[str]:
        args = build_args(model=model, system_prompt_file=system_prompt_file,
                          allowed_tools=allowed_tools, max_turns=max_turns,
                          json_schema=None, stream=True)
        async with contextlib.aclosing(self._events(args, prompt, cwd, timeout_s)) as events:
            async for ev in events:
                if ev.get("type") != "stream_event":
                    continue
                inner = ev.get("event")
                if not isinstance(inner, dict):
                    continue
                delta = inner.get("delta")
                if not isinstance(delta, dict):
                    continue
                if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    text = delta.get("text")
                    if text:
                        yield text
