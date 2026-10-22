import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from brainhub.config import Paths
from brainhub.claude_integration import console_python
from brainhub.desktop_config import default_target, write_desktop_config, write_runtime_desktop_config

APP_DIR = Path(__file__).resolve().parent.parent


def test_write_desktop_config(tmp_path):
    target = tmp_path / "BrainHub" / "desktop.json"
    root, py, app = tmp_path / "root", tmp_path / "py" / "pythonw.exe", tmp_path / "app"
    out = write_desktop_config(root=root, port=8099, python=py, app_dir=app, target=target)
    assert out == target
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"port": 8099, "root": str(root), "python": str(py), "app_dir": str(app)}


def test_default_target_honours_env(tmp_path):
    # conftest points BRAINHUB_CONFIG_DIR at a temp dir (the Rust shell reads the same variable)
    assert default_target() == tmp_path / "config" / "desktop.json"


@pytest.mark.skipif(sys.platform == "win32", reason="XDG rules")
def test_default_target_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAINHUB_CONFIG_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert default_target() == tmp_path / "xdg" / "brainhub" / "desktop.json"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert default_target() == tmp_path / "home" / ".config" / "brainhub" / "desktop.json"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows folder rules")
def test_default_target_windows(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAINHUB_CONFIG_DIR")
    assert default_target() == Path(os.environ["LOCALAPPDATA"]) / "BrainHub" / "desktop.json"
    assert str(tmp_path) in str(default_target())


def test_console_python_posix_layout(tmp_path):
    # A POSIX venv has bin/python, a symlink to the base interpreter: it is returned as it is,
    # never resolved (the base interpreter would run without the venv's packages).
    base = tmp_path / "base" / "bin" / "python3.14"
    base.parent.mkdir(parents=True)
    base.write_text("")
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    try:
        (venv_bin / "python").symlink_to(base)
    except OSError:  # Windows without symlink rights: a plain file shows the same rule
        (venv_bin / "python").write_text("")
    assert console_python(venv_bin / "python") == venv_bin / "python"
    # No pythonw on Linux; where one exists (a framework build), the console python beside it.
    assert console_python(venv_bin / "pythonw") == venv_bin / "python"
    assert console_python(base.with_name("pythonw")) == base.with_name("pythonw")  # no sibling: as is


def test_cli_desktop_config(tmp_path):
    target = tmp_path / "x.json"
    root = tmp_path / "root"
    r = subprocess.run([sys.executable, "-m", "brainhub", "desktop-config", "--root", str(root),
                        "--target", str(target)], cwd=APP_DIR, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert target.exists()
    assert r.stdout.strip() == str(target)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["port"] == 8090
    assert data["root"] == str(root.resolve())
    assert data["app_dir"] == str(Paths(root).app)
    # The console python.exe, never pythonw (Ruling 13): the supervisor's CREATE_NO_WINDOW gives it a
    # hidden console that git/claude/taskkill inherit, so they don't each pop a visible one.
    assert data["python"] == str(console_python(Path(sys.executable)))
    assert Path(data["python"]).name.lower() != "pythonw.exe"


def test_cli_desktop_config_from_pythonw_writes_python(tmp_path):
    pyw = Path(sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        return  # no pythonw beside this interpreter (not Windows)
    target = tmp_path / "x.json"
    r = subprocess.run([str(pyw), "-m", "brainhub", "desktop-config", "--root", str(tmp_path / "root"),
                        "--target", str(target)], cwd=APP_DIR, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = json.loads(target.read_text(encoding="utf-8"))
    assert Path(data["python"]).name.lower() == "python.exe"


def test_write_runtime_desktop_config(tmp_path):
    target = tmp_path / "BrainHub" / "desktop.json"
    root, live, state = tmp_path / "root", tmp_path / "live", tmp_path / "state"
    out = write_runtime_desktop_config(root=root, port=8099, live=live, state=state, target=target)
    assert out == target
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"port": 8099, "root": str(root), "live": str(live), "state": str(state)}


def test_cli_desktop_config_runtime(tmp_path):
    target = tmp_path / "x.json"
    root, live = tmp_path / "root", tmp_path / "live"
    r = subprocess.run([sys.executable, "-m", "brainhub", "desktop-config", "--root", str(root),
                        "--target", str(target), "--runtime", "--live", str(live)],
                       cwd=APP_DIR, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(target)
    data = json.loads(target.read_text(encoding="utf-8"))
    # conftest points BRAINHUB_STATE_DIR at a temp folder
    assert data == {"port": 8090, "root": str(root.resolve()), "live": str(live.resolve()),
                    "state": str(Path(os.environ["BRAINHUB_STATE_DIR"]).resolve())}
    assert "runtime init" in r.stderr  # no slot yet


def test_cli_desktop_config_runtime_with_slot_is_quiet(tmp_path):
    runtime = Path(os.environ["BRAINHUB_STATE_DIR"]) / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "current.json").write_text("{}", encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "brainhub", "desktop-config", "--root", str(tmp_path / "root"),
                        "--target", str(tmp_path / "x.json"), "--runtime", "--live", str(tmp_path / "live")],
                       cwd=APP_DIR, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


def test_cli_desktop_config_runtime_and_live_go_together(tmp_path):
    for extra in (["--runtime"], ["--live", str(tmp_path / "live")]):
        target = tmp_path / "x.json"
        r = subprocess.run([sys.executable, "-m", "brainhub", "desktop-config", "--root", str(tmp_path / "root"),
                            "--target", str(target), *extra], cwd=APP_DIR, capture_output=True, text=True)
        assert r.returncode == 2
        assert not target.exists()
