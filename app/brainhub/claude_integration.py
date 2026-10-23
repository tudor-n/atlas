"""Install/remove BrainHub's Claude Code hooks and status line in the owner's user settings, and
(phase 3) the `brainhub-request` command with its one line in the owner's user CLAUDE.md."""
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


# Phase 3: measurement requests (spec §7.3). The command is a wrapper in osdirs.bin_dir() around the
# stable copy of request_client.py; the CLAUDE.md beside the target settings.json gets one line.
REQUEST_SCRIPT = "request_client.py"
WRAPPER_MARK = "written by `brainhub integration install`"
MD_BEGIN = "<!-- brainhub: measurement requests (managed by `brainhub integration install`) -->"
MD_END = "<!-- /brainhub -->"
MD_LINE = ("Need measurements from the owner's workbench (lengths, angles, IDs)? Run "
           "`brainhub-request \"<title>\" \"<item>\"…`; answers arrive later in BrainHub, not here.")
_MD_BLOCK = re.compile(rf"\n*{re.escape(MD_BEGIN)}.*?{re.escape(MD_END)}\n?", re.S)


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
    installed, so the installed hooks keep working until the next install.

    Only runtime mode uses it. IntegrationError when that interpreter is gone: falling back to the
    slot venv's python would write a path the next slot flip removes."""
    base = Path(getattr(sys, "_base_executable", None) or sys.executable)
    if not base.is_file():
        raise IntegrationError(f"the base Python of this runtime is missing ({base}); "
                               "run `python -m brainhub runtime init` again")
    return console_python(base)


def hook_paths(state_dir: Path | None = None) -> tuple[str, str]:
    """`(python, script)` for the hooks and the status line (P4, dual-boot design §8.2).

    Runtime mode (`<state>/runtime/current.json` and the stable hook copy both exist): the base
    interpreter and `<state>/hook/hook_client.py`, which no slot flip moves, while the venv
    python and the package's own copy live inside a slot that the next update replaces. The hook
    client is stdlib only and runs with `-I -S`, so it needs no venv. Otherwise (dev): this venv's
    python and the package's hook_client.py, as before. IntegrationError (from base_python) when
    the runtime's base interpreter is gone."""
    state = Path(state_dir) if state_dir is not None else osdirs.state_dir()
    script = stable_hook(state)
    if (state / "runtime" / "current.json").is_file() and script.is_file():
        return str(base_python()), str(script)
    return str(console_python(Path(sys.executable))), str(hook_script())


def request_paths(state_dir: Path | None = None) -> tuple[str, str]:
    """`(python, script)` for brainhub-request, chosen like hook_paths(): in runtime mode the base
    interpreter and the stable `<state>/hook/request_client.py` the update step keeps; otherwise this
    venv's python and the package's copy."""
    state = Path(state_dir) if state_dir is not None else osdirs.state_dir()
    script = state / "hook" / REQUEST_SCRIPT
    if (state / "runtime" / "current.json").is_file() and script.is_file():
        return str(base_python()), str(script)
    return str(console_python(Path(sys.executable))), str(Path(__file__).with_name(REQUEST_SCRIPT))


def wrapper_path(bin_dir: Path) -> Path:
    return Path(bin_dir) / ("brainhub-request.cmd" if sys.platform == "win32" else "brainhub-request")


def wrapper_text(python: str, script: str, port: int) -> str:
    if sys.platform == "win32":
        return (f"@rem brainhub-request: measurement requests for the workbench tablet ({WRAPPER_MARK})\r\n"
                f'@"{python}" -I -S "{script}" --port {port} %*\r\n')
    return ("#!/bin/sh\n"
            f"# brainhub-request: measurement requests for the workbench tablet ({WRAPPER_MARK})\n"
            f"exec {shlex.join([python, '-I', '-S', script, '--port', str(port)])} \"$@\"\n")


def _wrapper_is_ours(path: Path) -> bool:
    try:
        return WRAPPER_MARK in path.read_text(encoding="utf-8", errors="replace")[:400]
    except OSError:
        return False


def _backup(path: Path, now: Callable[[], datetime]) -> None:
    if path.exists():
        stamp = now().strftime("%Y%m%d-%H%M%S")
        target, n = path.with_name(f"{path.name}.brainhub-backup-{stamp}"), 1
        while target.exists():
            n += 1
            target = path.with_name(f"{path.name}.brainhub-backup-{stamp}-{n}")
        shutil.copy2(path, target)


def _md_without_ours(text: str) -> str:
    return _MD_BLOCK.sub("\n", text).rstrip("\n")


def install_request_command(claude_md: Path, bin_dir: Path, *, python: str, script: str, port: int,
                            now: Callable[[], datetime] = datetime.now) -> None:
    """Write the brainhub-request wrapper (refusing to replace a file of the same name that isn't
    ours) and our one CLAUDE.md line between markers, keeping everything else in the file. The file
    is backed up before it changes, as settings.json is."""
    wrapper = wrapper_path(bin_dir)
    if wrapper.exists() and not _wrapper_is_ours(wrapper):
        raise IntegrationError(f"{wrapper} exists and isn't BrainHub's; nothing was changed")
    claude_md = Path(claude_md)
    old = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    rest = _md_without_ours(old)
    new = (rest + "\n\n" if rest.strip() else "") + f"{MD_BEGIN}\n{MD_LINE}\n{MD_END}\n"
    if new != old:
        _backup(claude_md, now)
        _atomic_write(claude_md, new)
    _atomic_write(wrapper, wrapper_text(python, script, port))
    if sys.platform != "win32":
        os.chmod(wrapper, 0o755)


def uninstall_request_command(claude_md: Path, bin_dir: Path, *,
                              now: Callable[[], datetime] = datetime.now) -> None:
    wrapper = wrapper_path(bin_dir)
    if wrapper.exists() and _wrapper_is_ours(wrapper):
        wrapper.unlink()
    claude_md = Path(claude_md)
    if claude_md.exists():
        old = claude_md.read_text(encoding="utf-8")
        if MD_BEGIN in old:
            rest = _md_without_ours(old)
            _backup(claude_md, now)
            if rest.strip():
                _atomic_write(claude_md, rest + "\n")
            else:
                claude_md.unlink()  # it held only our line


def request_command_status(claude_md: Path, bin_dir: Path) -> bool:
    try:
        md = Path(claude_md).read_text(encoding="utf-8") if Path(claude_md).exists() else ""
    except OSError:
        md = ""
    return MD_BEGIN in md and _wrapper_is_ours(wrapper_path(bin_dir))


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


def _statusline_head(python: str, script: str) -> str:
    """The status line command up to the script: `<python> -I -S <script>`, in this OS's form."""
    if osdirs.os_name() != "windows":
        return shlex.join([python, "-I", "-S", script])
    return f"{_q(_fwd(python))} -I -S {_q(_fwd(script))}"


def statusline_command(python: str, script: str, port: int, passthrough: Path | None) -> str:
    """Claude Code runs the status line through a shell. On Linux that is `sh`, so each word is
    quoted for it. On Windows (Git Bash, else PowerShell) the phase 2 form stays: forward slashes,
    double quotes only around a path with a space."""
    cmd = f"{_statusline_head(python, script)} statusline --port {port}"
    if passthrough is not None:
        quoted = shlex.quote(str(passthrough)) if osdirs.os_name() != "windows" else _q(_fwd(passthrough))
        cmd += f" --passthrough-file {quoted}"
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
    r_python, r_script = request_paths(state_dir)
    install_request_command(settings_path.parent / "CLAUDE.md", osdirs.bin_dir(),
                            python=r_python, script=r_script, port=port, now=now)
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
    uninstall_request_command(settings_path.parent / "CLAUDE.md", osdirs.bin_dir(), now=now)
    return status(settings_path, port=0, state_dir=state_dir)


def status(settings_path: Path, *, port: int, state_dir: Path | None = None) -> dict:
    """`state_dir` (default: this OS's) names the stable hook copy that counts as ours.

    `paths_current`: something of ours is installed, every hook of ours (and our status line) runs
    exactly the `(python, script)` that hook_paths() gives now, and both files exist. False after
    `runtime init` on a dev install, or when the interpreter or script it names is gone: a
    reinstall puts it right."""
    settings_path = Path(settings_path)
    state = Path(state_dir) if state_dir is not None else osdirs.state_dir()
    stable = stable_hook(state)
    obj = _read(settings_path)
    try:
        want = hook_paths(state)
    except IntegrationError:
        want = None  # the runtime's base interpreter is gone: nothing installed can be current
    hooks = obj.get("hooks", {})
    events, port_ok = [], True
    current = want is not None and all(os.path.isfile(p) for p in want)
    for ev in EVENTS:
        groups = [g for g in hooks.get(ev, []) if _group_is_ours(g, stable)] if isinstance(hooks.get(ev), list) else []
        if groups:
            events.append(ev)
            for g in groups:
                for h in _group_hooks(g):
                    if not _hook_is_ours(h, stable):
                        continue
                    args = h.get("args") if isinstance(h.get("args"), list) else []
                    if args[-1:] != [str(port)]:
                        port_ok = False
                    if want is None or h.get("command") != want[0] or args[2:3] != [want[1]]:
                        current = False
    line = obj.get("statusLine")
    line_ours = _line_is_ours(line, stable)
    command = str(line["command"]) if line_ours else ""
    if line_ours and f"--port {port} " not in command + " ":
        port_ok = False
    if line_ours and (want is None or not command.startswith(_statusline_head(*want) + " ")):
        current = False
    return {"settings_path": str(settings_path), "hooks_installed": bool(events), "events": events,
            "statusline_installed": line_ours, "port_matches": bool(events) and port_ok,
            "paths_current": (bool(events) or line_ours) and current,
            "previous_statusline": line_ours and "--passthrough-file" in command,
            "request_command": request_command_status(settings_path.parent / "CLAUDE.md", osdirs.bin_dir())}
