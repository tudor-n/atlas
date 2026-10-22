"""claude-sync (plan Task 11, ruling D2: option 2, autoMemoryDirectory instead of links).

Every test uses a temp claude dir, a temp shared store, a temp state dir, a temp home and a temp
projects root, passed explicitly; HOME/USERPROFILE point at a temp folder too (autouse fixture
below), and conftest points CLAUDE_CONFIG_DIR, BRAINHUB_STATE_DIR and BRAINHUB_SHARED_DIR at temp
folders. Nothing here reads or writes the real ~/.claude, ~/dev or /mnt/data. Windows-only cases
(junctions, drive-letter keys, default folders) are skipped on Linux and run in CI on Windows.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from brainhub import claude_sync as cs

WIN = sys.platform == "win32"
OS = "windows" if WIN else "linux"
APP = Path(__file__).resolve().parent.parent
ENTRY_ID = "e-20261020-101500-ab12"
windows_only = pytest.mark.skipif(not WIN, reason="Windows only (runs in CI on windows-latest)")
posix_only = pytest.mark.skipif(WIN, reason="POSIX only (the Windows side has its own test)")


@pytest.fixture(autouse=True)
def _temp_home(tmp_path, monkeypatch):
    """Even a default that slipped through (~/dev, ~/.claude) lands in a temp folder."""
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def tree(root: Path) -> dict:
    """Every file and link under root: rel path -> bytes (or "link -> target")."""
    out = {}
    if not os.path.lexists(root):
        return out
    for top, dirs, files in os.walk(root):
        for name in dirs + files:
            p = Path(top) / name
            rel = p.relative_to(root).as_posix()
            if cs.is_link(p):
                out[rel] = f"link -> {os.readlink(p)}"
            elif p.is_file():
                out[rel] = p.read_bytes()
    return out


class World:
    """One OS: its own claude dir, state dir, home and projects root, sharing one store."""

    def __init__(self, base: Path, shared: Path, os_name: str = OS):
        self.base, self.shared, self.os = base, shared, os_name
        self.claude, self.state = base / "claude", base / "state"
        self.home, self.root = base / "home", base / "dev"
        for d in (self.claude, self.home, self.root):
            d.mkdir(parents=True, exist_ok=True)

    def key_dir(self, folder) -> Path:
        d = self.claude / "projects" / cs.encode_key(folder)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def memory(self, folder, notes: dict | None = None) -> Path:
        d = self.key_dir(folder) / "memory"
        d.mkdir(parents=True, exist_ok=True)
        for name, text in (notes or {}).items():
            (d / name).write_text(text, "utf-8")
        return d

    def project(self, name: str) -> Path:
        p = self.root / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def sync(self, **kw) -> dict:
        kw.setdefault("os_name", self.os)
        return cs.run(self.shared, self.claude, self.state, home=self.home, projects_root=self.root, **kw)

    @property
    def settings_path(self) -> Path:
        return self.claude / "settings.json"

    def settings(self) -> dict:
        return json.loads(self.settings_path.read_text("utf-8"))

    def write_settings(self, obj: dict, mtime: float) -> None:
        self.settings_path.write_text(json.dumps(obj, indent=2), "utf-8")
        os.utime(self.settings_path, (mtime, mtime))

    def local_settings(self, folder: Path) -> dict:
        return json.loads((folder / ".claude" / "settings.local.json").read_text("utf-8"))


@pytest.fixture
def shared(tmp_path) -> Path:
    return cs.init_store(tmp_path / "ClaudeShared")


@pytest.fixture
def a(tmp_path, shared) -> World:
    return World(tmp_path / "os-a", shared, "linux")


@pytest.fixture
def b(tmp_path, shared) -> World:
    return World(tmp_path / "os-b", shared, "windows")


def store(shared: Path) -> dict:
    return json.loads((shared / cs.STORE_SETTINGS).read_text("utf-8"))


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-c", f"safe.directory={str(repo.resolve()).replace(os.sep, '/')}", *args],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


T0 = time.time() - 100_000  # settings files get explicit, well-separated modification times


# -- keys and pairing --------------------------------------------------------------------------

def test_key_encoding_matches_ruling_d2():
    assert cs.encode_key("/home/tudor/dev/X") == "-home-tudor-dev-X"
    assert cs.encode_key("/tmp/bh-mem-spike") == "-tmp-bh-mem-spike"
    assert cs.encode_key("/home/tudor/.config/a_b") == "-home-tudor--config-a-b"
    assert cs.encode_key(r"D:\RobotArmControl") == "D--RobotArmControl"
    assert cs.encode_key(r"C:\Users\Tudor") == "C--Users-Tudor"


def test_drive_letter_case_keys_pair():
    assert cs.fold_key("d--RobotArmControl", "windows") == cs.fold_key("D--RobotArmControl", "windows")
    assert cs.fold_key("c--Users-Tudor", "windows") == "C--Users-Tudor"
    assert cs.fold_key("d--X", "linux") != cs.fold_key("D--X", "linux")  # Linux keys compare as they are
    assert cs.fold_key("-home-tudor", "windows") == "-home-tudor"
    assert cs.fold_key("d--x-Y", "windows") != cs.fold_key("D--X-Y", "windows")  # only the drive letter


@windows_only
def test_drive_letter_case_keys_pair_on_windows(tmp_path, shared):
    w = World(tmp_path / "win", shared, "windows")
    folder = w.project("Robot")
    key = cs.encode_key(folder)
    assert key[1:3] == "--"
    lower = key[0].lower() + key[1:]  # Claude Code recorded the path with a lower-case drive letter
    d = w.claude / "projects" / lower / "memory"
    d.mkdir(parents=True)
    (d / "arm.md").write_text("servo 3 is weak", "utf-8")
    s = w.sync()
    assert s["memory"]["pairs"] == ["Robot"]
    assert (shared / "memory" / "projects" / "Robot" / "arm.md").read_text("utf-8") == "servo 3 is weak"
    assert w.local_settings(folder) == {"autoMemoryDirectory": str(shared / "memory" / "projects" / "Robot")}


def test_points_home_and_paired_projects(a, shared, tmp_path):
    a.memory(a.home, {"MEMORY.md": "- [Me](me.md) — who\n", "me.md": "owner"})
    robot = a.project("Robot")
    a.memory(robot, {"arm.md": "servo 3"})
    (robot / ".claude").mkdir()
    (robot / ".claude" / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}, "autoMemoryDirectory": "/old"}), "utf-8")
    a.key_dir(a.project("Quiet"))  # paired, but no notes on either side
    scratch = tmp_path / "scratch"
    a.memory(scratch, {"tmp.md": "scratch note"})  # not in the projects root: never paired
    arm = a.project("Arm")  # notes only in the store (made on the other OS), no key here yet
    (shared / "memory" / "projects" / "Arm").mkdir()
    (shared / "memory" / "projects" / "Arm" / "x.md").write_text("from windows", "utf-8")
    (shared / "memory" / "projects" / "Gone").mkdir()  # its folder doesn't exist on this OS
    (shared / "memory" / "projects" / "Gone" / "y.md").write_text("elsewhere", "utf-8")

    s = a.sync()

    assert s["memory"]["pairs"] == ["home", "Arm", "Robot"]
    assert json.loads((a.claude / "settings.local.json").read_text("utf-8")) == {
        "autoMemoryDirectory": str(shared / "memory" / "home")}
    assert a.local_settings(robot) == {"permissions": {"allow": ["Bash(ls:*)"]},
                                       "autoMemoryDirectory": str(shared / "memory" / "projects" / "Robot")}
    assert a.local_settings(arm) == {"autoMemoryDirectory": str(shared / "memory" / "projects" / "Arm")}
    assert not (a.root / "Quiet" / ".claude").exists()
    assert not (a.root / "Gone").exists()
    assert (shared / "memory" / "projects" / "Robot" / "arm.md").read_text("utf-8") == "servo 3"
    assert (shared / "memory" / "home" / "me.md").read_text("utf-8") == "owner"
    assert not any("scratch" in p.name.lower() for p in (shared / "memory" / "projects").iterdir())
    assert s["memory"]["unpaired"] == 1
    assert not s["problems"]
    again = a.sync()  # nothing new: nothing written
    assert again["memory"]["pointed"] == [] and again["memory"]["merged"] == []


def test_bad_settings_local_left_alone(a, shared):
    robot = a.project("Robot")
    a.memory(robot, {"arm.md": "servo 3"})
    (robot / ".claude").mkdir()
    (robot / ".claude" / "settings.local.json").write_text("{not json", "utf-8")
    s = a.sync()
    assert (robot / ".claude" / "settings.local.json").read_text("utf-8") == "{not json"
    assert "Robot" not in s["memory"]["pointed"]
    assert any("not valid JSON" in p for p in s["problems"]["memory"])


def test_pairs_json_exception(a, shared, tmp_path):
    renamed = a.project("robot-arm")  # in the root, but pairs.json files it under RobotArm
    far = tmp_path / "elsewhere" / "FarAway"
    far.mkdir(parents=True)
    (shared / cs.PAIRS_FILE).write_text(json.dumps({
        "RobotArm": {a.os: str(renamed), "windows" if a.os == "linux" else "linux": "X:\\Robot"},
        "Far": {a.os: str(far)},
        "bad/name": {a.os: str(far)},
    }), "utf-8")
    a.memory(renamed, {"arm.md": "servo"})
    a.memory(far, {"far.md": "far note"})
    s = a.sync()
    projects = shared / "memory" / "projects"
    assert (projects / "RobotArm" / "arm.md").read_text("utf-8") == "servo"
    assert (projects / "Far" / "far.md").read_text("utf-8") == "far note"
    assert not (projects / "robot-arm").exists()
    assert a.local_settings(renamed)["autoMemoryDirectory"] == str(projects / "RobotArm")
    assert a.local_settings(far)["autoMemoryDirectory"] == str(projects / "Far")
    assert any("bad/name" in p for p in s["problems"]["memory"])


def test_hub_filing_worktree_keys_never_paired(a, shared):
    """Ruling D11: the hub's own claude -p filing runs left keys for data/worktrees/<entry id>.
    They are never paired, merged or pointed, whatever their notes."""
    brainhub = a.project("BrainHub")
    worktree = brainhub / "data" / "worktrees" / ENTRY_ID
    worktree.mkdir(parents=True)
    a.memory(worktree, {"junk.md": "agent note"})
    for key in (f"-mnt-data-BrainHub-data-worktrees-{ENTRY_ID}", f"D--BrainHub-data-worktrees-{ENTRY_ID}",
                f"d--BrainHub-data-worktrees-{ENTRY_ID}"):
        d = a.claude / "projects" / key / "memory"
        d.mkdir(parents=True, exist_ok=True)
        (d / "junk.md").write_text("agent note", "utf-8")
    (shared / cs.PAIRS_FILE).write_text(json.dumps({"Filing": {a.os: str(worktree)}}), "utf-8")
    a.memory(brainhub, {"real.md": "the real project note"})

    s = a.sync()

    assert s["memory"]["pairs"] == ["BrainHub"]  # the project itself still pairs
    assert sorted(p.name for p in (shared / "memory" / "projects").iterdir() if not p.name.startswith(".")) \
        == ["BrainHub"]
    assert not (worktree / ".claude").exists()
    assert not list((shared / "memory").rglob("junk.md"))
    assert s["memory"]["unpaired"] == 0  # skipped outright, not even counted
    assert cs.is_hub_internal(f"-mnt-data-BrainHub-data-worktrees-{ENTRY_ID}")
    assert cs.is_hub_internal(worktree)
    assert cs.is_hub_internal(r"D:\BrainHub\data\worktrees\e-20261020-101500-ab12")
    assert not cs.is_hub_internal("-home-tudor-dev-BrainHub")
    assert not cs.is_hub_internal(brainhub)


# -- merging -------------------------------------------------------------------------------------

def test_local_folder_backed_up_before_link(a, shared):
    """Option 2: the local folder is backed up, merged, and then left in place (no link)."""
    local = a.memory(a.home, {"MEMORY.md": "- [A](a.md) — a\n", "a.md": "mine"})
    (shared / "memory" / "home" / "a.md").write_text("theirs", "utf-8")
    before = tree(local)
    a.sync()
    backups = [p for p in (a.state / cs.BACKUPS).iterdir() if p.name != "settings"]
    assert len(backups) == 1 and backups[0].name.startswith(cs.encode_key(a.home) + "-")
    assert tree(backups[0]) == before
    assert not cs.is_link(local) and tree(local) == before  # still there, unchanged
    a.sync()  # nothing new locally: no second backup
    assert len([p for p in (a.state / cs.BACKUPS).iterdir() if p.name != "settings"]) == 1
    (local / "b.md").write_text("new note", "utf-8")
    a.sync()
    assert len([p for p in (a.state / cs.BACKUPS).iterdir() if p.name != "settings"]) == 2
    assert (shared / "memory" / "home" / "b.md").read_text("utf-8") == "new note"


def test_merge_keeps_both_on_conflict(a, shared):
    home = shared / "memory" / "home"
    (home / "user.md").write_text("Windows version", "utf-8")
    (home / "MEMORY.md").write_text(f"{cs.HEADER}\n- [User](user.md) — profile\n", "utf-8")
    a.memory(a.home, {"user.md": "Linux version", "MEMORY.md": "- [User](user.md) — profile\n"})
    s = a.sync()
    assert (home / "user.md").read_text("utf-8") == "Windows version"
    assert (home / "user.linux.md").read_text("utf-8") == "Linux version"
    assert s["memory"]["merged"][0]["kept_both"] == ["user.linux.md"]
    assert (home / "MEMORY.md").read_text("utf-8").splitlines() == [
        cs.HEADER, "- [User](user.md) — profile", "- [User](user.linux.md) — profile"]
    # a third, different version from a later change here gets its own name
    (a.claude / "projects" / cs.encode_key(a.home) / "memory" / "user.md").write_text("Linux v2", "utf-8")
    a.sync()
    assert (home / "user.linux-2.md").read_text("utf-8") == "Linux v2"
    assert (home / "user.linux.md").read_text("utf-8") == "Linux version"


def test_memory_index_merged_without_duplicates(a, shared):
    home = shared / "memory" / "home"
    (home / "MEMORY.md").write_text(f"{cs.HEADER}\n- [A](a.md) — x\n- [B](b.md) — y\n\n", "utf-8")
    a.memory(a.home, {"MEMORY.md": f"{cs.HEADER}\n- [B](b.md) — y\n\n- [C](c.md) — z\n- [C](c.md) — z  \n",
                      "c.md": "c"})
    a.sync()
    assert (home / "MEMORY.md").read_text("utf-8") == \
        f"{cs.HEADER}\n- [A](a.md) — x\n- [B](b.md) — y\n- [C](c.md) — z\n"


def test_header_line_kept_once(a, shared):
    home = shared / "memory" / "home"
    (home / "MEMORY.md").write_text("- [A](a.md) — x\n", "utf-8")
    proj = shared / "memory" / "projects" / "Robot"
    proj.mkdir()
    (proj / "MEMORY.md").write_text(f"- [A](a.md)\n{cs.HEADER}\n- [B](b.md)\n  {cs.HEADER}\n", "utf-8")
    a.sync()
    assert (home / "MEMORY.md").read_text("utf-8") == f"{cs.HEADER}\n- [A](a.md) — x\n"
    assert (proj / "MEMORY.md").read_text("utf-8") == f"{cs.HEADER}\n- [A](a.md)\n- [B](b.md)\n"
    first = tree(shared / "memory")
    a.sync()
    assert tree(shared / "memory") == first  # never duplicated, nothing rewritten


def test_never_deletes_notes(a, shared):
    home = shared / "memory" / "home"
    (home / "a.md").write_text("theirs", "utf-8")
    (home / "c.md").write_text("only theirs", "utf-8")
    local = a.memory(a.home, {"a.md": "mine", "b.md": "two", "MEMORY.md": "- [A](a.md)\n"})
    local_before = tree(local)
    contents = {b"theirs", b"only theirs", b"mine", b"two"}
    for _ in range(2):
        a.sync()
    (local / "a.md").write_text("mine again", "utf-8")
    contents.add(b"mine again")
    for _ in range(2):
        a.sync()
    after = tree(home)
    assert contents <= set(after.values())  # every version of every note is still there
    assert sorted(after) == [".gitkeep", "MEMORY.md", "a.linux-2.md", "a.linux.md", "a.md", "b.md", "c.md"]
    assert set(tree(local)) == set(local_before)  # the local folder lost nothing


# -- skills --------------------------------------------------------------------------------------

def _skill(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(text, "utf-8")
    return folder


def test_skills_moved_and_linked_except_managed(a, shared, tmp_path):
    skills, store_skills = a.claude / "skills", shared / "skills"
    _skill(skills / "mine", "my skill")
    _skill(skills / "synced" / "bucket", "claude.ai sync")
    _skill(skills / "plug", "from a plugin")
    (a.claude / "plugins" / "plug").mkdir(parents=True)
    elsewhere = _skill(tmp_path / "elsewhere-skill", "linked by hand")
    cs.make_link(elsewhere, skills / "byhand")
    _skill(store_skills / "other", "made on the other OS")
    _skill(skills / "same", "identical")
    _skill(store_skills / "same", "identical")
    _skill(skills / "clash", "this OS")
    _skill(store_skills / "clash", "other OS")

    s = a.sync()

    assert s["skills"]["moved"] == ["mine"]
    assert sorted(s["skills"]["linked"]) == ["other", "same"]
    assert s["skills"]["conflicts"] == ["clash"]
    for name in ("mine", "other", "same"):
        assert cs.is_link(skills / name)
        assert Path(os.path.realpath(skills / name)) == Path(os.path.realpath(store_skills / name))
    assert (skills / "mine" / "SKILL.md").read_text("utf-8") == "my skill"
    assert (store_skills / "mine" / "SKILL.md").read_text("utf-8") == "my skill"
    backups = sorted(p.name.rsplit("-", 2)[0] for p in (a.state / cs.BACKUPS / "skills").iterdir())
    assert backups == ["mine", "same"]  # the originals are kept, never deleted
    assert not cs.is_link(skills / "synced") and not cs.is_link(skills / "plug")
    assert not (store_skills / "synced").exists() and not (store_skills / "plug").exists()
    assert os.path.realpath(skills / "byhand") == os.path.realpath(elsewhere)
    assert not cs.is_link(skills / "clash")
    assert (skills / "clash" / "SKILL.md").read_text("utf-8") == "this OS"
    assert (store_skills / "clash" / "SKILL.md").read_text("utf-8") == "other OS"
    assert any("clash" in p for p in s["problems"]["skills"])
    again = a.sync()
    assert again["skills"]["moved"] == [] and again["skills"]["linked"] == []


@windows_only
def test_skills_linked_with_junctions_on_windows(a, shared):
    _skill(a.claude / "skills" / "mine", "my skill")
    _skill(shared / "skills" / "other", "theirs")
    a.sync()
    for name in ("mine", "other"):
        link = a.claude / "skills" / name
        assert os.path.isjunction(link) and not os.path.islink(link)
        assert (link / "SKILL.md").exists()


@posix_only
def test_skills_linked_with_symlinks_on_posix(a, shared):
    _skill(a.claude / "skills" / "mine", "my skill")
    a.sync()
    assert os.path.islink(a.claude / "skills" / "mine")


# -- settings ------------------------------------------------------------------------------------

def _baseline(a, b, settings_a: dict, settings_b: dict) -> None:
    a.write_settings(settings_a, T0)
    b.write_settings(settings_b, T0 + 10)
    a.sync()
    b.sync()


def test_shared_key_change_flows_out(a, b, shared):
    _baseline(a, b, {"model": "sonnet"}, {"model": "sonnet"})
    a.write_settings({"model": "opus"}, T0 + 100)  # /model opus on OS A
    s = a.sync()
    assert s["settings"]["recorded"] == ["model"]
    assert store(shared)["settings"]["model"]["value"] == "opus"
    s = b.sync()
    assert s["settings"]["applied"] == ["model"] and b.settings() == {"model": "opus"}


@pytest.mark.parametrize("b_later", [True, False])
def test_later_change_wins(a, b, shared, b_later):
    _baseline(a, b, {"model": "sonnet"}, {"model": "sonnet"})
    a.write_settings({"model": "opus"}, T0 + 100)
    b.write_settings({"model": "haiku"}, T0 + (200 if b_later else 50))
    a.sync()
    b.sync()
    a.sync()
    winner = "haiku" if b_later else "opus"
    assert a.settings()["model"] == b.settings()["model"] == winner
    assert store(shared)["settings"]["model"]["value"] == winner


@pytest.mark.parametrize("local_newer", [True, False])
def test_first_sync_compares_file_times(a, b, shared, local_newer):
    a.write_settings({"model": "opus"}, T0 + 100)
    a.sync()
    b.write_settings({"model": "sonnet"}, T0 + (200 if local_newer else 50))  # b has never synced
    b.sync()
    assert b.settings()["model"] == ("sonnet" if local_newer else "opus")


def test_removal_propagates(a, b, shared):
    common = {"enabledPlugins": {"x@m": True, "y@m": True}, "tui": {"compact": True}}
    _baseline(a, b, common, common)
    a.write_settings({"enabledPlugins": {"x@m": True}}, T0 + 100)  # y removed, tui deleted
    a.sync()
    assert store(shared)["settings"]["tui"]["deleted"] is True
    b.sync()
    assert b.settings() == {"enabledPlugins": {"x@m": True}}


def test_per_os_keys_never_shared(a, b, shared, tmp_path):
    hooks_a = {"Stop": [{"hooks": [{"type": "command", "command": "/home/tudor/x.sh"}]}]}
    status_a = {"type": "command", "command": "python3 /home/tudor/statusline.py"}
    a.write_settings({"model": "opus", "hooks": hooks_a, "statusLine": status_a,
                      "permissions": {"allow": ["Read(/home/tudor/**)"]}, "env": {"A": "1"},
                      "defaultShell": "bash", "apiKeyHelper": "pass claude",
                      "someTool": "/home/tudor/bin/tool", "winTool": {"path": "C:\\Tools\\t.exe"},
                      "theme": "dark"}, T0)
    cs.set_per_os_keys(shared, ["theme"], a.state)
    mine_b = {"hooks": {"Stop": []}, "statusLine": {"type": "command", "command": "D:/x.cmd"},
              "permissions": {"allow": []}, "theme": "light", "someTool": "D:\\bin\\tool.exe"}
    b.write_settings(mine_b, T0 + 10)
    a.sync()
    b.sync()
    st = store(shared)
    assert set(st["settings"]) == {"model"}
    assert st["per_os_keys"] == ["theme"]
    text = (shared / cs.STORE_SETTINGS).read_text("utf-8")
    assert "/home/tudor" not in text and "Tools" not in text and "\r" not in text
    assert b.settings() == {**mine_b, "model": "opus"}  # b's own per-OS keys untouched
    assert set(cs.DEFAULT_PER_OS_KEYS) >= {"hooks", "statusLine", "permissions", "env", "defaultShell", "apiKeyHelper"}
    # A key the owner marks per-OS after it was shared is dropped from the store.
    cs.set_per_os_keys(shared, ["model", "hooks"], a.state)
    assert store(shared) == {"per_os_keys": ["model"], "settings": {}}


def test_abs_path_detection():
    for value in ("/home/tudor/x", "python3 /usr/bin/x", "C:\\Tools\\x.exe", "D:/BrainHub", "\\\\server\\share",
                  {"cmd": ["run", "--file=/etc/x"]}, {"/abs/key": True}, '"C:\\x"'):
        assert cs.holds_abs_path(value), value
    for value in ("opus", "https://github.com/a/b", "anthropics/claude-plugins", "~/notes", "a/b",
                  {"x@m": True}, ["Bash(git:*)"], 3, None, True, "10/22"):
        assert not cs.holds_abs_path(value), value


def test_settings_written_atomically_with_backup(a, b, shared):
    hooks_b = {"Stop": [{"hooks": [{"type": "command", "command": "D:/hook.cmd"}]}]}
    _baseline(a, b, {"model": "sonnet"}, {"model": "sonnet", "hooks": hooks_b})
    old_text = b.settings_path.read_bytes()
    old_inode = b.settings_path.stat().st_ino
    a.write_settings({"model": "opus"}, T0 + 100)
    a.sync()
    b.sync()
    backups = list((b.state / cs.BACKUPS / "settings").glob("settings.json-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == old_text
    assert b.settings() == {"model": "opus", "hooks": hooks_b}
    raw = b.settings_path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"}\n")
    assert not [p for p in b.claude.iterdir() if p.name.endswith(".tmp")]
    if not WIN:
        assert b.settings_path.stat().st_ino != old_inode  # replaced, not rewritten in place


def test_bad_settings_json_left_alone(a, shared):
    a.settings_path.write_text("{oops", "utf-8")
    s = a.sync()
    assert a.settings_path.read_text("utf-8") == "{oops"
    assert any("not valid JSON" in p for p in s["problems"]["settings"])


# -- availability, dry run, lock -----------------------------------------------------------------

def test_missing_shared_dir_changes_nothing(tmp_path, monkeypatch):
    w = World(tmp_path / "os", tmp_path / "nope")
    w.memory(w.home, {"a.md": "mine"})
    (w.claude / "skills" / "mine").mkdir(parents=True)
    w.write_settings({"model": "opus"}, T0)
    before = tree(w.claude)
    s = w.sync()
    assert s["available"] is False and "not found" in s["reason"]
    assert not (tmp_path / "nope").exists()  # never created
    assert tree(w.claude) == before
    assert json.loads((w.state / cs.SUMMARY_FILE).read_text("utf-8"))["available"] is False
    assert "nothing changed" in (w.state / cs.LOG_FILE).read_text("utf-8")
    # an empty folder (an unmounted mount point, say) is not a store either
    (tmp_path / "empty").mkdir()
    w.shared = tmp_path / "empty"
    assert w.sync()["available"] is False
    assert list((tmp_path / "empty").iterdir()) == [] and tree(w.claude) == before
    # nor one that can't be written
    w.shared = cs.init_store(tmp_path / "store")
    store_before = tree(w.shared)

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Read-only file system")
    monkeypatch.setattr(cs.tempfile, "mkstemp", refuse)
    s = w.sync()
    assert s["available"] is False and "can't be written" in s["reason"]
    assert tree(w.claude) == before and tree(w.shared) == store_before


def test_dry_run_writes_nothing(a, shared):
    a.memory(a.home, {"a.md": "mine"})
    (a.claude / "skills" / "mine").mkdir(parents=True)
    a.write_settings({"model": "opus"}, T0)
    before_claude, before_shared = tree(a.claude), tree(shared)
    s = a.sync(dry_run=True)
    assert s["dry_run"] is True and s["memory"]["pointed"] == ["home"] and s["skills"]["moved"] == ["mine"]
    assert s["settings"]["recorded"] == ["model"]
    assert tree(a.claude) == before_claude and tree(shared) == before_shared
    assert not a.state.exists()


def test_busy_lock_skips(a, shared):
    a.memory(a.home, {"a.md": "mine"})
    fd = cs.take_lock(a.state)
    try:
        assert a.sync() == {"skipped": "claude-sync already running"}
        assert not (shared / "memory" / "home" / "a.md").exists()
    finally:
        cs.release_lock(fd)
    assert a.sync()["memory"]["pairs"] == ["home"]


def test_settings_only_keeps_the_rest_of_the_summary(a, shared):
    a.memory(a.home, {"a.md": "mine"})
    (a.claude / "skills" / "mine").mkdir(parents=True)
    a.sync()
    (a.claude / "skills" / "later").mkdir()
    a.write_settings({"model": "opus"}, T0)
    s = a.sync(settings_only=True)
    assert s["settings"]["recorded"] == ["model"]
    assert s["memory"]["pairs"] == ["home"]  # from the full run
    assert not cs.is_link(a.claude / "skills" / "later")  # skills wait for the next full run
    assert json.loads((a.state / cs.SUMMARY_FILE).read_text("utf-8"))["settings"]["recorded"] == ["model"]


def test_project_settings_local_excluded_from_git(a, shared):
    robot = a.project("Robot")
    git(robot, "init", "-q")
    a.memory(robot, {"arm.md": "servo"})
    a.sync()
    a.sync()
    lines = (robot / ".git" / "info" / "exclude").read_text("utf-8").splitlines()
    assert lines.count("/.claude/settings.local.json") == 1
    assert git(robot, "status", "--porcelain", "--untracked-files=all") == ""


# -- the store and its git backup ----------------------------------------------------------------

def test_init_creates_store_and_is_idempotent(tmp_path):
    store_dir = cs.init_store(tmp_path / "Shared")
    for sub in cs.STORE_DIRS:
        assert (store_dir / sub).is_dir()
    assert (store_dir / ".gitattributes").read_bytes() == b"* -text\n"
    assert git(store_dir, "config", "core.autocrlf").strip() == "false"
    assert git(store_dir, "config", "core.filemode").strip() == "false"
    assert git(store_dir, "log", "--format=%s").splitlines() == ["claude-sync init"]
    cs.init_store(store_dir)
    assert git(store_dir, "log", "--format=%s").splitlines() == ["claude-sync init"]
    with pytest.raises(OSError, match="doesn't exist"):
        cs.init_store(tmp_path / "no-drive" / "Shared")
    assert not (tmp_path / "no-drive").exists()


def test_commit_and_quiet_push_failure(a, shared, tmp_path, capsys):
    remote = tmp_path / "backup.git"  # not there yet: every push fails
    git(shared, "remote", "add", "origin", str(remote))
    a.memory(a.home, {"a.md": "mine"})
    code = cs.main(["--shared", str(shared), "--claude-dir", str(a.claude), "--home", str(a.home),
                    "--projects-root", str(a.root)])
    assert code == 0
    assert "push failed" in capsys.readouterr().out
    summary = json.loads(((tmp_path / "state") / cs.SUMMARY_FILE).read_text("utf-8"))  # conftest's state dir
    assert summary["git"]["committed"] and summary["git"]["pushed"] is False and summary["git"]["error"]
    assert git(shared, "log", "-1", "--format=%s").startswith(f"claude-sync {OS} ")
    assert "push failed" in ((tmp_path / "state") / cs.LOG_FILE).read_text("utf-8")
    assert git(shared, "status", "--porcelain") == ""
    # The remote appears: the next sync pushes, even with nothing new to commit.
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    s = cs.run(shared, a.claude, tmp_path / "state", home=a.home, projects_root=a.root)
    assert s["git"]["committed"] is None and s["git"]["pushed"] is True
    assert git(remote, "log", "-1", "--format=%s", "main").startswith("claude-sync ")
    s = cs.run(shared, a.claude, tmp_path / "state", home=a.home, projects_root=a.root)
    assert s["git"]["pushed"] is None  # nothing new, nothing pending: no network call


def test_store_without_git_is_still_synced(tmp_path):
    shared = tmp_path / "plain"
    for sub in cs.STORE_DIRS:
        (shared / sub).mkdir(parents=True)
    w = World(tmp_path / "os", shared)
    w.memory(w.home, {"a.md": "mine"})
    s = w.sync()
    assert s["git"]["repo"] is False and (shared / "memory" / "home" / "a.md").exists()


# -- defaults, CLI, stdlib only ------------------------------------------------------------------

def test_defaults_point_at_temp_folders_in_tests(tmp_path):
    assert cs.default_shared_dir() == tmp_path / "claude-shared"  # conftest
    assert cs.default_claude_dir() == tmp_path / "claude-config"  # CLAUDE_CONFIG_DIR, as Claude Code reads it


@posix_only
def test_default_folders_on_linux(monkeypatch, _temp_home):
    monkeypatch.delenv("BRAINHUB_SHARED_DIR")
    assert cs.default_shared_dir() == Path("/mnt/data/ClaudeShared")
    assert cs.default_projects_root() == _temp_home / "dev"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert cs.default_claude_dir() == _temp_home / ".claude"


@windows_only
def test_default_folders_on_windows(monkeypatch, _temp_home):
    monkeypatch.delenv("BRAINHUB_SHARED_DIR")
    assert cs.default_shared_dir() == Path("D:\\ClaudeShared")
    assert cs.default_projects_root() == Path("D:\\")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert cs.default_claude_dir() == _temp_home / ".claude"


def test_cli_runs_and_init(tmp_path):
    env = {**os.environ, "BRAINHUB_STATE_DIR": str(tmp_path / "cli-state")}
    shared = tmp_path / "cli-shared"
    base = [sys.executable, "-m", "brainhub", "claude-sync"]
    r = subprocess.run([*base, "--shared", str(shared), "--claude-dir", str(tmp_path / "c")], cwd=APP, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0 and "nothing changed" in r.stdout and not shared.exists()
    r = subprocess.run([*base, "init", "--shared", str(shared)], cwd=APP, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and (shared / "memory" / "home").is_dir(), r.stderr
    (tmp_path / "c").mkdir()
    r = subprocess.run([*base, "--shared", str(shared), "--claude-dir", str(tmp_path / "c"),
                        "--projects-root", str(tmp_path / "dev"), "--home", str(tmp_path / "h")],
                       cwd=APP, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("claude-sync: "), r.stderr
    r = subprocess.run([*base, "init"], cwd=APP, env=env, capture_output=True, text=True)
    assert r.returncode == 2  # --shared is required: never a default for init


def test_module_is_stdlib_only():
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import brainhub.claude_sync; "
            "mine = sorted(m for m in sys.modules if m.split('.')[0] == 'brainhub'); "
            "assert mine == ['brainhub', 'brainhub.claude_sync', 'brainhub.osdirs'], mine; "
            "assert 'aiohttp' not in sys.modules")
    r = subprocess.run([sys.executable, "-I", "-c", code, str(APP)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
