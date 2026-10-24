import pytest

from atlas.config import Paths


@pytest.fixture
def paths(tmp_path):
    p = Paths(tmp_path)
    p.data.mkdir(parents=True, exist_ok=True)
    p.brain.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def store(paths):
    from atlas.store import Store
    s = Store(paths.db)
    s.init_schema()
    return s


@pytest.fixture(autouse=True)
def _isolate_claude_dirs(tmp_path, monkeypatch):
    """Keep tests away from the owner's real Claude config, local app data, the per-OS
    state and config folders, the shared Claude store (claude-sync) and the XDG data home
    (desktop-entry's launcher files; its tests also pass --data-home)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("ATLAS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ATLAS_SHARED_DIR", str(tmp_path / "claude-shared"))
    monkeypatch.setenv("ATLAS_BIN_DIR", str(tmp_path / "bin"))  # atlas-request (phase 3)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))  # desktop-entry (phase 4.5)
