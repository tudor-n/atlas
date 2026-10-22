import os
import re
import sys
from pathlib import Path

import pytest

from brainhub import osdirs

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX folder rules")
win_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows folder rules")


def _home(monkeypatch, home):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))


@pytest.fixture
def fresh_boot_id():
    osdirs.boot_id.cache_clear()
    yield
    osdirs.boot_id.cache_clear()


def test_os_name():
    assert osdirs.os_name() == ("windows" if sys.platform == "win32" else "linux")


def test_tests_never_see_the_real_folders(tmp_path):
    assert osdirs.state_dir() == tmp_path / "state"
    assert osdirs.config_dir() == tmp_path / "config"


def test_state_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAINHUB_STATE_DIR", str(tmp_path / "elsewhere"))
    assert osdirs.state_dir() == tmp_path / "elsewhere"
    assert not (tmp_path / "elsewhere").exists()  # never created here


def test_config_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAINHUB_CONFIG_DIR", str(tmp_path / "elsewhere"))
    assert osdirs.config_dir() == tmp_path / "elsewhere"
    assert not (tmp_path / "elsewhere").exists()


@posix_only
def test_state_dir_linux_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAINHUB_STATE_DIR")
    _home(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert osdirs.state_dir() == tmp_path / "xdg" / "brainhub"
    monkeypatch.setenv("XDG_DATA_HOME", "")  # empty counts as unset, like ${XDG_DATA_HOME:-…}
    assert osdirs.state_dir() == tmp_path / "home" / ".local" / "share" / "brainhub"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert osdirs.state_dir() == tmp_path / "home" / ".local" / "share" / "brainhub"
    assert not (tmp_path / "home").exists() and not (tmp_path / "xdg").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="XDG rules")
def test_relative_xdg_value_is_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAINHUB_STATE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", "relative/xdg")
    assert osdirs.state_dir() == tmp_path / "home" / ".local" / "share" / "brainhub"


@posix_only
def test_config_dir_linux_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAINHUB_CONFIG_DIR")
    _home(monkeypatch, tmp_path / "home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert osdirs.config_dir() == tmp_path / "xdg" / "brainhub"
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    assert osdirs.config_dir() == tmp_path / "home" / ".config" / "brainhub"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert osdirs.config_dir() == tmp_path / "home" / ".config" / "brainhub"
    assert not (tmp_path / "home").exists() and not (tmp_path / "xdg").exists()


@win_only
def test_state_dir_windows_localappdata(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAINHUB_STATE_DIR")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))  # not a Windows rule
    assert osdirs.state_dir() == tmp_path / "lad" / "BrainHub"
    assert not (tmp_path / "lad").exists()


@win_only
def test_config_dir_windows_localappdata(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAINHUB_CONFIG_DIR")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert osdirs.config_dir() == tmp_path / "lad" / "BrainHub"
    assert not (tmp_path / "lad").exists()


def test_boot_id_is_stable_within_a_process(fresh_boot_id):
    first = osdirs.boot_id()
    assert first and osdirs.boot_id() == first


@posix_only
def test_boot_id_linux_reads_proc(tmp_path, monkeypatch, fresh_boot_id):
    f = tmp_path / "boot_id"
    f.write_text("6f1c2e0a-1111-2222-3333-444455556666\n", "ascii")
    monkeypatch.setattr(osdirs, "LINUX_BOOT_ID", f)
    assert osdirs.boot_id() == "6f1c2e0a-1111-2222-3333-444455556666"


@posix_only
@pytest.mark.parametrize("content", [None, ""])
def test_boot_id_linux_unreadable_is_unknown(tmp_path, monkeypatch, fresh_boot_id, content):
    f = tmp_path / "boot_id"
    if content is not None:
        f.write_text(content, "ascii")
    monkeypatch.setattr(osdirs, "LINUX_BOOT_ID", f)
    assert osdirs.boot_id() == f"unknown-{os.getpid()}"


@win_only
def test_boot_id_windows_from_boot_counter(fresh_boot_id):
    assert re.fullmatch(r"win-boot-\d+", osdirs.boot_id())


@win_only
def test_boot_id_windows_falls_back_to_boot_minute(monkeypatch, fresh_boot_id):
    def unreadable():
        raise OSError("no BootId")
    monkeypatch.setattr(osdirs, "_windows_boot_counter", unreadable)
    assert re.fullmatch(r"win-\d+", osdirs.boot_id())


def test_windows_boot_minute_is_rounded():
    # Booted at epoch minute 1000; asked 90.4 s and 5000.2 s later: the same id both times.
    assert osdirs._boot_minute(60_000 + 90.4, 90_400) == "win-1000"
    assert osdirs._boot_minute(60_000 + 5_000.2, 5_000_200) == "win-1000"
    assert osdirs._boot_minute(60_000 + 120.0, 60_000) == "win-1001"


def test_boot_id_never_raises(monkeypatch, fresh_boot_id):
    def boom():
        raise OSError("no boot id here")
    monkeypatch.setattr(osdirs, "_linux_boot_id", boom)
    monkeypatch.setattr(osdirs, "_windows_boot_id", boom)
    assert osdirs.boot_id() == f"unknown-{os.getpid()}"
