import json
import os
import subprocess
import sys
from pathlib import Path

from brainhub.config import Paths
from brainhub.desktop_config import default_target, pythonw_for, write_desktop_config

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


def test_pythonw_for(tmp_path):
    both = tmp_path / "both"
    both.mkdir()
    (both / "python.exe").write_bytes(b"")
    (both / "pythonw.exe").write_bytes(b"")
    assert pythonw_for(both / "python.exe") == both / "pythonw.exe"
    only = tmp_path / "only"
    only.mkdir()
    (only / "python.exe").write_bytes(b"")
    assert pythonw_for(only / "python.exe") == only / "python.exe"


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
    assert data["python"] == str(pythonw_for(Path(sys.executable)))
