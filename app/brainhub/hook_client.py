"""Silent Claude Code hook / status line client for BrainHub.

Run as a file (``python -I -S hook_client.py <mode> --port P``), outside the
package: stdlib imports only, never import anything from ``brainhub``.

Hook rule: ``event`` mode always exits 0 and writes nothing to stdout/stderr.
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime

ALLOWED = ("hook_event_name", "session_id", "cwd", "source", "reason",
           "notification_type", "session_title")
CAPPED = ("message", "title", "error", "error_type")
CAP = 300


def filter_event(d, env):
    out = {k: d[k] for k in ALLOWED if isinstance(d.get(k), str)}
    for k in CAPPED:
        if isinstance(d.get(k), str):
            out[k] = d[k][:CAP]
    out["entrypoint"] = env.get("CLAUDE_CODE_ENTRYPOINT")
    return out


def statusline_text(d):
    parts = []
    model = d.get("model")
    if isinstance(model, dict) and model.get("display_name"):
        parts.append(str(model["display_name"]))
    rl = d.get("rate_limits")
    if isinstance(rl, dict):
        five = rl.get("five_hour")
        if isinstance(five, dict) and isinstance(five.get("used_percentage"), (int, float)):
            s = f"5h {five['used_percentage']:.0f}%"
            ra = five.get("resets_at")
            if isinstance(ra, (int, float)):
                s += f" ↻{datetime.fromtimestamp(ra):%H:%M}"
            parts.append(s)
        seven = rl.get("seven_day")
        if isinstance(seven, dict) and isinstance(seven.get("used_percentage"), (int, float)):
            parts.append(f"7d {seven['used_percentage']:.0f}%")
    return " · ".join(parts) if parts else "BrainHub"


def _post(port, path, payload, timeout):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=timeout).close()


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
    _post(port, "/api/hooks/event", filter_event(d, os.environ), 1.0)


def _statusline(port, passthrough):
    raw, d = _read_stdin()
    rl = d.get("rate_limits")
    if isinstance(rl, dict):
        try:
            _post(port, "/api/usage",
                  {"rate_limits": rl, "session_id": d.get("session_id")}, 0.5)
        except Exception:
            pass
    line = None
    if passthrough:
        try:
            with open(passthrough, encoding="utf-8") as f:
                cmd = f.read().strip()
            r = subprocess.run(cmd, shell=True, input=raw, capture_output=True, timeout=2)
            if r.returncode == 0:
                text = r.stdout.decode("utf-8", errors="replace").strip()
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
