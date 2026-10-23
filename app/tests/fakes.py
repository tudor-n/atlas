class FakeRunner:
    """Test double for ClaudeRunner. Records every call in `.calls`."""

    def __init__(self, json_result=None, stream_chunks=(), on_run=None, raise_error=None, steps=()):
        self.json_result = json_result
        self.stream_chunks = stream_chunks
        self.on_run = on_run
        self.raise_error = raise_error
        self.steps = steps  # (tool, input) pairs run_json plays through on_step, as the agent works
        self.calls: list[dict] = []

    def _begin(self, prompt, kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        if self.on_run:
            self.on_run(kwargs["cwd"])
        if self.raise_error:
            raise self.raise_error

    async def run_json(self, prompt, **kwargs):
        self._begin(prompt, kwargs)
        for tool, tool_input in self.steps:
            if kwargs.get("on_step"):
                await kwargs["on_step"](tool, tool_input)
        return self.json_result

    async def stream_text(self, prompt, **kwargs):
        self._begin(prompt, kwargs)
        for chunk in self.stream_chunks:
            yield chunk


class FakeRepo:
    """Test double for BrainRepo.push. Records remotes in `.pushes`; `results` scripts ok/fail."""

    def __init__(self, results: list[bool]):
        self.results = list(results)
        self.pushes: list[str] = []

    def push(self, remote_url: str) -> tuple[bool, str]:
        self.pushes.append(remote_url)
        ok = self.results.pop(0)
        return ok, "" if ok else "err"


class Clock:
    """Callable fake clock with a settable `.t`."""

    def __init__(self, t: float = 0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def fake_cli(folder, body: str, name: str = "fake_claude") -> str:
    """A stand-in CLI that runs `body` as a Python script with the test's interpreter.
    Windows gets a .bat wrapper, POSIX an executable shell script. Returns its path."""
    import os
    import sys
    from pathlib import Path
    folder = Path(folder)
    script = folder / f"{name}.py"
    script.write_text(body, encoding="utf-8")
    if sys.platform == "win32":
        wrapper = folder / f"{name}.bat"
        wrapper.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, script), encoding="utf-8")
    else:
        wrapper = folder / name
        wrapper.write_text('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script), encoding="utf-8")
        os.chmod(wrapper, 0o755)
    return str(wrapper)


def pid_gone(pid: int, timeout: float = 5.0) -> bool:
    """POSIX: True once `pid` no longer runs (gone, or a zombie its new parent has yet to reap),
    polling for up to `timeout` seconds."""
    import os
    import time
    from pathlib import Path
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:
            if Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                return True
        except (OSError, IndexError):
            pass
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)


def kill_pid_file(pid_file) -> None:
    """Test clean-up: SIGKILL the process whose pid is in `pid_file`, if it is still there."""
    import os
    import signal
    from pathlib import Path
    try:
        os.kill(int(Path(pid_file).read_text().strip()), signal.SIGKILL)
    except (OSError, ValueError):
        pass
