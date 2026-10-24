"""The rename's cut-over on the Python side (rename spec §2.2, §2.3): our own pre-rename Claude
Code entries (BrainHub's hooks, status line, `brainhub-request` wrapper and CLAUDE.md block) are
found and replaced, the update step copies the hook from a pre-rename slot, and desktop-entry
removes the old launcher entry. Temp folders only (conftest)."""
import json
import os
import sys
from pathlib import Path

import pytest

from atlas import claude_integration as ci
from atlas import desktop_entry as de
from atlas import legacy, osdirs
from atlas.config import Paths
from atlas.rootguard import check_root
from atlas.server import create_app, create_reduced_app
from tests.test_reduced import _make_brain_with_proposals
from tests.test_server import make_runner
from tests.test_update_step import us

PY = "/usr/bin/python3"
posix_only_link = pytest.mark.skipif(sys.platform == "win32", reason="the compat symlink is Linux's")
OLD_STATE = "/home/u/.local/share/brainhub"
OLD_HOOK = f"{OLD_STATE}/hook/hook_client.py"
OLD_DEV_HOOK = "/home/tudor/dev/BrainHub/app/brainhub/hook_client.py"
FOREIGN = {"type": "command", "command": "echo mine"}


def old_hook(script=OLD_HOOK, port=8090, is_async=False):
    """A hook entry as the pre-rename installer wrote it."""
    h = {"type": "command", "command": PY, "args": ["-I", "-S", script, "event", "--port", str(port)], "timeout": 5}
    return {**h, "async": True} if is_async else h


def old_settings(script=OLD_HOOK, passthrough=True):
    hooks = {ev: [{"hooks": [old_hook(script, is_async=ev in ci.ASYNC_EVENTS)]}] for ev in ci.EVENTS}
    hooks["Stop"].insert(0, {"hooks": [FOREIGN]})  # the owner's own hook, before ours
    line = f"{PY} -I -S {script} statusline --port 8090"
    if passthrough:
        line += f" --passthrough-file {OLD_STATE}/statusline_passthrough.txt"
    return {"model": "opus", "hooks": hooks, "statusLine": {"type": "command", "command": line}}


def old_wrapper():
    return ("#!/bin/sh\n"
            f"# {legacy.WRAPPER}: measurement requests for the workbench tablet ({legacy.WRAPPER_MARK})\n"
            f"exec {PY} -I -S {OLD_STATE}/hook/request_client.py --port 8090 \"$@\"\n")


OLD_BLOCK = [legacy.MD_BEGIN, "Need measurements? Run `brainhub-request`.", legacy.MD_END]


@pytest.fixture
def claude(tmp_path):
    """The temp Claude config folder (conftest's CLAUDE_CONFIG_DIR), the state folder and bin folder,
    seeded with a pre-rename install: settings, the wrapper and a CLAUDE.md with the old block in
    the middle of the owner's text."""
    d = Path(os.environ["CLAUDE_CONFIG_DIR"])
    d.mkdir(parents=True)
    state, bin_dir = osdirs.state_dir(), osdirs.bin_dir()
    state.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    (d / "settings.json").write_text(json.dumps(old_settings(), indent=2), "utf-8")
    # The moved state folder kept the owner's own status line, as the old install saved it.
    (state / ci.STATE_FILE).write_text(json.dumps({"previous_statusline": FOREIGN,
                                                   "installed_at": "2026-10-20T10:00:00"}), "utf-8")
    (bin_dir / legacy.WRAPPER).write_text(old_wrapper(), "utf-8")
    (d / "CLAUDE.md").write_text("\n".join(["# Mine", "", *OLD_BLOCK, "", "More of mine."]) + "\n", "utf-8")
    return d


def repair(claude, port=8090):
    return ci.repair_legacy(claude / "settings.json", port=port, state_dir=osdirs.state_dir())


def test_old_hooks_and_status_line_are_replaced(claude):
    assert repair(claude) is True
    s = json.loads((claude / "settings.json").read_text("utf-8"))
    python, script = ci.hook_paths(osdirs.state_dir())
    text = json.dumps(s)
    assert "brainhub" not in text.replace(r"\\", "/").lower()
    for ev in ci.EVENTS:
        ours = [g for g in s["hooks"][ev] if any(script in h.get("args", []) for h in g["hooks"])]
        assert len(ours) == 1 and ours[0]["hooks"][0]["command"] == python
    assert s["hooks"]["Stop"][0] == {"hooks": [FOREIGN]}  # the owner's hook kept, where it was
    assert s["model"] == "opus"
    passthrough = osdirs.state_dir() / ci.PASSTHROUGH_FILE
    assert s["statusLine"]["command"] == ci.statusline_command(python, script, 8090, passthrough)
    assert passthrough.read_text("utf-8") == "echo mine"  # the passthrough survives
    st = ci.status(claude / "settings.json", port=8090)
    assert st["hooks_installed"] and st["statusline_installed"] and st["paths_current"] and st["request_command"]
    assert list(claude.glob("settings.json.atlas-backup-*"))  # a backup first


def test_old_wrapper_and_block_are_replaced(claude):
    bin_dir = osdirs.bin_dir()
    assert repair(claude) is True
    assert not (bin_dir / legacy.WRAPPER).exists()
    assert ci.WRAPPER_MARK in ci.wrapper_path(bin_dir).read_text("utf-8")
    lines = (claude / "CLAUDE.md").read_text("utf-8").splitlines()
    assert lines == ["# Mine", "", ci.MD_BEGIN, ci.MD_LINE, ci.MD_END, "", "More of mine."]  # where it was
    assert list(claude.glob("CLAUDE.md.atlas-backup-*"))


def test_a_second_start_changes_nothing(claude):
    assert repair(claude) is True
    before = {p.name: p.read_bytes() for p in claude.iterdir()}
    assert repair(claude) is False
    assert {p.name: p.read_bytes() for p in claude.iterdir()} == before


def test_nothing_legacy_means_no_write(claude, tmp_path):
    f = claude / "settings.json"
    f.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [FOREIGN]}]}, "statusLine": FOREIGN}), "utf-8")
    before = {p.name: p.read_bytes() for p in claude.iterdir()}
    assert repair(claude) is False
    assert {p.name: p.read_bytes() for p in claude.iterdir()} == before
    # Spec §2.2: only old entries in the settings start a repair; the old wrapper stays until then.
    assert (osdirs.bin_dir() / legacy.WRAPPER).exists()
    # No settings file at all: nothing is written either.
    f.unlink()
    assert repair(claude) is False and not f.exists()


@pytest.mark.parametrize("script", [
    "/x/notbrainhub/hook_client.py",
    "/x/notbrainhub/hook/hook_client.py",
    "/opt/brainhub-tools/hook_client.py",
    "/opt/tools/hook/hook_client.py",
])
def test_someone_elses_hook_client_is_left_alone(claude, script):
    f = claude / "settings.json"
    f.write_text(json.dumps(old_settings(script, passthrough=False)), "utf-8")
    before = f.read_bytes()
    assert repair(claude) is False
    assert f.read_bytes() == before
    assert ci.status(f, port=8090)["hooks_installed"] is False


def test_the_dev_copys_old_hook_counts_as_ours(claude):
    (claude / "settings.json").write_text(json.dumps(old_settings(OLD_DEV_HOOK)), "utf-8")
    st = ci.status(claude / "settings.json", port=8090)
    assert st["hooks_installed"] and st["statusline_installed"] and not st["paths_current"]
    assert repair(claude) is True
    assert "app/brainhub/" not in (claude / "settings.json").read_text("utf-8")


def test_uninstall_removes_the_old_entries(claude):
    ci.uninstall(claude / "settings.json", state_dir=osdirs.state_dir())
    s = json.loads((claude / "settings.json").read_text("utf-8"))
    assert s == {"model": "opus", "hooks": {"Stop": [{"hooks": [FOREIGN]}]}, "statusLine": FOREIGN}
    assert not (osdirs.bin_dir() / legacy.WRAPPER).exists()
    assert (claude / "CLAUDE.md").read_text("utf-8") == "# Mine\n\nMore of mine.\n"


def test_an_unmarked_old_wrapper_is_not_ours(claude):
    w = osdirs.bin_dir() / legacy.WRAPPER
    w.write_text("#!/bin/sh\necho the owner's own\n", "utf-8")
    assert repair(claude) is True
    assert w.read_text("utf-8") == "#!/bin/sh\necho the owner's own\n"


def test_a_hub_still_on_the_legacy_state_folder_doesnt_reinstall_every_start(claude, monkeypatch):
    """The shell's folder move failed, so the hub runs on the old state folder: its current hook
    path is itself legacy-named. One repair (the wrapper, the block), then no more writes."""
    old_state = Path(os.environ["ATLAS_STATE_DIR"]).parent / "brainhub"
    old_state.mkdir()
    monkeypatch.setenv("ATLAS_STATE_DIR", str(old_state))
    python, script = ci.hook_paths(old_state)
    assert "brainhub" not in script  # dev mode: the package's own copy...
    stable = ci.stable_hook(old_state)  # ...so make it runtime mode, on the legacy folder
    stable.parent.mkdir()
    stable.write_bytes(ci.hook_script().read_bytes())
    (old_state / "runtime").mkdir()
    (old_state / "runtime" / "current.json").write_text("{}", "utf-8")
    python, script = ci.hook_paths(old_state)
    assert script == str(stable)
    ci.install(claude / "settings.json", python=python, script=script, port=8090, state_dir=old_state)
    (osdirs.bin_dir() / legacy.WRAPPER).write_text(old_wrapper(), "utf-8")
    assert ci.repair_legacy(claude / "settings.json", port=8090, state_dir=old_state) is True
    assert not (osdirs.bin_dir() / legacy.WRAPPER).exists()
    before = (claude / "settings.json").read_bytes()
    assert ci.repair_legacy(claude / "settings.json", port=8090, state_dir=old_state) is False
    assert (claude / "settings.json").read_bytes() == before


@pytest.fixture
def owner_hub(monkeypatch):
    """This test's hub counts as the owner's real one (runs_from_own_slot), so its start repairs."""
    monkeypatch.setattr(ci, "runs_from_own_slot", lambda state_dir: True)


async def test_a_full_start_repairs_quietly(aiohttp_client, paths, claude, owner_hub):
    app = create_app(paths, runner=make_runner(), start_workers=False, claude_settings_path=claude / "settings.json")
    client = await aiohttp_client(app)
    assert "brainhub" not in (claude / "settings.json").read_text("utf-8")
    assert not app["startup_errors"]
    view = await (await client.get("/api/integration")).json()
    assert view["hooks_installed"] and view["paths_current"] and view["request_command"]


async def test_a_failed_repair_is_only_logged(aiohttp_client, paths, claude, caplog, owner_hub):
    (claude / "settings.json").write_text("{ not json", "utf-8")
    app = create_app(paths, runner=make_runner(), start_workers=False, claude_settings_path=claude / "settings.json")
    await aiohttp_client(app)
    assert not app["startup_errors"]
    assert "could not replace the pre-rename Claude Code integration" in caplog.text
    assert (claude / "settings.json").read_text("utf-8") == "{ not json"


def _snapshot(*folders):
    return {str(p): p.read_bytes() for f in folders if f.exists() for p in sorted(f.rglob("*")) if p.is_file()}


async def test_a_dev_hub_never_touches_claude_code(aiohttp_client, paths, claude, monkeypatch):
    """The documented dev line (a temp ATLAS_STATE_DIR, the package from a checkout) with old
    entries in the settings: nothing is written, not even with the runtime flag and a runtime
    state folder, because the package doesn't run from that state folder's slot."""
    state = osdirs.state_dir()
    (state / "runtime").mkdir(parents=True, exist_ok=True)
    (state / "runtime" / "current.json").write_text("{}", "utf-8")
    ci.stable_hook(state).parent.mkdir(exist_ok=True)
    ci.stable_hook(state).write_bytes(ci.hook_script().read_bytes())
    monkeypatch.setenv(ci.RUNTIME_HUB_VAR, "1")
    before = _snapshot(claude, osdirs.bin_dir())
    app = create_app(paths, runner=make_runner(), start_workers=False, claude_settings_path=claude / "settings.json")
    await aiohttp_client(app)
    assert _snapshot(claude, osdirs.bin_dir()) == before
    assert ci.runs_from_own_slot(state) is False


async def test_a_reduced_start_repairs_too(aiohttp_client, tmp_path, claude, owner_hub):
    missing = Paths(tmp_path / "gone")  # a missing root: the reduced app
    app = create_reduced_app(missing, check_root(missing))
    await aiohttp_client(app)
    assert "brainhub" not in (claude / "settings.json").read_text("utf-8")
    assert not missing.root.exists()


async def test_a_dev_reduced_start_writes_nothing(aiohttp_client, tmp_path, claude):
    before = _snapshot(claude, osdirs.bin_dir())
    missing = Paths(tmp_path / "gone")
    await aiohttp_client(create_reduced_app(missing, check_root(missing)))
    assert _snapshot(claude, osdirs.bin_dir()) == before


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs a chmod-read-only folder")
async def test_a_read_only_start_repairs_too(aiohttp_client, tmp_path, claude, owner_hub):
    paths = Paths(tmp_path / "ro")
    eid, _ = await _make_brain_with_proposals(aiohttp_client, paths)
    (claude / "settings.json").write_text(json.dumps(old_settings(), indent=2), "utf-8")
    try:
        paths.root.chmod(0o555)
        app = create_app(paths, runner=make_runner(), start_workers=False, claude_settings_path=claude / "settings.json")
        assert app["root_status"].state == "readonly"
        await aiohttp_client(app)
    finally:
        paths.root.chmod(0o755)
    assert "brainhub" not in (claude / "settings.json").read_text("utf-8")


# -- the gate: only the owner's real hub repairs ---------------------------------------------
def _runtime_state(tmp_path) -> Path:
    state = tmp_path / "rt-state"
    (state / "runtime" / "slot-a" / "app" / "atlas").mkdir(parents=True)
    (state / "runtime" / "current.json").write_text("{}", "utf-8")
    ci.stable_hook(state).parent.mkdir()
    ci.stable_hook(state).write_text("# hook\n", "utf-8")
    return state


def test_runs_from_own_slot(tmp_path):
    state = _runtime_state(tmp_path)
    in_slot = state / "runtime" / "slot-a" / "app" / "atlas" / "claude_integration.py"
    on = {ci.RUNTIME_HUB_VAR: "1"}
    assert ci.runs_from_own_slot(state, package_file=in_slot, environ=on) is True
    assert ci.runs_from_own_slot(state, package_file=in_slot, environ={}) is False  # not the shell's
    assert ci.runs_from_own_slot(state, environ=on) is False  # this checkout's package
    other = _runtime_state(tmp_path / "other")
    assert ci.runs_from_own_slot(other, package_file=in_slot, environ=on) is False  # another state's slot
    (state / "runtime" / "current.json").unlink()
    assert ci.runs_from_own_slot(state, package_file=in_slot, environ=on) is False
    (state / "runtime" / "current.json").write_text("{}", "utf-8")
    ci.stable_hook(state).unlink()
    assert ci.runs_from_own_slot(state, package_file=in_slot, environ=on) is False


# -- the shell's compat link or copy at the old state folder ---------------------------------
@posix_only_link
def test_the_compat_link_goes_once_nothing_names_it(claude):
    state = osdirs.state_dir()
    old = legacy.state_dir()
    old.parent.mkdir(parents=True, exist_ok=True)
    old.symlink_to(state, target_is_directory=True)
    settings = claude / "settings.json"
    assert ci.remove_legacy_compat(settings, state) is None  # the settings still name it
    assert old.is_symlink()
    (settings.parent / "settings.json").write_text("{}", "utf-8")
    assert ci.remove_legacy_compat(settings, state) is None  # the old wrapper still names it
    (settings.parent / "settings.json").write_text(json.dumps(old_settings(), indent=2), "utf-8")
    assert repair(claude) is True
    assert ci.remove_legacy_compat(settings, state) == str(old)
    assert not old.exists() and not old.is_symlink() and state.is_dir()


@posix_only_link
def test_someone_elses_link_or_folder_stays(claude, tmp_path):
    state, old = osdirs.state_dir(), legacy.state_dir()
    old.parent.mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    old.symlink_to(elsewhere, target_is_directory=True)
    (claude / "settings.json").write_text("{}", "utf-8")
    assert ci.remove_legacy_compat(claude / "settings.json", state) is None
    assert old.is_symlink()
    old.unlink()
    old.mkdir()
    assert ci.remove_legacy_compat(claude / "settings.json", state) is None  # a real folder
    assert old.is_dir()


def test_the_windows_compat_copy_goes_only_when_exactly_ours(claude, tmp_path):
    (osdirs.bin_dir() / legacy.WRAPPER).unlink()  # the hub's repair removed it
    state, old = tmp_path / "ATLAS", tmp_path / "BrainHub"
    state.mkdir()
    (old / "hook").mkdir(parents=True)
    for name in legacy.COMPAT_FILES:
        (old / name).write_text("x", "utf-8")
    (claude / "settings.json").write_text(json.dumps({"statusLine": {"command": f"py {old}\\hook\\hook_client.py"}}), "utf-8")
    kw = {"legacy_dir": old, "os_name": "windows"}
    assert ci.remove_legacy_compat(claude / "settings.json", state, **kw) is None  # still named
    (claude / "settings.json").write_text("{}", "utf-8")
    (old / "mine.txt").write_text("the owner's", "utf-8")
    assert ci.remove_legacy_compat(claude / "settings.json", state, **kw) is None  # not only ours
    (old / "mine.txt").unlink()
    (old / legacy.COMPAT_MARKER).unlink()
    assert ci.remove_legacy_compat(claude / "settings.json", state, **kw) is None  # unmarked
    (old / legacy.COMPAT_MARKER).write_text("x", "utf-8")
    assert ci.remove_legacy_compat(claude / "settings.json", state, **kw) == str(old)
    assert not old.exists()


@posix_only_link
async def test_a_full_start_repairs_then_drops_the_link(aiohttp_client, paths, claude, owner_hub):
    state, old = osdirs.state_dir(), legacy.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    old.parent.mkdir(parents=True, exist_ok=True)
    old.symlink_to(state, target_is_directory=True)
    await aiohttp_client(create_app(paths, runner=make_runner(), start_workers=False,
                                    claude_settings_path=claude / "settings.json"))
    assert "brainhub" not in (claude / "settings.json").read_text("utf-8")
    assert not old.is_symlink()


# -- the update step: a slot from before the rename (spec §2.3) ------------------------------
def _slot(state: Path, package: str) -> dict:
    app = state / "runtime" / "slot-a" / "app" / package
    app.mkdir(parents=True)
    (app / "hook_client.py").write_text(f"# hook from {package}\n", "utf-8")
    (app / "request_client.py").write_text(f"# request from {package}\n", "utf-8")
    return {"slot": "a"}


@pytest.mark.parametrize("package", ["atlas", legacy.NAME])
def test_install_hook_from_either_package(tmp_path, package):
    state = tmp_path / "st"
    us.install_hook(str(state), _slot(state, package))
    assert (state / "hook" / "hook_client.py").read_text("utf-8") == f"# hook from {package}\n"
    assert (state / "hook" / "request_client.py").read_text("utf-8") == f"# request from {package}\n"


def test_install_hook_prefers_the_new_package(tmp_path):
    state = tmp_path / "st"
    cur = _slot(state, legacy.NAME)
    (state / "runtime" / "slot-a" / "app" / "atlas").mkdir()
    (state / "runtime" / "slot-a" / "app" / "atlas" / "hook_client.py").write_text("# new\n", "utf-8")
    us.install_hook(str(state), cur)
    assert (state / "hook" / "hook_client.py").read_text("utf-8") == "# new\n"
    assert us.LEGACY_PACKAGE == legacy.NAME


def test_install_hook_without_any_package_fails(tmp_path):
    state = tmp_path / "st"
    (state / "runtime" / "slot-a" / "app").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        us.install_hook(str(state), {"slot": "a"})


# -- desktop-entry: the old launcher entry and icon ------------------------------------------
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the launcher entry is Linux only for now")


def _exe(tmp_path):
    exe = tmp_path / "bin" / "atlas-desktop"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\nexit 0\n", "utf-8")
    exe.chmod(0o755)
    return exe


def _old_entry(home: Path, text: str | None, icon: bytes | None):
    desktop, icon_path = de.legacy_paths(home)
    if text is not None:
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(text, "utf-8")
    if icon is not None:
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        icon_path.write_bytes(icon)
    return desktop, icon_path


OLD_ENTRY = ("[Desktop Entry]\nType=Application\nName=BrainHub\nExec=\"/x/brainhub-desktop\" --open\n"
             f"{legacy.LAUNCHER_ICON_LINE}\nStartupWMClass=brainhub-desktop\n")


@posix_only
def test_install_removes_the_old_launcher_entry(tmp_path):
    home = tmp_path / "home"
    desktop, icon = _old_entry(home, OLD_ENTRY, de.ICON_SOURCE.read_bytes())
    result = de.install(_exe(tmp_path), home)
    assert result["removed_legacy"] == [str(desktop), str(icon)] and result["changed"]
    assert not desktop.exists() and not icon.exists()
    assert de.paths(home)[0].exists() and de.paths(home)[1].exists()
    assert de.install(_exe(tmp_path), home)["changed"] is False


@posix_only
def test_an_old_launcher_entry_that_isnt_ours_stays(tmp_path):
    home = tmp_path / "home"
    desktop, icon = _old_entry(home, "[Desktop Entry]\nName=Something else\nIcon=other\n", b"not our icon")
    assert de.install(_exe(tmp_path), home)["removed_legacy"] == []
    assert desktop.exists() and icon.read_bytes() == b"not our icon"


@posix_only
def test_our_old_icon_alone_is_removed(tmp_path):
    home = tmp_path / "home"
    _, icon = _old_entry(home, None, de.ICON_SOURCE.read_bytes())
    assert de.install(_exe(tmp_path), home)["removed_legacy"] == [str(icon)]

