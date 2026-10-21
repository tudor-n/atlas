import json
import os
import subprocess
import sys
from pathlib import Path

from brainhub.config import Paths
from brainhub.claude_integration import console_python
from brainhub.desktop_config import default_target, write_desktop_config

APP_DIR = Path(__file__).resolve().parent.parent


def test_write_desktop_config(tmp_path):
    target = tmp_path / "BrainHub" / "desktop.json"
    root, py, app = tmp_path / "root", tmp_path / "py" / "pythonw.exe", tmp_path / "app"
    out = write_desktop_config(root=root, port=8099, python=py, app_dir=app, target=target)
    assert out == target
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == {"port": 8099, "root": str(root), "python": str(py), "app_dir": str(app)}


def test_default_target_uses_localappdata(tmp_path):
    # conftest points LOCALAPPDATA at a temp dir
    assert default_target() == Path(os.environ["LOCALAPPDATA"]) / "BrainHub" / "desktop.json"
    assert str(tmp_path) in str(default_target())


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
