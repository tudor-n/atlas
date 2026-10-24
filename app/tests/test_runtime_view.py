"""`/api/state.runtime`: what the hub reports about its runtime slot (plan Task 9)."""
import json
from pathlib import Path

import pytest

from atlas import runtime
from atlas.server import create_app
from tests.test_server import make_runner


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _slot(state: Path, slot="a", commit="0123456789abcdef0123456789abcdef01234567", *, live_shell="s1",
          own_shell="s1", pending=None) -> Path:
    """A runtime layout in `state` whose slot `slot` is current; returns that slot's app folder."""
    app = state / "runtime" / f"slot-{slot}" / "app"
    app.mkdir(parents=True)
    _write(state / "runtime" / "current.json", {"slot": slot, "commit": commit, "deps": "d"})
    _write(state / "update.json", {"pulled": False, "slot": slot, "commit": commit, "pending": pending,
                                   "checked": "2026-10-22T14:05:09", "shell_source": live_shell,
                                   "stderr": None})
    _write(state / "shell.json", {"shell_source": own_shell})
    return app


def test_dev_hub_has_no_runtime_even_with_slots(tmp_path):
    state = tmp_path / "state"
    _slot(state)
    assert runtime.hub_view(state, tmp_path / "dev" / "app") is None
    assert runtime.hub_view(state) is None  # the test run's own checkout
    # A slot of another state folder isn't ours either.
    other = _slot(tmp_path / "other-state", "b")
    assert runtime.hub_view(state, other) is None


def test_hub_in_a_slot_reports_commit_slot_and_check(tmp_path):
    state = tmp_path / "state"
    app = _slot(state, "b")
    assert runtime.hub_view(state, app) == {
        "commit": "0123456789abcdef0123456789abcdef01234567", "slot": "b",
        "checked_at": "2026-10-22T14:05:09", "pending": None, "shell_update": False}


def test_pending_reason_and_shell_update(tmp_path):
    state = tmp_path / "state"
    app = _slot(state, live_shell="new", own_shell="old", pending="CI pending")
    view = runtime.hub_view(state, app)
    assert view["pending"] == "CI pending" and view["shell_update"] is True


@pytest.mark.parametrize("live_shell, own_shell", [(None, "s1"), ("s1", None), (None, None), ("", "s1")])
def test_unknown_shell_source_is_no_update(tmp_path, live_shell, own_shell):
    state = tmp_path / "state"
    app = _slot(state, live_shell=live_shell, own_shell=own_shell)
    assert runtime.hub_view(state, app)["shell_update"] is False


def test_missing_or_damaged_files_give_nulls(tmp_path):
    state = tmp_path / "state"
    app = _slot(state)
    (state / "update.json").write_text("{ nope", encoding="utf-8")
    (state / "shell.json").unlink()
    _write(state / "runtime" / "current.json", {"slot": "z", "commit": 7})
    assert runtime.hub_view(state, app) == {"commit": None, "slot": None, "checked_at": None, "pending": None,
                                            "shell_update": False}


async def test_api_state_runtime_null_in_dev(aiohttp_client, paths):
    c = await aiohttp_client(create_app(paths, runner=make_runner(), start_workers=False))
    state = await (await c.get("/api/state")).json()
    assert "runtime" in state and state["runtime"] is None


async def test_api_state_runtime_from_the_state_folder(aiohttp_client, paths, tmp_path, monkeypatch):
    # conftest points ATLAS_STATE_DIR at <tmp>/state; pretend this hub runs from its slot a.
    app_dir = _slot(tmp_path / "state", live_shell="new", own_shell="old", pending="CI pending")
    monkeypatch.setattr(runtime, "APP_DIR", app_dir)
    c = await aiohttp_client(create_app(paths, runner=make_runner(), start_workers=False))
    state = await (await c.get("/api/state")).json()
    assert state["runtime"] == {"commit": "0123456789abcdef0123456789abcdef01234567", "slot": "a",
                                "checked_at": "2026-10-22T14:05:09", "pending": "CI pending",
                                "shell_update": True}
