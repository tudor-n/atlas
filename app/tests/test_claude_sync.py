"""claude-sync (plan Task 11, ruling D2: option 2, autoMemoryDirectory instead of links).

Every test uses a temp claude dir, a temp shared store, a temp state dir, a temp home and a temp
projects root, passed explicitly; HOME/USERPROFILE point at a temp folder too (autouse fixture
below), and conftest points CLAUDE_CONFIG_DIR, BRAINHUB_STATE_DIR and BRAINHUB_SHARED_DIR at temp
folders. Nothing here reads or writes the real ~/.claude, ~/dev or /mnt/data. Windows-only cases
(junctions, drive-letter keys, default folders) are skipped on Linux and run in CI on Windows.
"""
import json
import os
import shutil
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
    """The store, alone on its "drive" (its parent folder), as D:\\ClaudeShared and
    /mnt/data/ClaudeShared are: a project folder beside it is on the shared drive."""
    (tmp_path / "drive").mkdir()
    return cs.init_store(tmp_path / "drive" / "ClaudeShared")


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
    # The home session's project settings: <home>/.claude, not the Claude config folder (which is
    # elsewhere here, as under CLAUDE_CONFIG_DIR).
    assert json.loads((a.home / ".claude" / "settings.local.json").read_text("utf-8")) == {
        "autoMemoryDirectory": str(shared / "memory" / "home")}
    assert not (a.claude / "settings.local.json").exists()
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


@pytest.mark.parametrize("os_name, theirs", [
    ("linux", "D:\\ClaudeShared\\memory\\projects\\Robot"),
    ("linux", "\\\\nas\\share\\Robot"),
    ("windows", "/mnt/data/ClaudeShared/memory/projects/Robot"),
])
def test_other_os_memory_path_left_alone(tmp_path, shared, os_name, theirs):
    """A settings.local.json whose autoMemoryDirectory has the other OS's path shape belongs to a
    folder both OSes start sessions in, and that OS's sessions use it: logged, never rewritten. The
    folder's notes still merge."""
    w = World(tmp_path / "w", shared, os_name)
    robot = w.project("Robot")
    w.memory(robot, {"arm.md": "servo"})
    (robot / ".claude").mkdir()
    f = robot / ".claude" / "settings.local.json"
    f.write_text(json.dumps({"autoMemoryDirectory": theirs, "x": 1}), "utf-8")
    before = f.read_bytes()
    s = w.sync()
    assert f.read_bytes() == before
    assert s["memory"]["pairs"] == ["Robot"] and s["memory"]["pointed"] == []
    assert (shared / "memory" / "projects" / "Robot" / "arm.md").read_text("utf-8") == "servo"
    assert "a path of the other OS" in (w.state / cs.LOG_FILE).read_text("utf-8")
    assert not s["problems"]


def test_live_copy_and_shared_drive_never_pointed(a, shared):
    """Design §5.1: the live copy is never a workspace, and sessions on both OSes would read the one
    settings.local.json of a folder on the shared drive. Such folders are never pointed (nor their
    git exclude touched); their old notes still merge."""
    live = a.project("BrainHub")  # a BrainHub root in the projects root, as D:\BrainHub is on Windows
    (live / "data").mkdir()
    (live / "data" / "root.json").write_text("{}", "utf-8")
    git(live, "init", "-q")
    exclude_before = (live / ".git" / "info" / "exclude").read_bytes()
    legacy = a.project("OldHub")  # a legacy root: brain/map.json, no marker yet
    (legacy / "brain").mkdir()
    (legacy / "brain" / "map.json").write_text("{}", "utf-8")
    on_drive = shared.parent / "Robot"  # beside the store, outside this OS's projects root
    on_drive.mkdir()
    (shared / cs.PAIRS_FILE).write_text(json.dumps({"Robot": {a.os: str(on_drive)}}), "utf-8")
    folders = {"BrainHub": live, "OldHub": legacy, "Robot": on_drive}
    for name, folder in folders.items():
        a.memory(folder, {f"{name}.md": f"note of {name}"})
    s = a.sync()
    assert s["memory"]["pairs"] == ["BrainHub", "OldHub", "Robot"] and s["memory"]["pointed"] == []
    for name, folder in folders.items():
        assert not (folder / ".claude").exists()
        assert (shared / "memory" / "projects" / name / f"{name}.md").read_text("utf-8") == f"note of {name}"
    assert (live / ".git" / "info" / "exclude").read_bytes() == exclude_before
    log = (a.state / cs.LOG_FILE).read_text("utf-8")
    assert "holds a BrainHub root (data/root.json)" in log and "holds a BrainHub root (brain/map.json)" in log
    assert f"is on the shared drive ({shared.parent})" in log
    assert not s["problems"]


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


def test_pairs_json_folder_never_flips(a, shared, tmp_path):
    """pairs.json files ~/dev/B under A, and the store also has a B folder (from before the entry,
    or from the other OS's own B). ~/dev/B is pointed at A only, at every sync: never at A and
    then at B, which rewrote (and backed up) its settings.local.json twice per login."""
    b_dir = a.project("B")
    projects = shared / "memory" / "projects"
    for name in ("A", "B"):
        (projects / name).mkdir()
        (projects / name / f"{name.lower()}.md").write_text(f"note {name}", "utf-8")
    c_dir, far_c = a.project("C"), tmp_path / "os-a" / "far" / "Cee"  # pairs.json names another folder for C
    far_c.mkdir(parents=True)
    (shared / cs.PAIRS_FILE).write_text(json.dumps({"A": {a.os: str(b_dir)}, "C": {a.os: str(far_c)}}), "utf-8")
    a.memory(b_dir, {"local.md": "from here"})
    a.memory(c_dir, {"c.md": "the root folder named C"})
    a.memory(far_c, {"c.md": "the real C"})
    for _ in range(3):
        s = a.sync()
        assert s["memory"]["pairs"] == ["A", "C"]
        assert a.local_settings(b_dir) == {"autoMemoryDirectory": str(projects / "A")}
        assert a.local_settings(far_c) == {"autoMemoryDirectory": str(projects / "C")}
    assert s["memory"]["pointed"] == []
    assert not (c_dir / ".claude").exists()
    assert (projects / "A" / "local.md").read_text("utf-8") == "from here" and not (projects / "B" / "local.md").exists()
    assert (projects / "C" / "c.md").read_text("utf-8") == "the real C" and len(list((projects / "C").iterdir())) == 1
    assert not list((a.state / cs.BACKUPS / "settings").glob("*.settings.local.json-*"))  # never rewritten


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


def test_non_utf8_index_merged_once(a, shared):
    """A MEMORY.md saved in another encoding still merges, its bytes kept as they are, and its
    hashes are recorded: no new full backup at every login."""
    local = a.memory(a.home, {"me.md": "owner"})
    raw = "- [Me](me.md) — caf".encode() + b"\xe9 (latin-1)\n"
    (local / cs.INDEX).write_bytes(raw)
    s = a.sync()
    assert not s["problems"]
    index = shared / "memory" / "home" / cs.INDEX
    assert index.read_bytes() == cs.HEADER.encode() + b"\n" + raw
    a.sync()
    assert len([p for p in (a.state / cs.BACKUPS).iterdir() if p.name != "settings"]) == 1
    (local / cs.INDEX).write_bytes(raw + b"- [New](new.md) \xfc\n")
    s = a.sync()
    assert not s["problems"]
    assert index.read_bytes() == cs.HEADER.encode() + b"\n" + raw + b"- [New](new.md) \xfc\n"


def test_deleted_index_lines_stay_deleted(a, shared):
    """Only lines new since the last merged local index are added: a line the owner deleted from
    the store's MEMORY.md isn't brought back when the local index changes later."""
    local = a.memory(a.home, {cs.INDEX: "- [A](a.md) — a\n- [B](b.md) — b\n", "a.md": "a", "b.md": "b"})
    a.sync()
    index = shared / "memory" / "home" / cs.INDEX
    assert index.read_text("utf-8").splitlines() == [cs.HEADER, "- [A](a.md) — a", "- [B](b.md) — b"]
    index.write_text(f"{cs.HEADER}\n- [A](a.md) — a\n", "utf-8")  # the owner drops B in the store
    (local / cs.INDEX).write_text("- [A](a.md) — a\n- [B](b.md) — b\n- [C](c.md) — c\n", "utf-8")
    a.sync()
    assert index.read_text("utf-8").splitlines() == [cs.HEADER, "- [A](a.md) — a", "- [C](c.md) — c"]
    index.unlink()  # even with the whole store index gone, only the new line arrives
    (local / cs.INDEX).write_text("- [A](a.md) — a\n- [B](b.md) — b\n- [C](c.md) — c\n- [D](d.md) — d\n", "utf-8")
    a.sync()
    assert index.read_text("utf-8").splitlines() == [cs.HEADER, "- [D](d.md) — d"]


def test_damaged_state_file_merges_only_new_content(a, shared):
    """A claude-sync-last.json that can't be read is reported and backed up, and counts as unknown:
    only notes whose content the store has never held are merged. Stale local copies of notes since
    edited or removed in the store don't come back, as <stem>.<os>.md or at all."""
    local = a.memory(a.home, {cs.INDEX: "- [A](a.md) — a\n", "a.md": "v1", "b.md": "removed later"})
    a.sync()
    home = shared / "memory" / "home"
    (home / "a.md").write_text("v2, edited in the store", "utf-8")
    (home / "b.md").unlink()
    (home / "same.md").write_text("one text", "utf-8")
    a.sync()  # commits the store's changes
    (a.state / cs.LAST_FILE).write_text("{broken", "utf-8")
    (local / "new.md").write_text("written here meanwhile", "utf-8")
    (local / "copy.md").write_text("one text", "utf-8")  # already in the store, as same.md
    s = a.sync()
    assert sorted(p.name for p in home.iterdir()) == [".gitkeep", cs.INDEX, "a.md", "new.md", "same.md"]
    assert (home / "a.md").read_text("utf-8") == "v2, edited in the store"
    (merged,) = s["memory"]["merged"]
    assert merged["copied"] == ["new.md"] and merged["kept_both"] == []
    assert any("backed up" in p and "claude-sync-last.json" in p for p in s["problems"]["memory"])
    (kept,) = (a.state / cs.BACKUPS).glob(f"{cs.LAST_FILE}-*")
    assert kept.read_text("utf-8") == "{broken"
    backups = len([p for p in (a.state / cs.BACKUPS).iterdir() if p.is_dir() and p.name != "settings"])
    s = a.sync()  # back to normal: nothing new, no new backup, no problem
    assert s["memory"]["merged"] == [] and not s["problems"]
    assert len([p for p in (a.state / cs.BACKUPS).iterdir() if p.is_dir() and p.name != "settings"]) == backups


def test_note_copy_is_atomic(a, shared, monkeypatch):
    """A note is copied into a temp file and renamed into place: a copy that fails halfway leaves
    neither half a note nor the temp file in the store."""
    a.memory(a.home, {"a.md": "x" * 100})
    real = shutil.copyfileobj

    def half(src, dst, *args, **kwargs):
        dst.write(src.read(10))
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(shutil, "copyfileobj", half)
    s = a.sync()
    home = shared / "memory" / "home"
    assert any("No space left" in p for p in s["problems"]["memory"])
    assert sorted(p.name for p in home.iterdir()) == [".gitkeep"]
    monkeypatch.setattr(shutil, "copyfileobj", real)
    a.sync()
    assert (home / "a.md").read_text("utf-8") == "x" * 100


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


@pytest.mark.parametrize("b_value", [None, "tool"])
def test_removed_path_key_never_shared(a, b, shared, b_value):
    """A key whose value held a path here was never shared from here, so removing it here isn't
    shared either: the store gets no entry, and the other OS keeps its own value."""
    mine_b = {"model": "opus", **({"someTool": b_value} if b_value else {})}
    _baseline(a, b, {"model": "opus", "someTool": "/home/tudor/bin/tool"}, mine_b)
    a.write_settings({"model": "opus"}, T0 + 100)  # someTool removed on A
    a.sync()
    entry = store(shared)["settings"].get("someTool")
    assert entry is None if b_value is None else entry["deleted"] is False
    b.sync()
    assert b.settings() == mine_b


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
    for value in ("opus", "https://github.com/a/b", "anthropics/claude-plugins", "a/b", "git@github.com:a/b.git",
                  {"x@m": True}, ["Bash(git:*)"], 3, None, True, "10/22", "10:22", "100%", "file: notes",
                  {"$schema": "https://json.schemastore.org/claude-code-settings.json"}, "ws://localhost:8090/x"):
        assert not cs.holds_abs_path(value), value


@pytest.mark.parametrize("value", [
    "file:///home/tudor/notes.md", "FILE://server/share/x", "see file:/C:/x",  # file URLs
    "/usr/bin:/bin", "node_modules/.bin:/usr/local/bin", ".:/opt/tools",  # PATH-style lists
    "C:\\x;D:\\y", ".;D:\\tools", "%PATH%;C:\\bin",
    "$HOME/bin/tool", "${HOME}/x", "run $XDG_DATA_HOME/brainhub", "~/notes", "~\\notes", "cd ~/dev && x",
    "%USERPROFILE%\\x", "%LOCALAPPDATA%/BrainHub", "%ProgramFiles(x86)%\\Tool",
    {"a": [{"b": {"c": ["fine", "~/deep"]}}]}, [[["opus", {"k": ["file:///x"]}]]],  # at any depth
    {"outer": {"/etc/key": 1}},
])
def test_abs_path_detection_catches_machine_bound_forms(value):
    assert cs.holds_abs_path(value), value


def test_other_os_path_shapes():
    for value in ("D:\\ClaudeShared\\memory\\home", "c:/x", "\\\\nas\\share"):
        assert cs.other_os_path(value, "linux") and not cs.other_os_path(value, "windows"), value
    for value in ("/mnt/data/ClaudeShared/memory/home", "/home/tudor"):
        assert cs.other_os_path(value, "windows") and not cs.other_os_path(value, "linux"), value
    for value in ("~/memory", "memory/home", None, 3):
        assert not cs.other_os_path(value, "linux") and not cs.other_os_path(value, "windows"), value


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


def test_project_settings_local_excluded_in_a_worktree(a, shared):
    """A worktree's .git is a file: the exclude file is the one git names (the main repo's
    info/exclude, which every worktree reads). A folder whose .git isn't a repo is skipped quietly."""
    main = a.project("Robot")
    git(main, "init", "-q")
    (main / "x.txt").write_text("x", "utf-8")
    git(main, "add", "-A")
    git(main, "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-q", "-m", "x")
    wt = a.root / "Robot-wt"
    git(main, "worktree", "add", "-q", "-b", "wt", str(wt))
    assert (wt / ".git").is_file()
    a.memory(wt, {"arm.md": "servo"})
    bogus = a.project("Bogus")
    (bogus / ".git").write_text("gitdir: /nowhere/at/all\n", "utf-8")
    a.memory(bogus, {"b.md": "b"})
    s = a.sync()
    a.sync()
    assert a.local_settings(wt)["autoMemoryDirectory"] == str(shared / "memory" / "projects" / "Robot-wt")
    lines = (main / ".git" / "info" / "exclude").read_text("utf-8").splitlines()
    assert lines.count("/.claude/settings.local.json") == 1
    assert git(wt, "status", "--porcelain", "--untracked-files=all") == ""
    assert "Bogus" in s["memory"]["pointed"] and not s["problems"]
    assert (bogus / ".git").read_text("utf-8") == "gitdir: /nowhere/at/all\n"


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


def test_push_never_waits_on_a_prompt(a, shared, tmp_path, monkeypatch):
    """The push runs in a session of its own on POSIX (no terminal to prompt on), with every
    no-prompt switch; other git calls don't detach. An ssh command the owner set is kept."""
    calls = []
    real = subprocess.Popen

    def spy(cmd, *args, **kwargs):
        calls.append((cmd, kwargs))
        return real(cmd, *args, **kwargs)
    git(shared, "remote", "add", "origin", str(tmp_path / "missing.git"))
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.setattr(subprocess, "Popen", spy)
    a.memory(a.home, {"a.md": "mine"})
    s = a.sync()
    assert s["git"]["pushed"] is False
    (push_cmd, push_kw), = [(c, k) for c, k in calls if "push" in c]
    assert push_kw["start_new_session"] is (not WIN)
    assert "credential.interactive=never" in push_cmd
    env = push_kw["env"]
    assert (env["GIT_TERMINAL_PROMPT"], env["GCM_INTERACTIVE"]) == ("0", "never")
    assert env["GIT_SSH_COMMAND"] == "ssh -oBatchMode=yes"
    others = [k for c, k in calls if c[0] == "git" and "push" not in c]
    assert others and not any(k["start_new_session"] for k in others)
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i ~/.ssh/backup")
    assert cs._child_env()["GIT_SSH_COMMAND"] == "ssh -i ~/.ssh/backup"
    monkeypatch.delenv("GIT_SSH_COMMAND")
    monkeypatch.setenv("GIT_SSH", "plink")
    assert "GIT_SSH_COMMAND" not in cs._child_env()


@posix_only
def test_store_git_sees_case_variant_notes(a, shared):
    """Ruling D7 for the store: a .git/config made on Windows says core.ignorecase=true, and Linux
    git must still see (and commit) a note.md next to Note.md."""
    git(shared, "config", "core.ignorecase", "true")
    home = shared / "memory" / "home"
    (home / "Note.md").write_text("upper", "utf-8")
    a.sync()
    (home / "note.md").write_text("lower", "utf-8")
    s = a.sync()
    assert s["git"]["committed"]
    assert {"memory/home/Note.md", "memory/home/note.md"} <= set(git(shared, "ls-files").splitlines())
    assert git(shared, "-c", "core.ignorecase=false", "status", "--porcelain") == ""


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
