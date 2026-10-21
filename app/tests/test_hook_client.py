import json
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "brainhub" / "hook_client.py"


def _load():
    import importlib.util

    spec = importlib.util.spec_from_file_location("hook_client_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Hub:
    def __init__(self, delay: float = 0.0):
        self.bodies: list[tuple[str, dict]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                if delay:
                    time.sleep(delay)
                try:
                    outer.bodies.append((self.path, json.loads(body)))
                    self.send_response(204)
                    self.end_headers()
                except Exception:
                    pass

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hub():
    h = Hub()
    try:
        yield h
    finally:
        h.close()


def run(mode, port, stdin: bytes, *extra):
    t = time.monotonic()
    p = subprocess.run(
        [sys.executable, "-I", "-S", str(HOOK), mode, "--port", str(port), *extra],
        input=stdin, capture_output=True, timeout=10,
    )
    return p, time.monotonic() - t


def baseline() -> float:
    t = time.monotonic()
    subprocess.run([sys.executable, "-I", "-S", "-c", "pass"], timeout=10)
    return time.monotonic() - t


def test_filter_event_allowlist():
    hc = _load()
    d = {
        "hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "C:\\x",
        "prompt": "secret", "tool_input": {"a": 1}, "tool_response": "r",
        "transcript_path": "t", "message": "m" * 1000,
    }
    out = hc.filter_event(d, {"CLAUDE_CODE_ENTRYPOINT": "cli"})
    for k in ("prompt", "tool_input", "tool_response", "transcript_path"):
        assert k not in out
    assert len(out["message"]) == 300
    assert out["entrypoint"] == "cli"
    assert out["hook_event_name"] == "UserPromptSubmit"


def test_statusline_text():
    hc = _load()
    T = 1_900_000_000
    d = {"model": {"display_name": "Sonnet"},
         "rate_limits": {"five_hour": {"used_percentage": 62.4, "resets_at": T},
                         "seven_day": {"used_percentage": 31}}}
    assert hc.statusline_text(d) == f"Sonnet · 5h 62% ↻{datetime.fromtimestamp(T):%H:%M} · 7d 31%"
    assert hc.statusline_text({}) == "BrainHub"


def test_event_posts_to_hub(hub):
    p, _ = run("event", hub.port, json.dumps(
        {"hook_event_name": "SessionStart", "prompt": "nope"}).encode())
    assert p.returncode == 0
    assert p.stdout == b"" and p.stderr == b""
    path, body = hub.bodies[0]
    assert path == "/api/hooks/event"
    assert body["hook_event_name"] == "SessionStart"
    assert "prompt" not in body


def test_event_hub_down_is_silent_and_fast():
    base = baseline()
    p, dt = run("event", free_port(), b'{"hook_event_name": "Stop"}')
    print(f"hub-down wall={dt:.3f}s baseline={base:.3f}s")
    assert p.returncode == 0 and p.stdout == b"" and p.stderr == b""
    assert dt < base + 1.5


def test_event_hub_slow_times_out_silently():
    h = Hub(delay=5)
    try:
        base = baseline()
        p, dt = run("event", h.port, b'{"hook_event_name": "Stop"}')
        print(f"hub-slow wall={dt:.3f}s baseline={base:.3f}s")
        assert p.returncode == 0 and p.stdout == b"" and p.stderr == b""
        assert dt < base + 2
    finally:
        h.close()


@pytest.mark.parametrize("stdin", [b"not json", b"", b"[1,2]", b"\xff\xfe"])
def test_event_garbage_stdin_is_silent(stdin):
    p, _ = run("event", free_port(), stdin)
    assert p.returncode == 0 and p.stdout == b"" and p.stderr == b""


def test_statusline_posts_usage_and_prints(hub):
    hc = _load()
    d = {"session_id": "s", "model": {"display_name": "Opus"},
         "rate_limits": {"five_hour": {"used_percentage": 10, "resets_at": 1_900_000_000}}}
    p, _ = run("statusline", hub.port, json.dumps(d).encode())
    assert p.returncode == 0
    assert p.stdout == (hc.statusline_text(d) + "\n").encode("utf-8")
    path, body = hub.bodies[0]
    assert path == "/api/usage"
    assert body == {"rate_limits": d["rate_limits"], "session_id": "s"}


def test_statusline_without_rate_limits_does_not_post(hub):
    p, _ = run("statusline", hub.port, b'{"model": {"display_name": "X"}}')
    assert p.returncode == 0 and p.stdout == b"X\n"
    assert hub.bodies == []


def test_statusline_passthrough(hub, tmp_path):
    f = tmp_path / "pt.txt"
    f.write_text("echo hello", encoding="utf-8")
    d = b'{"model": {"display_name": "X"}}'
    p, _ = run("statusline", hub.port, d, "--passthrough-file", str(f))
    assert p.returncode == 0 and p.stdout == b"hello\n"

    f.write_text("exit 3", encoding="utf-8")
    p, _ = run("statusline", hub.port, d, "--passthrough-file", str(f))
    assert p.returncode == 0 and p.stdout == b"X\n"
