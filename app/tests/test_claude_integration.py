import json
import subprocess
import sys
from pathlib import Path

import pytest

from brainhub import claude_integration as ci
from brainhub import osdirs
from brainhub.claude_integration import EVENTS, IntegrationError

APP_DIR = Path(__file__).resolve().parent.parent
PY, SCRIPT = "D:/py/python.exe", "D:/BrainHub/app/brainhub/hook_client.py"


def do_install(path, state, port=8090):
    return ci.install(path, python=PY, script=SCRIPT, port=port, state_dir=state)


def ours(settings, event):
    return [g for g in settings["hooks"][event]
            if any(ci.MARK in " ".join(h.get("args", [])) for h in g["hooks"])]


def test_install_into_missing_file(tmp_path):
    f = tmp_path / "nope" / "settings.json"
    do_install(f, tmp_path / "state")
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
    ci.uninstall(f, state_dir=tmp_path)
    assert json.loads(f.read_text("utf-8")) == orig


def test_mixed_group_keeps_foreign_hook(tmp_path):
    f = tmp_path / "settings.json"
    foreign = {"type": "command", "command": "say done"}
    orig = {"hooks": {"Stop": [{"matcher": "m", "hooks": [foreign, ci.hook_entry(PY, SCRIPT, 8090, is_async=False)]}]}}
    f.write_text(json.dumps(orig), "utf-8")
    do_install(f, tmp_path)
    s = json.loads(f.read_text("utf-8"))
    assert s["hooks"]["Stop"][0] == {"matcher": "m", "hooks": [foreign]} and len(s["hooks"]["Stop"]) == 2
    ci.uninstall(f, state_dir=tmp_path)
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
    ci.uninstall(f, state_dir=tmp_path / "d")
    assert json.loads(f.read_text("utf-8")) == orig


@pytest.mark.parametrize("bad", [
    {"hooks": {"Stop": "x"}}, {"hooks": {"Stop": ["x"]}}, {"hooks": {"Stop": [{"hooks": "x"}]}},
    {"hooks": {"Stop": [{"hooks": ["x"]}]}}, {"hooks": {"Stop": [{"hooks": [{"args": "x"}]}]}}])
def test_odd_shapes(tmp_path, bad):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(bad), "utf-8")
    ci.status(f, port=8090)  # must not crash
    for fn in (lambda: do_install(f, tmp_path), lambda: ci.uninstall(f, state_dir=tmp_path)):
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
        ci.install(f, python=PY, script=SCRIPT, port=1, state_dir=tmp_path, now=fixed)
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
    state = tmp_path / "state"
    do_install(f, state)
    s = json.loads(f.read_text("utf-8"))
    assert "--passthrough-file" in s["statusLine"]["command"]
    assert (state / "statusline_passthrough.txt").read_text("utf-8").strip() == "bash ~/x.sh"
    assert ci.status(f, port=8090)["previous_statusline"] is True
    do_install(f, state)  # re-install must not record our own line as the previous one
    ci.uninstall(f, state_dir=state)
    assert json.loads(f.read_text("utf-8"))["statusLine"] == prev
    assert not (state / "claude_integration.json").exists()


def test_integration_state_is_per_os(tmp_path, monkeypatch):
    # One shared root, two OSes: each has its own Claude settings file and its own state folder.
    data = tmp_path / "root" / "data"
    data.mkdir(parents=True)
    oses = {}
    for name in ("windows", "linux"):
        f = tmp_path / name / "settings.json"
        f.parent.mkdir()
        f.write_text(json.dumps({"statusLine": {"type": "command", "command": f"{name}-line"}}), "utf-8")
        oses[name] = (f, tmp_path / name / "state")
    for name, (f, state) in oses.items():
        monkeypatch.setattr(osdirs, "os_name", lambda name=name: name)
        ci.install(f, python=PY, script=SCRIPT, port=8090, state_dir=state, data_dir=data)
        assert (state / ci.PASSTHROUGH_FILE).read_text("utf-8") == f"{name}-line"
    win_f, win_state = oses["windows"]
    monkeypatch.setattr(osdirs, "os_name", lambda: "windows")
    ci.uninstall(win_f, state_dir=win_state, data_dir=data)
    assert json.loads(win_f.read_text("utf-8"))["statusLine"] == {"type": "command", "command": "windows-line"}
    assert not (win_state / ci.STATE_FILE).exists() and not (win_state / ci.PASSTHROUGH_FILE).exists()
    lin_f, lin_state = oses["linux"]  # untouched by the Windows uninstall
    assert (lin_state / ci.PASSTHROUGH_FILE).read_text("utf-8") == "linux-line"
    state = json.loads((lin_state / ci.STATE_FILE).read_text("utf-8"))
    assert state["previous_statusline"] == {"type": "command", "command": "linux-line"}
    assert list(data.iterdir()) == []  # nothing per-OS in shared data
    monkeypatch.setattr(osdirs, "os_name", lambda: "linux")
    ci.uninstall(lin_f, state_dir=lin_state, data_dir=data)
    assert json.loads(lin_f.read_text("utf-8"))["statusLine"] == {"type": "command", "command": "linux-line"}


def _legacy_files(data: Path, prev: dict) -> dict:
    """What an older hub left in the shared data/ folder (it was always the Windows install's)."""
    data.mkdir(parents=True, exist_ok=True)
    files = {ci.STATE_FILE: json.dumps({"previous_statusline": prev, "installed_at": "2026-10-01T10:00:00"}),
             ci.PASSTHROUGH_FILE: prev["command"]}
    for name, text in files.items():
        (data / name).write_text(text, "utf-8")
    return files


@pytest.mark.parametrize("os_name", ["windows", "linux"])
def test_legacy_state_adopted_on_windows_only(tmp_path, monkeypatch, os_name):
    monkeypatch.setattr(osdirs, "os_name", lambda: os_name)
    data, state = tmp_path / "data", tmp_path / "state"
    prev = {"type": "command", "command": "powershell -File C:/x.ps1"}
    legacy = _legacy_files(data, prev)
    f = tmp_path / "settings.json"
    ours_line = ci.statusline_command(PY, SCRIPT, 8090, data / ci.PASSTHROUGH_FILE)
    f.write_text(json.dumps({"statusLine": {"type": "command", "command": ours_line}}), "utf-8")
    ci.uninstall(f, state_dir=state, data_dir=data)
    after = json.loads(f.read_text("utf-8"))
    if os_name == "windows":
        assert after["statusLine"] == prev  # adopted, then used to restore the owner's own line
        assert list(data.iterdir()) == []
    else:
        assert "statusLine" not in after  # never read on Linux
        assert {p.name: p.read_text("utf-8") for p in data.iterdir()} == legacy  # and never touched
    assert not state.exists() or list(state.iterdir()) == []


def test_adopt_legacy_moves_files_and_keeps_existing_state(tmp_path):
    data, state = tmp_path / "data", tmp_path / "state"
    legacy = _legacy_files(data, {"type": "command", "command": "old"})
    assert ci.adopt_legacy(data, state, "linux") == []
    assert ci.adopt_legacy(data, state, "windows") == [ci.STATE_FILE, ci.PASSTHROUGH_FILE]
    assert {p.name: p.read_text("utf-8") for p in state.iterdir()} == legacy
    assert list(data.iterdir()) == []
    # The state folder already has its own files: the legacy ones are left where they are.
    _legacy_files(data, {"type": "command", "command": "older"})
    (state / ci.STATE_FILE).unlink()
    assert ci.adopt_legacy(data, state, "windows") == []
    assert (data / ci.STATE_FILE).exists() and (state / ci.PASSTHROUGH_FILE).read_text("utf-8") == "old"


def test_legacy_install_on_windows_restores_owner_line_later(tmp_path, monkeypatch):
    monkeypatch.setattr(osdirs, "os_name", lambda: "windows")
    data, state = tmp_path / "data", tmp_path / "state"
    prev = {"type": "command", "command": "powershell -File C:/x.ps1"}
    _legacy_files(data, prev)
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"statusLine": {"type": "command",
                                            "command": ci.statusline_command(PY, SCRIPT, 8090, None)}}), "utf-8")
    ci.install(f, python=PY, script=SCRIPT, port=8090, state_dir=state, data_dir=data)
    line = json.loads(f.read_text("utf-8"))["statusLine"]["command"]
    assert "--passthrough-file" in line and (state / ci.PASSTHROUGH_FILE).as_posix() in line
    assert json.loads((state / ci.STATE_FILE).read_text("utf-8"))["installed_at"] == "2026-10-01T10:00:00"
    assert list(data.iterdir()) == []
    ci.uninstall(f, state_dir=state, data_dir=data)
    assert json.loads(f.read_text("utf-8"))["statusLine"] == prev


def test_uninstall_when_not_installed_is_noop(tmp_path):
    f = tmp_path / "settings.json"
    f.write_bytes(b'{"model":"opus"}')
    ci.uninstall(f, state_dir=tmp_path)
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


def test_cli_install_uses_the_state_folder(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo mine"}}), "utf-8")
    r = subprocess.run([sys.executable, "-m", "brainhub", "integration", "install", "--root", str(tmp_path),
                        "--settings", str(f)], cwd=APP_DIR, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    state = tmp_path / "state"  # BRAINHUB_STATE_DIR from conftest, inherited by the child
    assert (state / ci.STATE_FILE).exists() and (state / ci.PASSTHROUGH_FILE).read_text("utf-8") == "echo mine"
    assert not any((tmp_path / "data" / name).exists() for name in (ci.STATE_FILE, ci.PASSTHROUGH_FILE))
