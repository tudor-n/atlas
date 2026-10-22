"""Silent Claude Code hook / status line client for BrainHub.

Run as a file (``python -I -S hook_client.py <mode> --port P``), outside the
package: stdlib imports only, never import anything from ``brainhub``.

Hook rule: ``event`` mode always exits 0 and writes nothing to stdout/stderr.
"""
import http.client
import json
import os
import sys
from datetime import datetime

ALLOWED = ("hook_event_name", "session_id", "cwd", "source", "reason",
           "notification_type", "session_title")
CAPPED = ("message", "title", "error", "error_type")
CAP = 300
CONNECT_TIMEOUT_S = 0.25
EVENT_TIMEOUT_S = 1.0
USAGE_TIMEOUT_S = 0.5
PASSTHROUGH_TIMEOUT_S = 2.0
KILL_GRACE_S = 0.5  # short: the status line must stay quick


def filter_event(d, env):
    out = {k: d[k] for k in ALLOWED if isinstance(d.get(k), str)}
    for k in CAPPED:
        if isinstance(d.get(k), str):
            out[k] = d[k][:CAP]
    out["entrypoint"] = env.get("CLAUDE_CODE_ENTRYPOINT")
    return out


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _kill_group(proc):
    # POSIX: SIGTERM the passthrough's process group, a short grace, then SIGKILL what is left.
    import signal

    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=KILL_GRACE_S)
    except Exception:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass


def _run_passthrough(cmd, raw):
    import subprocess

    win = sys.platform == "win32"
    flags = 0x08000000 if win else 0
    # POSIX: its own process group, so a timeout also kills the shell's children.
    proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=flags, start_new_session=not win)
    try:
        out, _ = proc.communicate(raw, timeout=PASSTHROUGH_TIMEOUT_S)
    except BaseException:
        try:
            if win:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=flags, timeout=2)
                proc.kill()
            else:
                _kill_group(proc)
        except Exception:
            pass
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                pipe.close()
            except Exception:
                pass
        try:
            proc.wait(timeout=0.5)
        except Exception:
            pass
        return None
    return out if proc.returncode == 0 else None


def statusline_text(d):
    parts = []
    model = d.get("model")
    if isinstance(model, dict) and model.get("display_name"):
        parts.append(str(model["display_name"]))
    rl = d.get("rate_limits")
    if isinstance(rl, dict):
        five = rl.get("five_hour")
        if isinstance(five, dict) and _num(five.get("used_percentage")):
            s = f"5h {five['used_percentage']:.0f}%"
            ra = five.get("resets_at")
            if _num(ra):
                try:
                    s += f" ↻{datetime.fromtimestamp(ra):%H:%M}"
                except Exception:
                    pass
            parts.append(s)
        seven = rl.get("seven_day")
        if isinstance(seven, dict) and _num(seven.get("used_percentage")):
            parts.append(f"7d {seven['used_percentage']:.0f}%")
    return " · ".join(parts) if parts else "BrainHub"


def _post(port, path, payload, timeout):
    # Short connect timeout (Windows retries refused localhost connects for ~1 s),
    # then the full budget for send + response.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=CONNECT_TIMEOUT_S)
    try:
        conn.connect()
        conn.sock.settimeout(timeout)
        conn.request("POST", path, body=json.dumps(payload).encode("utf-8"),
                     headers={"Content-Type": "application/json"})
        conn.getresponse().read()
    finally:
        conn.close()


def _arg(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _read_stdin():
    raw = sys.stdin.buffer.read()
    try:
        d = json.loads(raw.decode("utf-8"))
    except Exception:
        d = {}
    return raw, d if isinstance(d, dict) else {}


def _event(port):
    _, d = _read_stdin()
    _post(port, "/api/hooks/event", filter_event(d, os.environ), EVENT_TIMEOUT_S)


def _statusline(port, passthrough):
    raw, d = _read_stdin()
    rl = d.get("rate_limits")
    if isinstance(rl, dict):
        try:
            _post(port, "/api/usage",
                  {"rate_limits": rl, "session_id": d.get("session_id")}, USAGE_TIMEOUT_S)
        except Exception:
            pass
    line = None
    if passthrough:
        try:
            with open(passthrough, encoding="utf-8") as f:
                cmd = f.read().strip()
            out = _run_passthrough(cmd, raw)
            if out is not None:
                text = out.decode("utf-8", errors="replace").strip()
                line = text.splitlines()[0] if text else None
        except Exception:
            line = None
    if not line:
        line = statusline_text(d)
    sys.stdout.buffer.write(line.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def main(argv):
    try:
        mode = argv[0] if argv else ""
        port = int(_arg(argv, "--port"))
        if mode == "event":
            _event(port)
        elif mode == "statusline":
            _statusline(port, _arg(argv, "--passthrough-file"))
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
