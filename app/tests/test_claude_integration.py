import json
import os
import shlex
import shutil
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


def test_statusline_command_quoting(monkeypatch):
    monkeypatch.setattr(osdirs, "os_name", lambda: "windows")  # the phase 2 form, kept on Windows
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


def test_posix_statusline_quoting(tmp_path, monkeypatch):
    monkeypatch.setattr(osdirs, "os_name", lambda: "linux")
    py, script = "/opt/my py/bin/python3.14", "/home/o'neil/.local/share/brainhub/hook/hook_client.py"
    passthrough = Path("/home/o'neil/my state/statusline_passthrough.txt")
    c = ci.statusline_command(py, script, 8090, passthrough)
    assert shlex.split(c) == [py, "-I", "-S", script, "statusline", "--port", "8090",
                              "--passthrough-file", str(passthrough)]
    # A backslash is part of a POSIX file name: never turned into a slash.
    assert shlex.split(ci.statusline_command("/a\\b/python", SCRIPT, 1, None))[0] == "/a\\b/python"
    # Installed in that form, status and uninstall still know the line as ours.
    f, state = tmp_path / "settings.json", tmp_path / "st"
    f.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo mine"}}), "utf-8")
    ci.install(f, python=py, script=script, port=8090, state_dir=state)
    st = ci.status(f, port=8090, state_dir=state)
    assert st["statusline_installed"] and st["previous_statusline"] and st["port_matches"]
    ci.uninstall(f, state_dir=state)
    assert json.loads(f.read_text("utf-8")) == {"statusLine": {"type": "command", "command": "echo mine"}}


@pytest.mark.skipif(sys.platform == "win32", reason="Claude Code runs the status line through sh on POSIX")
def test_posix_statusline_runs_under_sh(tmp_path, monkeypatch):
    """The quoted line really runs under sh, with a space and a ' in every path."""
    from tests.test_hook_client import free_port
    monkeypatch.setattr(osdirs, "os_name", lambda: "linux")
    odd = tmp_path / "it's a dir"
    script = odd / "brainhub" / "hook" / "hook_client.py"
    script.parent.mkdir(parents=True)
    shutil.copy(ci.hook_script(), script)
    python = odd / "python"
    python.symlink_to(sys.executable)
    passthrough = tmp_path / "my state's" / ci.PASSTHROUGH_FILE
    passthrough.parent.mkdir()
    passthrough.write_text("echo owner-line", "utf-8")
    cmd = ci.statusline_command(str(python), str(script), free_port(), passthrough)
    p = subprocess.run(["sh", "-c", cmd], input=b"{}", capture_output=True, timeout=10)
    assert (p.returncode, p.stdout, p.stderr) == (0, b"owner-line\n", b"")


def _runtime_state(state: Path, slot: str = "a") -> Path:
    """A state folder in runtime mode, as update_step.py leaves it: current.json plus the stable
    hook copy (P4). Returns the copy's path."""
    (state / "runtime").mkdir(parents=True, exist_ok=True)
    (state / "runtime" / "current.json").write_text(json.dumps({"slot": slot, "commit": "c" * 40, "deps": "d"}))
    stable = ci.stable_hook(state)
    stable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ci.hook_script(), stable)
    return stable


def test_runtime_mode_hooks_use_stable_paths(tmp_path, monkeypatch):
    state = tmp_path / "state"  # BRAINHUB_STATE_DIR from conftest: the default
    base = tmp_path / "uv" / "cpython-3.14-linux-x86_64-gnu" / "bin" / "python3.14"
    base.parent.mkdir(parents=True)
    base.write_text("")
    monkeypatch.setattr(sys, "_base_executable", str(base), raising=False)
    dev = (str(ci.console_python(Path(sys.executable))), str(ci.hook_script()))
    assert ci.hook_paths() == dev  # no runtime slot: dev mode, as before
    (state / "runtime").mkdir(parents=True)
    (state / "runtime" / "current.json").write_text("{}")
    assert ci.hook_paths() == dev  # no stable copy yet: never a path that doesn't exist
    stable = _runtime_state(state)
    assert ci.hook_paths() == ci.hook_paths(state) == (str(base), str(stable))

    f = tmp_path / "settings.json"
    python, script = ci.hook_paths()
    ci.install(f, python=python, script=script, port=8090, state_dir=state)
    s = json.loads(f.read_text("utf-8"))
    for ev in EVENTS:
        (hook,) = [h for g in s["hooks"][ev] for h in g["hooks"]]
        assert hook["command"] == str(base) and hook["args"][:3] == ["-I", "-S", str(stable)]
    if sys.platform != "win32":
        assert shlex.split(s["statusLine"]["command"])[:4] == [str(base), "-I", "-S", str(stable)]
    st = ci.status(f, port=8090)
    assert st["hooks_installed"] and st["events"] == list(EVENTS) and st["statusline_installed"]
    assert st["port_matches"]

    # A slot flip moves neither path.
    _runtime_state(state, slot="b")
    assert ci.hook_paths() == (str(base), str(stable))
    # A pythonw base (Windows' windowed launcher) gives its console sibling; a missing base falls
    # back to this python rather than a path that can't run.
    pythonw = base.with_name("pythonw")
    pythonw.write_text("")
    base.with_name("python").write_text("")
    monkeypatch.setattr(sys, "_base_executable", str(pythonw))
    assert ci.hook_paths()[0] == str(base.with_name("python"))
    monkeypatch.setattr(sys, "_base_executable", str(tmp_path / "gone" / "python3"))
    assert ci.hook_paths()[0] == str(ci.console_python(Path(sys.executable)))


def test_runtime_hook_runs_with_the_base_interpreter(tmp_path):
    """The hook as installed in runtime mode (the real base interpreter, outside any venv, and the
    stable copy) delivers an event."""
    from tests.test_hook_client import Hub
    stable = _runtime_state(tmp_path / "state")
    python, script = ci.hook_paths()
    assert script == str(stable)
    h = Hub()
    try:
        hook = ci.hook_entry(python, script, h.port, is_async=False)
        p = subprocess.run([hook["command"], *hook["args"]], input=b'{"hook_event_name": "Stop"}',
                           capture_output=True, timeout=10, env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "cli"})
    finally:
        h.close()
    assert (p.returncode, p.stdout, p.stderr) == (0, b"", b"")
    assert h.bodies == [("/api/hooks/event", {"hook_event_name": "Stop", "entrypoint": "cli"})]


def test_uninstall_on_one_os_keeps_other_statusline(tmp_path, monkeypatch):
    # One shared root; each OS has its own settings file and state folder, both in runtime mode,
    # and each kept the owner's own status line as its passthrough.
    data = tmp_path / "root" / "data"
    data.mkdir(parents=True)
    oses = {}
    for name in ("windows", "linux"):
        state = tmp_path / name / "state"
        stable = _runtime_state(state)
        f = tmp_path / name / "settings.json"
        f.write_text(json.dumps({"model": "opus", "statusLine": {"type": "command", "command": f"{name}-line"}}),
                     "utf-8")
        monkeypatch.setattr(osdirs, "os_name", lambda name=name: name)
        python, script = ci.hook_paths(state)
        assert script == str(stable)
        ci.install(f, python=python, script=script, port=8090, state_dir=state, data_dir=data)
        oses[name] = (f, state)

    def snapshot(name):
        f, state = oses[name]
        return {p: p.read_bytes() for p in [*f.parent.glob("settings.json*"), *state.rglob("*")] if p.is_file()}

    for gone, kept in (("linux", "windows"), ("windows", None)):
        before = snapshot(kept) if kept else None
        monkeypatch.setattr(osdirs, "os_name", lambda gone=gone: gone)
        f, state = oses[gone]
        ci.uninstall(f, state_dir=state, data_dir=data)
        assert json.loads(f.read_text("utf-8")) == {
            "model": "opus", "statusLine": {"type": "command", "command": f"{gone}-line"}}
        assert not (state / ci.STATE_FILE).exists() and not (state / ci.PASSTHROUGH_FILE).exists()
        assert ci.stable_hook(state).exists()  # the update step's copy, not the integration's
        if kept:  # the other OS: its settings, status line and state are untouched
            assert snapshot(kept) == before
            kept_f, kept_state = oses[kept]
            st = ci.status(kept_f, port=8090, state_dir=kept_state)
            assert st["hooks_installed"] and st["statusline_installed"] and st["previous_statusline"]
            assert (kept_state / ci.PASSTHROUGH_FILE).read_text("utf-8") == f"{kept}-line"
        assert list(data.iterdir()) == []  # nothing per-OS in shared data


@pytest.mark.parametrize("stable", [
    "C:\\Users\\me\\AppData\\Local\\BrainHub\\hook\\hook_client.py",  # Windows' default state folder
    "/home/me/.local/share/brainhub/hook/hook_client.py",  # Linux's
    "/home/me/my data/brainhub/hook/hook_client.py",  # an XDG_DATA_HOME with a space
])
@pytest.mark.parametrize("os_name", ["windows", "linux"])
def test_status_recognises_runtime_hook_path(tmp_path, monkeypatch, stable, os_name):
    monkeypatch.setattr(osdirs, "os_name", lambda: os_name)
    f = tmp_path / "settings.json"
    foreign = {"hooks": [{"type": "command", "command": "say done"}]}
    hooks = {ev: [foreign, {"hooks": [ci.hook_entry("/usr/bin/python3", stable, 8090, is_async=False)]}]
             for ev in EVENTS}
    line = {"type": "command", "command": ci.statusline_command("/usr/bin/python3", stable, 8090, None)}
    f.write_text(json.dumps({"hooks": hooks, "statusLine": line}), "utf-8")
    st = ci.status(f, port=8090)
    assert st["hooks_installed"] and st["events"] == list(EVENTS)
    assert st["statusline_installed"] and st["port_matches"]
    ci.uninstall(f, state_dir=tmp_path / "state")
    assert json.loads(f.read_text("utf-8")) == {"hooks": {ev: [foreign] for ev in EVENTS}}


@pytest.mark.parametrize("os_name", ["windows", "linux"])
def test_status_recognises_the_state_folder_in_use(tmp_path, monkeypatch, os_name):
    """Under BRAINHUB_STATE_DIR the state folder may have any name: its own stable copy is ours."""
    monkeypatch.setattr(osdirs, "os_name", lambda: os_name)
    state = tmp_path / "bh state's"
    monkeypatch.setenv("BRAINHUB_STATE_DIR", str(state))
    stable = _runtime_state(state)
    python, script = ci.hook_paths()
    assert script == str(stable)
    f = tmp_path / "settings.json"
    ci.install(f, python=python, script=script, port=8090, state_dir=osdirs.state_dir())
    st = ci.status(f, port=8090)
    assert st["hooks_installed"] and st["statusline_installed"] and st["port_matches"]
    assert ci.status(f, port=8090, state_dir=tmp_path / "other")["hooks_installed"] is False
    ci.uninstall(f, state_dir=state)
    assert json.loads(f.read_text("utf-8")) == {}


@pytest.mark.parametrize("script", [
    "/opt/tools/hook/hook_client.py",  # a hook/ folder, but not in a BrainHub state folder
    "/x/notbrainhub/hook/hook_client.py",
    "/x/notbrainhub/hook_client.py",
    "/x/brainhub-old/hook/hook_client.py",
    "/x/brainhub/hook/hook_client.py.bak",
    "/tmp/other-state/hook/hook_client.py",  # a state folder other than the one in use
    "C:/tools/BrainHub/hook/other/hook_client.py",
])
@pytest.mark.parametrize("os_name", ["windows", "linux"])
def test_unrelated_hook_clients_are_not_ours(tmp_path, monkeypatch, script, os_name):
    monkeypatch.setattr(osdirs, "os_name", lambda: os_name)
    f = tmp_path / "settings.json"
    orig = {"hooks": {"Stop": [{"hooks": [ci.hook_entry("python", script, 8090, is_async=False)]}]},
            "statusLine": {"type": "command", "command": ci.statusline_command("python", script, 8090, None)}}
    f.write_text(json.dumps(orig), "utf-8")
    st = ci.status(f, port=8090)
    assert st["hooks_installed"] is False and st["statusline_installed"] is False
    ci.uninstall(f, state_dir=tmp_path / "state")
    assert json.loads(f.read_text("utf-8")) == orig


def test_cli_install_in_runtime_mode(tmp_path):
    stable = _runtime_state(tmp_path / "state")  # BRAINHUB_STATE_DIR from conftest, inherited
    f = tmp_path / "s.json"
    r = subprocess.run([sys.executable, "-m", "brainhub", "integration", "install", "--root", str(tmp_path),
                        "--settings", str(f)], cwd=APP_DIR, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    hook = json.loads(f.read_text("utf-8"))["hooks"]["Stop"][0]["hooks"][0]
    assert hook["command"] == str(ci.base_python()) and hook["args"][2] == str(stable)
    assert json.loads(r.stdout)["hooks_installed"] is True
