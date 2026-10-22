"""The desktop shell's config file (design §8): `%LOCALAPPDATA%\\BrainHub\\desktop.json`.

Two modes:
- **Legacy (dev):** `{port, root, python, app_dir}`. The shell starts
  `<python> -m brainhub --root <root> --port <port>` in `<app_dir>`.
- **Runtime (plan decision P2):** `{port, root, live, state}`. Before each start the shell runs
  the current slot's `update_step.py --live <live> --state <state>`, then starts the hub from the
  slot `<state>/runtime/current.json` names (`python -m brainhub` in the slot's `app/`).

Without the file the shell only connects to port 8090 and never starts a hub.

`python` is the console python.exe, never pythonw.exe (Ruling 13): the shell starts it with
CREATE_NO_WINDOW, which gives the hub a hidden console that every git, claude and taskkill child
inherits. Under pythonw (no console at all) each of those children would open a visible one.
"""
import json
import os
from pathlib import Path


def default_target() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "BrainHub" / "desktop.json"


def _write(target: Path, data: dict) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return target


def write_desktop_config(*, root: Path, port: int, python: Path, app_dir: Path, target: Path) -> Path:
    return _write(target, {"port": int(port), "root": str(root), "python": str(python), "app_dir": str(app_dir)})


def write_runtime_desktop_config(*, root: Path, port: int, live: Path, state: Path, target: Path) -> Path:
    return _write(target, {"port": int(port), "root": str(root), "live": str(live), "state": str(state)})
