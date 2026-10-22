"""`python -m brainhub runtime init|status` (plan decisions P2 and P3, ruling D9).

`init` builds the first runtime slot from the live copy's HEAD, without the pull part; after that
the desktop shell runs `update_step.py` before every hub start. `status` shows the current slot
and what the last update step did. The slots themselves are built by `update_step.py`.
"""
import os
from pathlib import Path

from . import update_step

# A first venv sync may also have uv download Python itself: give it longer than a start does.
INIT_BUDGET_S = 600


class InitRefused(Exception):
    """The live HEAD fails the provenance guard (D9): not on master, or not on origin/master."""


def init(live: Path, state: Path, *, budget: float = INIT_BUDGET_S) -> dict:
    """Build the first slot. Raises InitRefused, before touching anything, when the live HEAD
    isn't a commit on master that the local origin/master has."""
    reason = update_step.provenance_reason(str(live))
    if reason:
        raise InitRefused(reason)
    return update_step.run_step(str(live), str(state), budget=budget, pull_first=False)


def status(state: Path) -> dict:
    return {"current": update_step.read_json(os.path.join(state, "runtime", "current.json")),
            "update": update_step.read_json(os.path.join(state, "update.json"))}


# The folder holding the running package and web/: `<state>/runtime/slot-<x>/app` when the shell
# started this hub from a runtime slot, the dev checkout otherwise.
APP_DIR = Path(__file__).resolve().parent.parent


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def hub_view(state: Path, app_dir: Path | None = None) -> dict | None:
    """`/api/state.runtime`: None unless this hub runs from one of `state`'s runtime slots (a dev
    or legacy hub, even with runtime slots present). Otherwise the current slot and commit
    (`runtime/current.json`), the last update step's time and pending reason (`update.json`), and
    whether the desktop shell is older than the live copy's shell source (`update.json`'s
    `shell_source` against the one the running shell wrote to `shell.json` at start)."""
    app_dir = Path(app_dir or APP_DIR)
    try:
        runtime_dir = (Path(state) / "runtime").resolve()
        app_dir = app_dir.resolve()
    except OSError:
        return None
    if not (app_dir.name == "app" and app_dir.parent.name in ("slot-a", "slot-b")
            and app_dir.parent.parent == runtime_dir):
        return None
    current = update_step.read_json(os.path.join(state, "runtime", "current.json")) or {}
    update = update_step.read_json(os.path.join(state, "update.json")) or {}
    shell = update_step.read_json(os.path.join(state, "shell.json")) or {}
    live_shell, own_shell = _text(update.get("shell_source")), _text(shell.get("shell_source"))
    return {"commit": _text(current.get("commit")),
            "slot": current.get("slot") if current.get("slot") in update_step.SLOTS else None,
            "checked_at": _text(update.get("checked")), "pending": _text(update.get("pending")),
            "shell_update": bool(live_shell and own_shell and live_shell != own_shell)}
