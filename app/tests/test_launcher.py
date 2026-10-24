"""The launcher (atlas/launcher.py): update, rebuild the shell when the live HEAD moved, then start
it. Temp folders only: a temp live copy (real git), temp state and config folders, and fake
cargo, notify-send, slot python and shell binaries. The launcher runs as a real subprocess (it is
a file run with -I -S); its pure helpers are also called directly."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from atlas import launcher as la
from tests.fakes import fake_cli

APP_DIR = Path(__file__).resolve().parents[1]
SCRIPT = APP_DIR / "atlas" / "launcher.py"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the launcher is Linux only for now")


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args], check=True,
                   capture_output=True)


def head(cwd):
    return subprocess.run(["git", "-C", str(cwd), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A live copy with a shell crate, a state folder with a current slot, desktop.json in runtime
    mode, and fakes on PATH: cargo (builds the binary, or fails when FAIL_BUILD exists),
    notify-send and the slot python (each records how it was called)."""
    live, state, config, bin_ = (tmp_path / n for n in ("live", "state", "config", "fakebin"))
    for d in (live / "app" / "desktop" / "src-tauri", state, config, bin_):
        d.mkdir(parents=True)
    (live / "app" / "desktop" / "src-tauri" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    git(live, "init", "-q", "-b", "master")
    git(live, "add", "-A")
    git(live, "commit", "-q", "-m", "one")
    exe = tmp_path / "target" / "release" / "atlas-desktop"
    calls = tmp_path / "calls.jsonl"
    record = (f"import json, os, sys\nwith open({str(calls)!r}, 'a') as f:\n"
              "    f.write(json.dumps({'who': os.path.basename(sys.argv[0])[:-3], 'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
              "                        'target': os.environ.get('CARGO_TARGET_DIR'),\n"
              "                        'state': os.environ.get('ATLAS_STATE_DIR')}) + '\\n')\n")
    fake_cli(bin_, record + (
        f"if os.path.exists({str(tmp_path / 'FAIL_BUILD')!r}):\n    sys.exit(101)\n"
        f"exe = {str(exe)!r}\nos.makedirs(os.path.dirname(exe), exist_ok=True)\n"
        "open(exe, 'w').write('#!/bin/sh\\nexit 0\\n'); os.chmod(exe, 0o755)\n"), name="cargo")
    fake_cli(bin_, record, name="notify-send")
    slot = state / "runtime" / "slot-a"
    (slot / "app" / "atlas").mkdir(parents=True)
    (slot / "venv" / "bin").mkdir(parents=True)
    os.symlink(fake_cli(tmp_path, record, name="slotpython"), slot / "venv" / "bin" / "python")
    (slot / "slot.json").write_text(json.dumps({"commit": "c0ffee"}), encoding="utf-8")
    (state / "runtime" / "current.json").write_text(json.dumps({"slot": "a", "commit": "c0ffee"}), encoding="utf-8")
    (config / "desktop.json").write_text(json.dumps({"port": 8090, "root": str(tmp_path / "root"), "live": str(live),
                                                     "state": str(state)}), encoding="utf-8")
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(config))
    monkeypatch.setenv("ATLAS_STATE_DIR", str(state))
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")

    class W:
        pass
    w = W()
    w.tmp, w.live, w.state, w.exe, w.slot = tmp_path, live, state, exe, slot

    def read_calls(who=None):
        if not calls.exists():
            return []
        rows = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
        return [r for r in rows if who is None or r["who"] == who]
    w.calls = read_calls
    return w


def run_launcher(world, *args, exec_as="fake_shell"):
    """Run the launcher as the desktop entry does; `--exe` is the world's binary, which (once
    built) is replaced by a recorder so the final exec can be seen."""
    return subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "--exe", str(world.exe), *args],
                          capture_output=True, text=True, timeout=60, env=dict(os.environ))


def make_recording_exe(world):
    world.exe.parent.mkdir(parents=True, exist_ok=True)
    out = world.tmp / "exec.json"
    world.exe.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{out}"\n', encoding="utf-8")
    world.exe.chmod(0o755)
    return out


# -- the whole run ---------------------------------------------------------------------------------

@posix_only
def test_first_launch_updates_builds_then_starts_the_shell(world):
    r = run_launcher(world, "--open")
    assert r.returncode == 0, r.stderr
    upd = world.calls("slotpython")
    assert len(upd) == 1
    assert upd[0]["argv"] == ["-I", "-S", str(world.slot / "app" / "atlas" / "update_step.py"), "--live",
                              str(world.live), "--state", str(world.state)]
    assert upd[0]["cwd"] == str(world.state) and upd[0]["state"] == str(world.state)
    builds = world.calls("cargo")
    assert [b["argv"] for b in builds] == [["build", "--release", "--locked"]]
    assert builds[0]["cwd"] == str(world.live / "app" / "desktop" / "src-tauri")
    assert builds[0]["target"] == str(world.tmp / "target")
    assert len(world.calls("notify-send")) == 1  # "Updating ATLAS"
    stamp = json.loads((world.state / "launcher.json").read_text(encoding="utf-8"))
    assert stamp["built"] == head(world.live) and stamp["exe"] == str(world.exe)
    assert "build: exit 0" in (world.state / "launcher.log").read_text(encoding="utf-8")


@posix_only
def test_no_rebuild_until_the_live_head_moves(world):
    assert run_launcher(world).returncode == 0
    out = make_recording_exe(world)
    assert run_launcher(world, "--open").returncode == 0
    assert len(world.calls("cargo")) == 1  # nothing new: no build, no notification
    assert len(world.calls("notify-send")) == 1
    assert out.read_text(encoding="utf-8").splitlines() == ["--open"]  # the shell got the arguments
    assert len(world.calls("slotpython")) == 2  # the update step runs at every cold start
    (world.live / "README.md").write_text("two\n", encoding="utf-8")
    git(world.live, "add", "-A")
    git(world.live, "commit", "-q", "-m", "two")
    assert run_launcher(world).returncode == 0
    assert len(world.calls("cargo")) == 2
    assert json.loads((world.state / "launcher.json").read_text(encoding="utf-8"))["built"] == head(world.live)


@posix_only
def test_a_missing_binary_is_rebuilt_even_at_the_same_head(world):
    assert run_launcher(world).returncode == 0
    world.exe.unlink()
    assert run_launcher(world).returncode == 0
    assert len(world.calls("cargo")) == 2 and world.exe.is_file()


@posix_only
def test_a_failed_build_keeps_the_old_binary_and_says_so(world):
    assert run_launcher(world).returncode == 0
    stamp = (world.state / "launcher.json").read_text(encoding="utf-8")
    out = make_recording_exe(world)
    (world.live / "x.txt").write_text("x\n", encoding="utf-8")
    git(world.live, "add", "-A")
    git(world.live, "commit", "-q", "-m", "x")
    (world.tmp / "FAIL_BUILD").write_text("", encoding="utf-8")
    r = run_launcher(world, "--open")
    assert r.returncode == 0, r.stderr
    assert out.read_text(encoding="utf-8").splitlines() == ["--open"]  # the old one still starts
    assert (world.state / "launcher.json").read_text(encoding="utf-8") == stamp  # and is retried next time
    notes = world.calls("notify-send")
    assert "couldn't update" in " ".join(notes[-1]["argv"])
    assert "--urgency=critical" in notes[-1]["argv"]


@posix_only
def test_no_binary_and_a_failed_build_exits_1(world):
    (world.tmp / "FAIL_BUILD").write_text("", encoding="utf-8")
    r = run_launcher(world)
    assert r.returncode == 1
    assert "can't start" in " ".join(world.calls("notify-send")[-1]["argv"])


@posix_only
def test_without_runtime_mode_it_only_starts_the_shell(world):
    (Path(os.environ["ATLAS_CONFIG_DIR"]) / "desktop.json").write_text(json.dumps({"port": 8090}), encoding="utf-8")
    out = make_recording_exe(world)
    assert run_launcher(world, "--open").returncode == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["--open"]
    assert world.calls() == []
    (Path(os.environ["ATLAS_CONFIG_DIR"]) / "desktop.json").unlink()  # none at all (before the cut-over)
    assert run_launcher(world).returncode == 0 and world.calls() == []


@posix_only
def test_a_missing_live_copy_or_slot_skips_those_steps(world):
    import shutil
    shutil.rmtree(world.state / "runtime")
    assert run_launcher(world).returncode == 0
    assert world.calls("slotpython") == [] and len(world.calls("cargo")) == 1
    assert "no runtime slot" in (world.state / "launcher.log").read_text(encoding="utf-8")


# -- the pieces ------------------------------------------------------------------------------------

def test_runtime_mode():
    assert la.runtime_mode(None) is None
    assert la.runtime_mode({"port": 8090}) is None
    assert la.runtime_mode({"root": "/r", "python": "/p", "app_dir": "/a"}) is None  # legacy mode
    assert la.runtime_mode({"root": "/r", "live": "/l", "state": "/s"}) == ("/l", "/s")
    assert la.runtime_mode({"root": "", "live": "/l", "state": "/s"}) is None


def test_target_dir_of():
    assert la.target_dir_of("/home/t/.cache/atlas-target/release/atlas-desktop") == "/home/t/.cache/atlas-target"
    assert la.target_dir_of("/home/t/.cache/atlas-target/debug/atlas-desktop") is None
    assert la.target_dir_of("/usr/bin/atlas-desktop") is None


def test_shell_running_reads_comm_of_this_user(tmp_path):
    proc = tmp_path / "proc"
    for pid, comm in (("10", "bash"), ("11", "atlas-desktop"), ("self", "x")):
        (proc / pid).mkdir(parents=True)
        (proc / pid / "comm").write_text(comm + "\n", encoding="utf-8")
    assert la.shell_running(str(proc), uid=os.getuid()) is True
    assert la.shell_running(str(proc), uid=os.getuid() + 1) is False  # another user's shell
    (proc / "11" / "comm").write_text("atlas-desktop-x\n", encoding="utf-8")
    assert la.shell_running(str(proc), uid=os.getuid()) is False
    assert la.shell_running(str(tmp_path / "none")) is False


def test_config_path_follows_the_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_CONFIG_DIR", str(tmp_path / "c"))
    assert la.config_path() == str(tmp_path / "c" / "desktop.json")
    monkeypatch.delenv("ATLAS_CONFIG_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
    assert la.config_path() == str(tmp_path / "x" / "atlas" / "desktop.json")
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")
    assert la.config_path() == os.path.join(os.path.expanduser("~"), ".config", "atlas", "desktop.json")


@posix_only
def test_running_shell_is_handed_over_at_once(world, monkeypatch):
    monkeypatch.setattr(la, "shell_running", lambda *a, **k: True)
    seen = []
    make_recording_exe(world)
    assert la.main(["--exe", str(world.exe), "--open"], exec_fn=lambda exe, argv: seen.append(argv)) == 0
    assert seen == [[str(world.exe), "--open"]]
    assert world.calls() == []  # no update step, no build while a shell runs


@posix_only
def test_run_bounded_stops_a_child_at_its_limit(tmp_path):
    log = la.Log(str(tmp_path / "l.log"))
    started = time.monotonic()
    assert la.run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(tmp_path), env=dict(os.environ),
                          limit=1, log=log, what="sleeper") is None
    assert time.monotonic() - started < 10
    assert "still running after 1 s" in (tmp_path / "l.log").read_text(encoding="utf-8")


def test_the_log_is_cut_back(tmp_path, monkeypatch):
    monkeypatch.setattr(la, "LOG_MAX", 1000)
    path = tmp_path / "l.log"
    path.write_text("".join(f"line {i}\n" for i in range(400)), encoding="utf-8")
    la.Log(str(path)).trim()
    text = path.read_text(encoding="utf-8")
    assert len(text) <= 500 and text.endswith("line 399\n") and text.startswith("line ")


def test_stdlib_only():
    import ast
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "atlas" not in names
    assert all(n in sys.stdlib_module_names for n in names), names
