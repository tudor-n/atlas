"""Install/remove BrainHub's Claude Code hooks and status line in the owner's user settings."""
from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Notification", "Stop", "StopFailure", "SessionEnd")
# Async hooks never make the owner wait on a downed hub. Stop/SessionEnd stay synchronous so
# they are not cancelled at teardown.
ASYNC_EVENTS = ("UserPromptSubmit", "Notification", "PostToolUse")
MARK = "hook_client.py"
STATE_FILE = "claude_integration.json"
PASSTHROUGH_FILE = "statusline_passthrough.txt"


class IntegrationError(Exception):
    pass


def default_settings_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "settings.json"


def hook_script() -> Path:
    return Path(__file__).with_name("hook_client.py")


def console_python(executable: Path) -> Path:
    """pythonw has no usable stdout (the status line needs it): prefer the sibling python.exe."""
    executable = Path(executable)
    if executable.name.lower() == "pythonw.exe":
        sibling = executable.with_name("python.exe")
        if sibling.exists():
            return sibling
    return executable


def hook_entry(python: str, script: str, port: int, *, is_async: bool) -> dict:
    entry = {"type": "command", "command": python,
             "args": ["-I", "-S", script, "event", "--port", str(port)], "timeout": 5}
    if is_async:
        entry["async"] = True
    return entry


def _q(p: str) -> str:
    return f'"{p}"' if " " in p else p


def _fwd(p) -> str:
    return str(p).replace("\\", "/")


def statusline_command(python: str, script: str, port: int, passthrough: Path | None) -> str:
    cmd = f"{_q(_fwd(python))} -I -S {_q(_fwd(script))} statusline --port {port}"
    if passthrough is not None:
        cmd += f" --passthrough-file {_q(_fwd(passthrough))}"
    return cmd


def _group_is_ours(group) -> bool:
    return isinstance(group, dict) and any(
        isinstance(h, dict) and (MARK in " ".join(map(str, h.get("args", []))) or MARK in str(h.get("command", "")))
        for h in group.get("hooks", []))


def _line_is_ours(line) -> bool:
    return isinstance(line, dict) and MARK in str(line.get("command", ""))


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text("utf-8"))
    except (ValueError, OSError) as e:
        raise IntegrationError(f"cannot parse {path}: {e}") from e
    if not isinstance(obj, dict):
        raise IntegrationError(f"{path} is not a JSON object")
    if not isinstance(obj.get("hooks", {}), dict):
        raise IntegrationError(f"{path}: 'hooks' is not an object")
    return obj


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_settings(path: Path, obj: dict, now: Callable[[], datetime]) -> None:
    if path.exists():
        stamp = now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.name}.brainhub-backup-{stamp}"))
    _atomic_write(path, json.dumps(obj, indent=2) + "\n")


def _strip_ours(obj: dict) -> None:
    hooks = obj.get("hooks", {})
    for ev in list(hooks):
        if isinstance(hooks[ev], list):
            hooks[ev] = [g for g in hooks[ev] if not _group_is_ours(g)]
            if not hooks[ev]:
                del hooks[ev]
    if "hooks" in obj and not hooks:
        del obj["hooks"]


def _load_state(data_dir: Path) -> dict | None:
    try:
        return json.loads((data_dir / STATE_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def install(settings_path: Path, *, python: str, script: str, port: int, data_dir: Path,
            now: Callable[[], datetime] = datetime.now) -> dict:
    settings_path = Path(settings_path)
    obj = _read(settings_path)
    _strip_ours(obj)
    hooks = obj.setdefault("hooks", {})
    for ev in EVENTS:
        hooks.setdefault(ev, []).append(
            {"hooks": [hook_entry(python, script, port, is_async=ev in ASYNC_EVENTS)]})

    existing = obj.get("statusLine")
    state = _load_state(data_dir)
    if existing is not None and not _line_is_ours(existing):
        previous = existing
    else:
        previous = state.get("previous_statusline") if state else None
    passthrough = None
    data_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(previous, dict) and previous.get("command"):
        passthrough = data_dir / PASSTHROUGH_FILE
        _atomic_write(passthrough, str(previous["command"]))
    obj["statusLine"] = {"type": "command", "command": statusline_command(python, script, port, passthrough)}
    installed_at = (state or {}).get("installed_at") if existing is not None and _line_is_ours(existing) else None
    _atomic_write(data_dir / STATE_FILE, json.dumps(
        {"previous_statusline": previous, "installed_at": installed_at or now().isoformat()}, indent=2))
    _write_settings(settings_path, obj, now)
    return status(settings_path, port=port)


def uninstall(settings_path: Path, *, data_dir: Path,
              now: Callable[[], datetime] = datetime.now) -> dict:
    settings_path = Path(settings_path)
    obj = _read(settings_path)
    before = json.dumps(obj, sort_keys=True)
    _strip_ours(obj)
    if _line_is_ours(obj.get("statusLine")):
        state = _load_state(data_dir)
        previous = state.get("previous_statusline") if state else None
        if previous is not None:
            obj["statusLine"] = previous
        else:
            del obj["statusLine"]
    if json.dumps(obj, sort_keys=True) != before:
        _write_settings(settings_path, obj, now)
    for name in (STATE_FILE, PASSTHROUGH_FILE):
        (data_dir / name).unlink(missing_ok=True)
    return status(settings_path, port=0)


def status(settings_path: Path, *, port: int) -> dict:
    settings_path = Path(settings_path)
    obj = _read(settings_path)
    hooks = obj.get("hooks", {})
    events, port_ok = [], True
    for ev in EVENTS:
        groups = [g for g in hooks.get(ev, []) if _group_is_ours(g)] if isinstance(hooks.get(ev), list) else []
        if groups:
            events.append(ev)
            for g in groups:
                for h in g["hooks"]:
                    if MARK in " ".join(map(str, h.get("args", []))) and h["args"][-1:] != [str(port)]:
                        port_ok = False
    line = obj.get("statusLine")
    line_ours = _line_is_ours(line)
    command = str(line["command"]) if line_ours else ""
    if line_ours and f"--port {port} " not in command + " ":
        port_ok = False
    return {"settings_path": str(settings_path), "hooks_installed": bool(events), "events": events,
            "statusline_installed": line_ours, "port_matches": bool(events) and port_ok,
            "previous_statusline": line_ours and "--passthrough-file" in command}
