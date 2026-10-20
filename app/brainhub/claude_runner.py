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
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_STDERR_TAIL = 2000


class ClaudeError(Exception):
    pass


def child_env() -> dict:
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def build_args(*, model: str, system_prompt_file: Path, allowed_tools: list[str], max_turns: int,
               json_schema: dict | None, stream: bool) -> list[str]:
    args = ["-p", "--model", model, "--append-system-prompt-file", str(system_prompt_file),
            "--max-turns", str(max_turns)]
    if allowed_tools:
        args += ["--allowedTools", *allowed_tools]
        if _WRITE_TOOLS & set(allowed_tools):
            args += ["--permission-mode", "acceptEdits"]
    if stream:
        args += ["--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    else:
        args += ["--output-format", "json"]
    if json_schema is not None:
        args += ["--json-schema", json.dumps(json_schema, separators=(",", ":"))]
    return args


class ClaudeRunner:
    def __init__(self, claude_path: str):
        self.claude_path = shutil.which(claude_path)

    async def _spawn(self, args: list[str], prompt: str, cwd: Path) -> asyncio.subprocess.Process:
        if not self.claude_path:
            raise ClaudeError("claude CLI not found")
        proc = await asyncio.create_subprocess_exec(
            self.claude_path, *args, cwd=str(cwd), env=child_env(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
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
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await killer.wait()
                except OSError:
                    pass
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            await proc.wait()
        except Exception:
            pass

    @staticmethod
    def _tail(stderr: bytes) -> str:
        return stderr.decode("utf-8", "replace")[-_STDERR_TAIL:].strip()

    async def run_json(self, prompt: str, *, cwd: Path, model: str, system_prompt_file: Path,
                       allowed_tools: list[str], json_schema: dict, max_turns: int,
                       timeout_s: int) -> dict:
        args = build_args(model=model, system_prompt_file=system_prompt_file,
                          allowed_tools=allowed_tools, max_turns=max_turns,
                          json_schema=json_schema, stream=False)
        proc = await self._spawn(args, prompt, cwd)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
        except asyncio.TimeoutError:
            await self._kill(proc)
            raise ClaudeError(f"claude timed out after {timeout_s}s") from None
        except BaseException:
            await self._kill(proc)
            raise
        if proc.returncode != 0:
            raise ClaudeError(f"claude exited {proc.returncode}: {self._tail(err)}")
        try:
            data = json.loads(out.decode("utf-8"))
        except ValueError as e:
            raise ClaudeError(f"unparsable claude output: {e}") from e
        if not isinstance(data, dict):
            raise ClaudeError("unparsable claude output: not an object")
        structured = data.get("structured_output")
        if not isinstance(structured, dict):
            raise ClaudeError("claude returned no structured output")
        return structured

    async def stream_text(self, prompt: str, *, cwd: Path, model: str, system_prompt_file: Path,
                          allowed_tools: list[str], max_turns: int,
                          timeout_s: int) -> AsyncIterator[str]:
        args = build_args(model=model, system_prompt_file=system_prompt_file,
                          allowed_tools=allowed_tools, max_turns=max_turns,
                          json_schema=None, stream=True)
        proc = await self._spawn(args, prompt, cwd)
        stderr_buf = bytearray()

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
                line = await asyncio.wait_for(proc.stdout.readline(), remaining)
                if not line:
                    break
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") != "stream_event":
                    continue
                inner = ev.get("event") or {}
                delta = inner.get("delta") or {}
                if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    text = delta.get("text")
                    if text:
                        yield text
            await asyncio.wait_for(proc.wait(), max(deadline - time.monotonic(), 0.1))
            await drain_task
            if proc.returncode != 0:
                raise ClaudeError(f"claude exited {proc.returncode}: {self._tail(bytes(stderr_buf))}")
        except asyncio.TimeoutError:
            await self._kill(proc)
            raise ClaudeError(f"claude timed out after {timeout_s}s") from None
        finally:
            if proc.returncode is None:
                await self._kill(proc)
            drain_task.cancel()
