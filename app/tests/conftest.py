import pytest

from brainhub.config import Paths


@pytest.fixture
def paths(tmp_path):
    p = Paths(tmp_path)
    p.data.mkdir(parents=True, exist_ok=True)
    p.brain.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def store(paths):
    from brainhub.store import Store
    s = Store(paths.db)
    s.init_schema()
    return s


@pytest.fixture(autouse=True)
def _isolate_claude_dirs(tmp_path, monkeypatch):
    """Keep tests away from the owner's real Claude config, local app data, the per-OS
    state and config folders and the shared Claude store (claude-sync)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("BRAINHUB_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BRAINHUB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("BRAINHUB_SHARED_DIR", str(tmp_path / "claude-shared"))
    monkeypatch.setenv("BRAINHUB_BIN_DIR", str(tmp_path / "bin"))  # brainhub-request (phase 3)
