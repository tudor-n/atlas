"""The desktop shell's config file (design §8): `%LOCALAPPDATA%\\BrainHub\\desktop.json`.

The Tauri shell reads `{port, root, python, app_dir}` from it to know which hub to show and how
to start one (`<python> -m brainhub --root <root>` in `<app_dir>`). Without the file the shell
only connects to port 8090 and never starts a hub.
"""
import json
import os
from pathlib import Path


def default_target() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "BrainHub" / "desktop.json"


def pythonw_for(executable: Path) -> Path:
    """The console-less pythonw.exe beside `executable` if there is one, else `executable`."""
    executable = Path(executable)
    sibling = executable.with_name("pythonw.exe")
    return sibling if sibling.exists() else executable


def write_desktop_config(*, root: Path, port: int, python: Path, app_dir: Path, target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {"port": int(port), "root": str(root), "python": str(python), "app_dir": str(app_dir)}
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return target
