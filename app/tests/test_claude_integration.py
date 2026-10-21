import json
import subprocess
import sys
from pathlib import Path

import pytest

from brainhub import claude_integration as ci
from brainhub.claude_integration import EVENTS, IntegrationError

APP_DIR = Path(__file__).resolve().parent.parent
PY, SCRIPT = "D:/py/python.exe", "D:/BrainHub/app/brainhub/hook_client.py"


def do_install(path, data, port=8090):
    return ci.install(path, python=PY, script=SCRIPT, port=port, data_dir=data)


def ours(settings, event):
    return [g for g in settings["hooks"][event]
            if any(ci.MARK in " ".join(h.get("args", [])) for h in g["hooks"])]


def test_install_into_missing_file(tmp_path):
    f = tmp_path / "nope" / "settings.json"
    do_install(f, tmp_path / "data")
    s = json.loads(f.read_text("utf-8"))
    for ev in EVENTS:
        groups = ours(s, ev)
        assert len(groups) == 1
        hook = groups[0]["hooks"][0]
        if ev in ci.ASYNC_EVENTS:
            assert hook["async"] is True
        else:
            assert "async" not in hook
    assert set(ci.ASYNC_EVENTS) == {"UserPromptSubmit", "Notification", "PostToolUse"}
    assert "hook_client.py statusline --port 8090" in s["statusLine"]["command"]
    st = ci.status(f, port=8090)
    assert st["hooks_installed"] and st["port_matches"] and st["statusline_installed"]


def test_install_is_idempotent_and_updates_port(tmp_path):
    f = tmp_path / "settings.json"
    do_install(f, tmp_path, 8090)
    do_install(f, tmp_path, 8090)
    do_install(f, tmp_path, 8091)
    s = json.loads(f.read_text("utf-8"))
    for ev in EVENTS:
        assert len(s["hooks"][ev]) == 1
        assert s["hooks"][ev][0]["hooks"][0]["args"][-1] == "8091"
    assert ci.status(f, port=8090)["port_matches"] is False
    assert ci.status(f, port=8091)["port_matches"] is True


def test_install_preserves_foreign_hooks_and_keys(tmp_path):
    f = tmp_path / "settings.json"
    orig = {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]},
            "permissions": {"allow": ["x"]},
            "PreToolUse": [], "note": "café"}
    orig["hooks"]["PreToolUse"] = []
    f.write_text(json.dumps(orig), "utf-8")
    do_install(f, tmp_path)
    s = json.loads(f.read_text("utf-8"))
    assert s["model"] == "opus" and s["permissions"] == {"allow": ["x"]}
    assert len(s["hooks"]["Stop"]) == 2 and s["hooks"]["Stop"][0] == orig["hooks"]["Stop"][0]
    assert "café" in f.read_text("utf-8")
    ci.uninstall(f, data_dir=tmp_path)
    assert json.loads(f.read_text("utf-8")) == orig


def test_mixed_group_keeps_foreign_hook(tmp_path):
    f = tmp_path / "settings.json"
    foreign = {"type": "command", "command": "say done"}
    orig = {"hooks": {"Stop": [{"matcher": "m", "hooks": [foreign, ci.hook_entry(PY, SCRIPT, 8090, is_async=False)]}]}}
    f.write_text(json.dumps(orig), "utf-8")
    do_install(f, tmp_path)
    s = json.loads(f.read_text("utf-8"))
    assert s["hooks"]["Stop"][0] == {"matcher": "m", "hooks": [foreign]} and len(s["hooks"]["Stop"]) == 2
    ci.uninstall(f, data_dir=tmp_path)
    assert json.loads(f.read_text("utf-8"))["hooks"]["Stop"] == [{"matcher": "m", "hooks": [foreign]}]


def test_foreign_hook_client_is_not_ours(tmp_path):
    f = tmp_path / "settings.json"
    hook = {"type": "command", "command": "python", "args": ["C:/tools/other/hook_client.py"]}
    prev = {"type": "command", "command": "python C:/x/hook_client.py"}
    orig = {"hooks": {"Stop": [{"hooks": [hook]}]}, "statusLine": prev}
    f.write_text(json.dumps(orig), "utf-8")
    assert ci.status(f, port=8090)["hooks_installed"] is False
    do_install(f, tmp_path / "d")
    assert len(json.loads(f.read_text("utf-8"))["hooks"]["Stop"]) == 2
    assert ci.status(f, port=8090)["previous_statusline"] is True
    ci.uninstall(f, data_dir=tmp_path / "d")
    assert json.loads(f.read_text("utf-8")) == orig


@pytest.mark.parametrize("bad", [
    {"hooks": {"Stop": "x"}}, {"hooks": {"Stop": ["x"]}}, {"hooks": {"Stop": [{"hooks": "x"}]}},
    {"hooks": {"Stop": [{"hooks": ["x"]}]}}, {"hooks": {"Stop": [{"hooks": [{"args": "x"}]}]}}])
def test_odd_shapes(tmp_path, bad):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(bad), "utf-8")
    ci.status(f, port=8090)  # must not crash
    for fn in (lambda: do_install(f, tmp_path), lambda: ci.uninstall(f, data_dir=tmp_path)):
        with pytest.raises(IntegrationError):
            fn()
    assert json.loads(f.read_text("utf-8")) == bad
    assert not list(tmp_path.glob("*backup*"))


def test_backups_never_overwrite(tmp_path):
    from datetime import datetime
    f = tmp_path / "settings.json"
    f.write_text('{"a": 1}')
    fixed = lambda: datetime(2026, 1, 1, 1, 1, 1, 555)  # noqa: E731
    for _ in range(3):
        ci.install(f, python=PY, script=SCRIPT, port=1, data_dir=tmp_path, now=fixed)
    assert len(list(tmp_path.glob("settings.json.brainhub-backup-*"))) == 3
    assert "." not in json.loads((tmp_path / "claude_integration.json").read_text())["installed_at"]


def test_install_backs_up_original(tmp_path):
    f = tmp_path / "settings.json"
    f.write_bytes(b'{"model": "opus"}\n')
    do_install(f, tmp_path)
    backups = list(tmp_path.glob("settings.json.brainhub-backup-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == b'{"model": "opus"}\n'


def test_install_refuses_invalid_json(tmp_path):
    f = tmp_path / "settings.json"
    f.write_bytes(b"{ // comment\n}")
    with pytest.raises(IntegrationError):
        do_install(f, tmp_path)
    assert f.read_bytes() == b"{ // comment\n}"
    assert not list(tmp_path.glob("settings.json.brainhub-backup-*"))


def test_statusline_passthrough_roundtrip(tmp_path):
    f = tmp_path / "settings.json"
    prev = {"type": "command", "command": "bash ~/x.sh", "padding": 1}
    f.write_text(json.dumps({"statusLine": prev}), "utf-8")
    data = tmp_path / "data"
    do_install(f, data)
    s = json.loads(f.read_text("utf-8"))
    assert "--passthrough-file" in s["statusLine"]["command"]
    assert (data / "statusline_passthrough.txt").read_text("utf-8").strip() == "bash ~/x.sh"
    assert ci.status(f, port=8090)["previous_statusline"] is True
    do_install(f, data)  # re-install must not record our own line as the previous one
    ci.uninstall(f, data_dir=data)
    assert json.loads(f.read_text("utf-8"))["statusLine"] == prev
    assert not (data / "claude_integration.json").exists()


def test_uninstall_when_not_installed_is_noop(tmp_path):
    f = tmp_path / "settings.json"
    f.write_bytes(b'{"model":"opus"}')
    ci.uninstall(f, data_dir=tmp_path)
    assert f.read_bytes() == b'{"model":"opus"}'
    assert not list(tmp_path.glob("*backup*"))


def test_statusline_command_quoting():
    c = ci.statusline_command("C:/Program Files/py/python.exe", "D:/BrainHub/hook_client.py", 8090, None)
    assert c.startswith('"C:/Program Files/py/python.exe" -I -S D:/BrainHub/hook_client.py statusline')
    c = ci.statusline_command("D:/py/python.exe", "D:/B/hook_client.py", 1, Path("D:/my data/p.txt"))
    assert '--passthrough-file "D:/my data/p.txt"' in c and '"D:/py' not in c


def test_default_path_honours_env(tmp_path):
    assert tmp_path in ci.default_settings_path().parents


def test_console_python(tmp_path):
    (tmp_path / "pythonw.exe").write_text("")
    assert ci.console_python(tmp_path / "pythonw.exe") == tmp_path / "pythonw.exe"  # no sibling yet
    (tmp_path / "python.exe").write_text("")
    assert ci.console_python(tmp_path / "PythonW.exe") == tmp_path / "python.exe"
    assert ci.console_python(tmp_path / "python.exe") == tmp_path / "python.exe"


def test_cli_status(tmp_path):
    f = tmp_path / "s.json"
    r = subprocess.run([sys.executable, "-m", "brainhub", "integration", "status", "--root", str(tmp_path),
                        "--settings", str(f)], cwd=APP_DIR, capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["hooks_installed"] is False


def test_cli_error_exit(tmp_path):
    f = tmp_path / "s.json"
    f.write_text("{ nope")
    r = subprocess.run([sys.executable, "-m", "brainhub", "integration", "install", "--root", str(tmp_path),
                        "--settings", str(f)], cwd=APP_DIR, capture_output=True, text=True)
    assert r.returncode == 1 and r.stderr.startswith("error:")
