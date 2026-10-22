"""Install/remove BrainHub's Claude Code hooks and status line in the owner's user settings."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import osdirs

EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "Notification", "Stop", "StopFailure", "SessionEnd")
# Async hooks never make the owner wait on a downed hub. Stop/SessionEnd stay synchronous so
# they are not cancelled at teardown.
ASYNC_EVENTS = ("UserPromptSubmit", "Notification", "PostToolUse")
MARK = "hook_client.py"
OUR_SUFFIX = "brainhub/hook_client.py"  # our own package path, not any file named hook_client.py
STABLE_SUFFIX = "brainhub/hook/hook_client.py"  # the stable copy in a default state folder (P4)
# Either suffix as whole path components, in a hook's args or a status line command (lowercased,
# forward slashes): "notbrainhub/hook_client.py" or "tools/hook/hook_client.py" is someone else's.
_OURS = re.compile(rf"(?:^|[\s/\"'])(?:{re.escape(OUR_SUFFIX)}|{re.escape(STABLE_SUFFIX)})(?=$|[\s\"'])")
# Both files describe one OS's Claude settings, so they live in that OS's state folder
# (osdirs.state_dir()), never in the shared data/ folder (dual-boot design §6.2).
STATE_FILE = "claude_integration.json"
PASSTHROUGH_FILE = "statusline_passthrough.txt"


class IntegrationError(Exception):
    pass


def default_settings_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "settings.json"


def hook_script() -> Path:
    return Path(__file__).with_name("hook_client.py")


def stable_hook(state_dir: Path) -> Path:
    """The copy of the hook client the update step keeps at a path no slot flip moves (P4)."""
    return Path(state_dir) / "hook" / "hook_client.py"


def console_python(executable: Path) -> Path:
    """pythonw has no usable stdout (the status line needs it): prefer the sibling python.exe
    (`pythonw` → `python` where a POSIX build has one). A POSIX venv's bin/python is returned as it
    is, never resolved: it is a symlink to the base interpreter, which would run without the venv."""
    executable = Path(executable)
    name = executable.name.lower()
    if name in ("pythonw.exe", "pythonw"):
        sibling = executable.with_name("python.exe" if name.endswith(".exe") else "python")
        if sibling.exists():
            return sibling
    return executable


def base_python() -> Path:
    """The interpreter this process's venv was made from (`sys._base_executable`; this python
    itself outside a venv), as a console python. Never resolved: with uv on Linux it is
    `~/.local/share/uv/python/cpython-3.X-<platform>/bin/python3.X`, through uv's minor-version
    link, which a patch upgrade of Python moves to the new patch. Each slot's venv is made from the
    same interpreter, so a slot flip never changes it; only a Python version bump does (a new
    `.python-version`), and then the update step re-syncs the venv while the old interpreter stays
    installed, so the installed hooks keep working until the next install."""
    base = Path(getattr(sys, "_base_executable", None) or sys.executable)
    return console_python(base if base.is_file() else Path(sys.executable))


def hook_paths(state_dir: Path | None = None) -> tuple[str, str]:
    """`(python, script)` for the hooks and the status line (P4, dual-boot design §8.2).

    Runtime mode (`<state>/runtime/current.json` and the stable hook copy both exist): the base
    interpreter and `<state>/hook/hook_client.py`, which no slot flip moves, while the venv
    python and the package's own copy live inside a slot that the next update replaces. The hook
    client is stdlib only and runs with `-I -S`, so it needs no venv. Otherwise (dev): this venv's
    python and the package's hook_client.py, as before."""
    state = Path(state_dir) if state_dir is not None else osdirs.state_dir()
    script = stable_hook(state)
    if (state / "runtime" / "current.json").is_file() and script.is_file():
        return str(base_python()), str(script)
    return str(console_python(Path(sys.executable))), str(hook_script())


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
    """Claude Code runs the status line through a shell. On Linux that is `sh`, so each word is
    quoted for it. On Windows (Git Bash, else PowerShell) the phase 2 form stays: forward slashes,
    double quotes only around a path with a space."""
    if osdirs.os_name() != "windows":
        words = [python, "-I", "-S", script, "statusline", "--port", str(port)]
        if passthrough is not None:
            words += ["--passthrough-file", str(passthrough)]
        return shlex.join(words)
    cmd = f"{_q(_fwd(python))} -I -S {_q(_fwd(script))} statusline --port {port}"
    if passthrough is not None:
        cmd += f" --passthrough-file {_q(_fwd(passthrough))}"
    return cmd


def _norm(text) -> str:
    return str(text).replace("\\", "/").lower()


def _names_ours(text, stable: Path) -> bool:
    """Whether `text` names one of our hook clients as a whole path: the package's own copy, a
    default state folder's stable copy, or `stable`, the stable copy of the state folder in use
    (any name, e.g. under BRAINHUB_STATE_DIR), bare, double-quoted or quoted for `sh`."""
    text = _norm(text)
    if _OURS.search(text):
        return True
    return any(re.search(rf"(?:^|[\s\"']){re.escape(_norm(form))}(?=$|[\s\"'])", text)
               for form in {str(stable), shlex.quote(str(stable))})


def _hook_is_ours(h, stable: Path) -> bool:
    if not isinstance(h, dict):
        return False
    args = h.get("args")
    joined = " ".join(map(str, args)) if isinstance(args, list) else ""
    return _names_ours(joined, stable) or _names_ours(h.get("command", ""), stable)


def _group_hooks(group) -> list:
    hooks = group.get("hooks") if isinstance(group, dict) else None
    return hooks if isinstance(hooks, list) else []


def _group_is_ours(group, stable: Path) -> bool:
    return any(_hook_is_ours(h, stable) for h in _group_hooks(group))


def _line_is_ours(line, stable: Path) -> bool:
    return isinstance(line, dict) and _names_ours(line.get("command", ""), stable)


def _validate(obj: dict, path: Path) -> None:
    def bad(what):
        raise IntegrationError(f"{path}: unexpected settings shape ({what}); refusing to edit it")
    for ev, groups in obj.get("hooks", {}).items():
        if not isinstance(groups, list):
            bad(f"hooks.{ev} is not a list")
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                bad(f"a group in hooks.{ev} is not an object with a hooks list")
            for h in g["hooks"]:
                if not isinstance(h, dict):
                    bad(f"a hook in hooks.{ev} is not an object")
                if "args" in h and not isinstance(h["args"], list):
                    bad(f"a hook's args in hooks.{ev} is not a list")


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
        target, n = path.with_name(f"{path.name}.brainhub-backup-{stamp}"), 1
        while target.exists():
            n += 1
            target = path.with_name(f"{path.name}.brainhub-backup-{stamp}-{n}")
        shutil.copy2(path, target)
    _atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _strip_ours(obj: dict, stable: Path) -> None:
    hooks = obj.get("hooks", {})
    removed = False
    for ev in list(hooks):
        if not any(_group_is_ours(g, stable) for g in hooks[ev]):
            continue  # untouched, even if it is a pre-existing empty list
        removed = True
        kept = []
        for g in hooks[ev]:
            rest = [h for h in g["hooks"] if not _hook_is_ours(h, stable)]
            if not _group_is_ours(g, stable):
                kept.append(g)
            elif rest:
                kept.append({**g, "hooks": rest})
        if kept:
            hooks[ev] = kept
        else:
            del hooks[ev]
    if removed and "hooks" in obj and not hooks:
        del obj["hooks"]


def _load_state(state_dir: Path) -> dict | None:
    try:
        return json.loads((state_dir / STATE_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def adopt_legacy(data_dir: Path, state_dir: Path, os_name: str) -> list[str]:
    """Move the files an older hub kept in the shared data/ folder into this OS's state folder,
    unless the state folder already has either one. Windows only: those files always described
    the Windows install, so Linux never reads or touches them. Returns the names moved."""
    if os_name != "windows":
        return []
    data_dir, state_dir = Path(data_dir), Path(state_dir)
    if any((state_dir / name).exists() for name in (STATE_FILE, PASSTHROUGH_FILE)):
        return []
    moved = []
    for name in (STATE_FILE, PASSTHROUGH_FILE):
        src = data_dir / name
        if not src.is_file():
            continue
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(src, state_dir / name)  # D: to C: is a copy and delete, not a rename
        except OSError as e:
            raise IntegrationError(f"cannot move {src} into {state_dir}: {e}") from e
        moved.append(name)
    return moved


def install(settings_path: Path, *, python: str, script: str, port: int, state_dir: Path,
            data_dir: Path | None = None, now: Callable[[], datetime] = datetime.now) -> dict:
    """`state_dir` is this OS's state folder. `data_dir`, when given, is the shared data/ folder,
    only for adopting an older hub's files (adopt_legacy). `python` and `script` come from
    hook_paths()."""
    settings_path, state_dir = Path(settings_path), Path(state_dir)
    stable = stable_hook(state_dir)
    obj = _read(settings_path)
    _validate(obj, settings_path)
    if data_dir is not None:
        adopt_legacy(data_dir, state_dir, osdirs.os_name())
    _strip_ours(obj, stable)
    hooks = obj.setdefault("hooks", {})
    for ev in EVENTS:
        hooks.setdefault(ev, []).append(
            {"hooks": [hook_entry(python, script, port, is_async=ev in ASYNC_EVENTS)]})

    existing = obj.get("statusLine")
    state = _load_state(state_dir)
    if existing is not None and not _line_is_ours(existing, stable):
        previous = existing
    else:
        previous = state.get("previous_statusline") if state else None
    passthrough = None
    state_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(previous, dict) and previous.get("command"):
        passthrough = state_dir / PASSTHROUGH_FILE
        _atomic_write(passthrough, str(previous["command"]))
    obj["statusLine"] = {"type": "command", "command": statusline_command(python, script, port, passthrough)}
    reinstall = existing is not None and _line_is_ours(existing, stable)
    installed_at = (state or {}).get("installed_at") if reinstall else None
    _atomic_write(state_dir / STATE_FILE, json.dumps(
        {"previous_statusline": previous, "installed_at": installed_at or now().replace(microsecond=0).isoformat()}, indent=2))
    _write_settings(settings_path, obj, now)
    return status(settings_path, port=port, state_dir=state_dir)


def uninstall(settings_path: Path, *, state_dir: Path, data_dir: Path | None = None,
              now: Callable[[], datetime] = datetime.now) -> dict:
    settings_path, state_dir = Path(settings_path), Path(state_dir)
    stable = stable_hook(state_dir)
    obj = _read(settings_path)
    _validate(obj, settings_path)
    if data_dir is not None:
        adopt_legacy(data_dir, state_dir, osdirs.os_name())
    before = json.dumps(obj, sort_keys=True)
    _strip_ours(obj, stable)
    if _line_is_ours(obj.get("statusLine"), stable):
        state = _load_state(state_dir)
        previous = state.get("previous_statusline") if state else None
        if previous is not None:
            obj["statusLine"] = previous
        else:
            del obj["statusLine"]
    if json.dumps(obj, sort_keys=True) != before:
        _write_settings(settings_path, obj, now)
    for name in (STATE_FILE, PASSTHROUGH_FILE):
        (state_dir / name).unlink(missing_ok=True)
    return status(settings_path, port=0, state_dir=state_dir)


def status(settings_path: Path, *, port: int, state_dir: Path | None = None) -> dict:
    """`state_dir` (default: this OS's) names the stable hook copy that counts as ours."""
    settings_path = Path(settings_path)
    stable = stable_hook(state_dir if state_dir is not None else osdirs.state_dir())
    obj = _read(settings_path)
    hooks = obj.get("hooks", {})
    events, port_ok = [], True
    for ev in EVENTS:
        groups = [g for g in hooks.get(ev, []) if _group_is_ours(g, stable)] if isinstance(hooks.get(ev), list) else []
        if groups:
            events.append(ev)
            for g in groups:
                for h in _group_hooks(g):
                    if _hook_is_ours(h, stable) and isinstance(h.get("args"), list) and h["args"][-1:] != [str(port)]:
                        port_ok = False
    line = obj.get("statusLine")
    line_ours = _line_is_ours(line, stable)
    command = str(line["command"]) if line_ours else ""
    if line_ours and f"--port {port} " not in command + " ":
        port_ok = False
    return {"settings_path": str(settings_path), "hooks_installed": bool(events), "events": events,
            "statusline_installed": line_ours, "port_matches": bool(events) and port_ok,
            "previous_statusline": line_ours and "--passthrough-file" in command}
