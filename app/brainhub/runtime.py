"""`python -m brainhub runtime init|status` (plan decisions P2 and P3).

`init` builds the first runtime slot from the live copy's HEAD, without the pull part; after that
the desktop shell runs `update_step.py` before every hub start. `status` shows the current slot
and what the last update step did. The slots themselves are built by `update_step.py`.
"""
import os
from pathlib import Path

from . import update_step

# A first venv sync may also have uv download Python itself: give it longer than a start does.
INIT_TIMEOUT_S = 600


def init(live: Path, state: Path, *, timeout: float = INIT_TIMEOUT_S) -> dict:
    return update_step.run_step(str(live), str(state), timeout=timeout, pull_first=False)


def status(state: Path) -> dict:
    return {"current": update_step.read_json(os.path.join(state, "runtime", "current.json")),
            "update": update_step.read_json(os.path.join(state, "update.json"))}
