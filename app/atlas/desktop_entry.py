"""The launcher entry for the desktop shell (phase 4.5, design §6): `python -m atlas
desktop-entry install|uninstall|status --exec <binary> [--data-home <dir>]`.

`install` writes `<data home>/applications/dev.atlas.desktop.desktop`, whose `Exec` starts the
shell with `--open` (the shell, or the one already running, then shows its main window), and copies
the shell's icon to `<data home>/icons/hicolor/256x256/apps/dev.atlas.desktop.png`. The data
home is `--data-home`, else `$XDG_DATA_HOME` (absolute), else `~/.local/share`. It is idempotent: a
file already right is left alone. `uninstall` removes only those two files. `status` prints JSON.

Linux only for now: on Windows it says so and exits 2. Stdlib only.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import osdirs

APP_ID = "dev.atlas.desktop"  # the shell's identifier (tauri.conf.json)
DESKTOP_NAME = APP_ID + ".desktop"
ICON_NAME = APP_ID + ".png"
WM_CLASS = "atlas-desktop"  # the binary's name, which GTK makes the X11 WM_CLASS name
ICON_SOURCE = Path(__file__).resolve().parents[1] / "desktop" / "src-tauri" / "icons" / "128x128@2x.png"

# Characters the Desktop Entry spec ("The Exec key") escapes with a backslash inside a quoted
# argument.
_QUOTED_ESCAPES = ('"', "`", "$", "\\")


def _on_windows() -> bool:
    return sys.platform == "win32"


def default_data_home() -> Path:
    return osdirs._xdg("XDG_DATA_HOME", ".local/share")


def paths(data_home: Path) -> tuple[Path, Path]:
    """(the desktop file, the icon) under `data_home`."""
    data_home = Path(data_home)
    return (data_home / "applications" / DESKTOP_NAME,
            data_home / "icons" / "hicolor" / "256x256" / "apps" / ICON_NAME)


def exec_arg(path: str) -> str:
    """`path` as one quoted argument of an `Exec` value, as written in the file: quoted, with
    `"`, `` ` ``, `$` and `\\` backslash-escaped, `%` doubled (it starts a field code), and then
    every backslash doubled again (the value is a string, whose own escape is `\\\\`)."""
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in path):
        raise ValueError("the path has a control character")
    quoted = "".join("\\" + c if c in _QUOTED_ESCAPES else c for c in path)
    quoted = '"' + quoted.replace("%", "%%") + '"'
    return quoted.replace("\\", "\\\\")


def desktop_file(exe: str) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.5",
        "Name=ATLAS",
        "Comment=Your second brain: capture, review and ask",
        f"Exec={exec_arg(exe)} --open",
        f"Icon={APP_ID}",
        f"StartupWMClass={WM_CLASS}",
        "Categories=Office;Utility;",
        # false: the usual click is a second launch (the shell already runs from autostart), which
        # hands `--open` to the running shell and exits at once, before any window of its own. It
        # never completes a startup notification, so `true` left a busy cursor spinning until
        # the desktop's timeout.
        "StartupNotify=false",
        "Terminal=false",
        "",
    ])


def _write_if_changed(path: Path, data: bytes) -> bool:
    """Write `data` to `path` (atomically) unless it already holds it; True when it wrote."""
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return True


def check_exec(exe: Path) -> Path:
    """The binary as an absolute path; ValueError unless it is an executable file."""
    exe = Path(os.path.abspath(exe))
    if not exe.is_file():
        raise ValueError(f"{exe} is not a file")
    if not os.access(exe, os.X_OK):
        raise ValueError(f"{exe} is not executable")
    return exe


def install(exe: Path, data_home: Path, icon_source: Path = ICON_SOURCE) -> dict:
    exe = check_exec(exe)
    desktop, icon = paths(data_home)
    text = desktop_file(str(exe)).encode("utf-8")
    icon_bytes = Path(icon_source).read_bytes()
    wrote_desktop = _write_if_changed(desktop, text)
    wrote_icon = _write_if_changed(icon, icon_bytes)
    return {"desktop_file": str(desktop), "icon": str(icon), "exec": str(exe),
            "changed": wrote_desktop or wrote_icon}


def uninstall(data_home: Path) -> dict:
    removed = []
    for path in paths(data_home):
        try:
            path.unlink()
            removed.append(str(path))
        except FileNotFoundError:
            pass
    return {"removed": removed}


def _exec_line(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("Exec="):
            return line[len("Exec="):]
    return None


def status(data_home: Path, exe: Path | None = None, icon_source: Path = ICON_SOURCE) -> dict:
    desktop, icon = paths(data_home)
    try:
        text = desktop.read_text(encoding="utf-8")
    except OSError:
        text = None
    result = {"desktop_file": str(desktop), "desktop_file_exists": text is not None,
              "icon": str(icon), "icon_exists": icon.is_file(),
              "exec": _exec_line(text) if text is not None else None}
    if exe is not None:
        try:
            current = (text == desktop_file(str(check_exec(exe)))
                       and icon.read_bytes() == Path(icon_source).read_bytes())
        except (OSError, ValueError):
            current = False
        result["current"] = current
    return result


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="atlas desktop-entry")
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--exec", dest="exe", type=Path, default=None,
                    help="the desktop shell's binary (atlas-desktop); required for install")
    ap.add_argument("--data-home", type=Path, default=None,
                    help="instead of $XDG_DATA_HOME or ~/.local/share (tests use a temp folder)")
    args = ap.parse_args(argv)
    if _on_windows():
        print("desktop-entry: not on Windows yet")
        return 2
    data_home = args.data_home or default_data_home()
    try:
        if args.action == "install":
            if args.exe is None:
                ap.error("install needs --exec <binary>")
            result = install(args.exe, data_home)
        elif args.action == "uninstall":
            result = uninstall(data_home)
        else:
            result = status(data_home, args.exe)
    except (OSError, ValueError) as e:
        print(f"desktop-entry: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0
