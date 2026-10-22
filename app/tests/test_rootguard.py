"""Root guard (dual-boot design §5.2, §6.5): a root is never created, an unmounted drive is
never mistaken for an empty folder, two hubs never share a root, newer data is refused."""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import brainhub.__main__ as main_mod
from brainhub import osdirs, rootguard
from brainhub.brainmap import default_map, save_map
from brainhub.config import Paths
from brainhub.server import RootUnavailable, create_app
from brainhub.store import SCHEMA_VERSION, SchemaTooNew, Store
from tests.test_server import MAP, make_runner

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process and mount rules")
win_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows process rules")
not_superuser = pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                                   reason="chmod doesn't stop root (and is POSIX)")


def _app(paths):
    return create_app(paths, runner=make_runner(), start_workers=False)


def _marker(paths):
    return json.loads((paths.data / rootguard.MARKER_NAME).read_text(encoding="utf-8"))


def _fstab_path(p):
    return p.as_posix().replace(" ", "\\040")


def _listing(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


# -- missing and the mount guard ---------------------------------------------
def test_missing_root_is_never_created(tmp_path):
    root = tmp_path / "drive" / "BrainHub"
    paths = Paths(root)
    assert rootguard.check_root(paths).state == "missing"
    with pytest.raises(RootUnavailable) as e:
        _app(paths)
    assert e.value.status.state == "missing" and "never creates" in e.value.status.message
    assert not (tmp_path / "drive").exists()


def test_unmounted_fstab_mount_point_is_missing(tmp_path):
    mnt = tmp_path / "mnt"
    root = mnt / "BrainHub"
    root.mkdir(parents=True)  # the folder exists and is empty, as an unmounted mount point is
    fstab = tmp_path / "fstab"
    fstab.write_text(f"UUID=abc / btrfs defaults 0 0\n/dev/sdb1 {_fstab_path(mnt)} ntfs3 nofail 0 0\n",
                     encoding="utf-8")
    seen = []

    def unmounted(p):
        seen.append(p)
        return False

    status = rootguard.check_root(Paths(root), fstab=fstab, ismount=unmounted)
    assert status == rootguard.RootStatus("missing", rootguard.UNMOUNTED)
    assert seen == [mnt]  # `/` guards nothing and is never asked about
    assert _listing(root) == []  # nothing written
    assert rootguard.check_root(Paths(root), fstab=fstab, ismount=lambda p: True).state == "fresh"
    assert rootguard.check_root(Paths(mnt), fstab=fstab, ismount=unmounted).state == "missing"


def test_fstab_parsing_is_robust(tmp_path):
    fstab = tmp_path / "fstab"
    fstab.write_text("\n".join([
        "# /etc/fstab: static file system information.",
        "   # an indented comment",
        "",
        "UUID=abc  /  btrfs  defaults  0 0",
        "UUID=def  none  swap  sw  0 0",
        "/swap/swapfile  swap  swap  defaults  0 0",
        "/swap/other  /swapspace  swap  defaults  0 0",
        f"/dev/sdb1  {_fstab_path(tmp_path / 'mnt')}  ntfs3  defaults,nofail  0 2",
        f"/dev/sdc1\t{_fstab_path(tmp_path / 'my drive')}\tntfs3\tdefaults 0 0",
        f"tmpfs {_fstab_path(tmp_path / 'mnt' / 'deeper')} tmpfs defaults 0 0",
        "lonely-field",
    ]) + "\n", encoding="utf-8")
    assert rootguard.fstab_mount_points(fstab) == [
        tmp_path / "mnt", tmp_path / "my drive", tmp_path / "mnt" / "deeper"]
    assert rootguard.fstab_mount_points(tmp_path / "no-such-fstab") == []
    # The longest mount point holding the root decides.
    deep = tmp_path / "mnt" / "deeper" / "BrainHub"
    deep.mkdir(parents=True)
    only_outer = lambda p: p == tmp_path / "mnt"
    only_inner = lambda p: p == tmp_path / "mnt" / "deeper"
    assert rootguard.check_root(Paths(deep), fstab=fstab, ismount=only_outer).state == "missing"
    assert rootguard.check_root(Paths(deep), fstab=fstab, ismount=only_inner).state == "fresh"
    spaced = tmp_path / "my drive" / "BrainHub"
    spaced.mkdir(parents=True)
    assert rootguard.check_root(Paths(spaced), fstab=fstab, ismount=lambda p: False).state == "missing"
    elsewhere = tmp_path / "elsewhere"  # inside `/` only: no guard
    elsewhere.mkdir()
    assert rootguard.check_root(Paths(elsewhere), fstab=fstab, ismount=lambda p: False).state == "fresh"


# -- fresh, setup and the marker ---------------------------------------------
async def test_fresh_root_allows_setup_and_writes_marker(aiohttp_client, tmp_path):
    root = tmp_path / "BrainHub"
    (root / "app").mkdir(parents=True)  # the live copy's code sits next to the data
    (root / "app" / "README.md").write_text("code", encoding="utf-8")
    (root / "brain").mkdir()
    paths = Paths(root)
    assert rootguard.check_root(paths).state == "fresh"
    app = _app(paths)
    assert app["root_status"].state == "fresh"
    c = await aiohttp_client(app)
    assert (await c.post("/api/setup", json={"map": MAP, "backup_remote": None})).status == 200
    marker = _marker(paths)
    assert re.fullmatch(r"[0-9a-f]{32}", marker["id"]) and marker["brain_format"] == 1
    assert marker["tz"] == rootguard.utc_offsets() and set(marker["tz"]) == {"jan", "jul"}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", marker["created"])
    raw = (paths.data / rootguard.MARKER_NAME).read_bytes()
    assert b"\r\n" not in raw and str(root).encode() not in raw  # LF, no absolute paths
    assert rootguard.check_root(paths).state == "ok"
    state = await (await c.get("/api/state")).json()
    assert state["setup_done"] is True and state["warnings"] == []
    assert (await c.post("/api/setup", json={"map": MAP})).status == 409
    assert _marker(paths) == marker  # never replaced


async def test_setup_refused_on_non_fresh_root(aiohttp_client, paths, tmp_path):
    c = await aiohttp_client(_app(paths))
    (paths.brain / "notes.md").write_text("someone else's notes", encoding="utf-8")  # foreign now
    r = await c.post("/api/setup", json={"map": MAP})
    assert r.status == 409 and "doesn't look like a BrainHub brain" in (await r.json())["error"]
    assert not (paths.brain / "map.json").exists() and not (paths.data / rootguard.MARKER_NAME).exists()
    # A marked root without map.json is a brain that went missing, never a fresh folder: no app
    # (Task 6 serves the banner and holds captures), no setup, and the marker is never replaced.
    other = Paths(tmp_path / "other")
    other.brain.mkdir(parents=True)
    rootguard.create_marker(other)
    status = rootguard.check_root(other)
    assert status.state == "foreign" and "map.json is missing" in status.message
    with pytest.raises(RootUnavailable):
        _app(other)
    assert not (other.brain / "map.json").exists()
    with pytest.raises(FileExistsError):
        rootguard.create_marker(other)


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions, not as root")
def test_unreadable_root_is_missing_never_fresh(paths):
    save_map(paths.brain, default_map())
    assert rootguard.check_root(paths).state == "ok"
    paths.root.chmod(0o600)  # no search permission: stat of anything inside fails
    try:
        status = rootguard.check_root(paths)
    finally:
        paths.root.chmod(0o755)
    assert status.state == "missing" and "can't be read" in status.message


def test_foreign_root(paths):
    (paths.brain / "notes.md").write_text("not a BrainHub brain", encoding="utf-8")
    status = rootguard.check_root(paths)
    assert status.state == "foreign" and str(paths.brain) in status.message
    with pytest.raises(RootUnavailable) as e:
        _app(paths)
    assert e.value.status.state == "foreign"
    assert _listing(paths.data) == []  # no marker, no hub.db


def test_unreadable_marker_is_foreign_and_kept(paths):
    save_map(paths.brain, default_map())
    (paths.data / rootguard.MARKER_NAME).write_text("{broken", encoding="utf-8")
    status = rootguard.check_root(paths)
    assert status.state == "foreign" and "unreadable" in status.message
    assert (paths.data / rootguard.MARKER_NAME).read_text(encoding="utf-8") == "{broken"


async def test_legacy_brain_gets_marker(aiohttp_client, paths):
    save_map(paths.brain, default_map())  # a brain made before markers
    assert rootguard.check_root(paths).state == "ok"
    first = _marker(paths)
    assert first["brain_format"] == rootguard.BRAIN_FORMAT and re.fullmatch(r"[0-9a-f]{32}", first["id"])
    assert rootguard.check_root(paths).state == "ok" and _marker(paths) == first  # stable identity
    c = await aiohttp_client(_app(paths))
    assert (await (await c.get("/api/state")).json())["setup_done"] is True


def test_newer_brain_format_refused(paths):
    save_map(paths.brain, default_map())
    marker = {"id": "f" * 32, "brain_format": rootguard.BRAIN_FORMAT + 1, "created": "2027-01-01T00:00:00"}
    (paths.data / rootguard.MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    status = rootguard.check_root(paths)
    assert status.state == "newer" and "newer BrainHub" in status.message
    with pytest.raises(RootUnavailable) as e:
        _app(paths)
    assert e.value.status.state == "newer"
    assert _marker(paths) == marker and not paths.db.exists()


# -- hub.db schema version ---------------------------------------------------
def _db_at_version(db, version):
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE future(x)")
    con.execute(f"PRAGMA user_version={version}")
    con.commit()
    con.close()


def test_schema_too_new_refused(paths):
    save_map(paths.brain, default_map())
    _db_at_version(paths.db, SCHEMA_VERSION + 5)
    before = paths.db.read_bytes()
    s = Store(paths.db)
    with pytest.raises(SchemaTooNew):
        s.init_schema()
    s.close()
    with pytest.raises(RootUnavailable) as e:
        _app(paths)
    assert e.value.status.state == "newer" and f"schema {SCHEMA_VERSION + 5}" in e.value.status.message
    assert not list(paths.data.glob("hub.db.damaged-*"))  # never moved aside as damaged
    assert paths.db.read_bytes() == before


def test_schema_versioned_from_zero(paths):
    from brainhub import store as store_mod
    s = Store(paths.data / "new.db")
    s.init_schema()
    assert s.schema_version() == SCHEMA_VERSION
    s.init_schema()  # the server runs it twice: idempotent
    assert s.schema_version() == SCHEMA_VERSION
    s.close()
    # A hub.db made before versioning: the tables exist, user_version is 0.
    con = sqlite3.connect(paths.db)
    con.executescript(store_mod._SCHEMA)
    con.execute("INSERT INTO entries VALUES('e-20261020-000000-abcd','2026-10-20T00:00:00','pc','kept')")
    con.commit()
    con.close()
    s = Store(paths.db)
    s.init_schema()
    assert s.schema_version() == SCHEMA_VERSION
    assert s.get_entry("e-20261020-000000-abcd").text == "kept"
    s.close()


# -- read-only ---------------------------------------------------------------
@not_superuser
def test_readonly_root_detected(paths, tmp_path):
    save_map(paths.brain, default_map())
    assert rootguard.check_root(paths).state == "ok"  # marker adopted while writable
    legacy = Paths(tmp_path / "legacy")
    save_map(legacy.brain, default_map())
    fresh = Paths(tmp_path / "fresh")
    fresh.root.mkdir()
    roots = [paths.root, legacy.root, fresh.root]
    try:
        for r in roots:
            r.chmod(0o555)
        assert rootguard.check_root(paths).state == "readonly"
        assert rootguard.check_root(legacy).state == "readonly"
        assert not (legacy.data / rootguard.MARKER_NAME).exists()  # nothing written to a read-only root
        assert rootguard.check_root(fresh).state == "readonly"  # setup couldn't create a brain there
    finally:
        for r in roots:
            r.chmod(0o755)


@posix_only
def test_readonly_mount_detected(paths, monkeypatch):
    save_map(paths.brain, default_map())
    rootguard.check_root(paths)
    monkeypatch.setattr(rootguard.os, "statvfs", lambda p: SimpleNamespace(f_flag=os.ST_RDONLY))
    assert rootguard.check_root(paths).state == "readonly"


# -- time zone ---------------------------------------------------------------
async def test_tz_mismatch_warning(aiohttp_client, paths, monkeypatch):
    save_map(paths.brain, default_map())
    rootguard.check_root(paths)
    marker = _marker(paths)
    assert rootguard.tz_mismatch(marker) is None
    assert rootguard.tz_mismatch({"id": "x", "brain_format": 1}) is None  # nothing to compare with
    here = rootguard.utc_offsets()
    monkeypatch.setattr(rootguard, "utc_offsets", lambda: {"jan": here["jan"] + 3600, "jul": here["jul"]})
    assert rootguard.tz_mismatch(marker) == rootguard.TZ_WARNING
    c = await aiohttp_client(_app(paths))
    assert (await (await c.get("/api/state")).json())["warnings"] == [rootguard.TZ_WARNING]


# -- hub lock ----------------------------------------------------------------
def _write_lock(paths, **fields):
    lock = {"os": osdirs.os_name(), "boot": osdirs.boot_id(), "pid": os.getpid(),
            "started": "2026-10-22T09:00:00", **fields}
    (paths.data / rootguard.LOCK_NAME).write_text(json.dumps(lock), encoding="utf-8")
    return lock


def _lock(paths):
    return json.loads((paths.data / rootguard.LOCK_NAME).read_text(encoding="utf-8"))


def _ours(lock):
    return (lock["os"], lock["boot"], lock["pid"]) == (osdirs.os_name(), osdirs.boot_id(), os.getpid())


@pytest.fixture
def live_child():
    """A running process that isn't this one: a lock naming it belongs to another live hub."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield child
    finally:
        child.kill()
        child.wait()


@pytest.fixture
def finished():
    """A child that has exited. The Popen object lives as long as the test, so on Windows its
    handle keeps the pid from being reused (and pid_alive reads its exit code)."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    yield child


def test_lock_from_other_os_is_taken_over(paths):
    _write_lock(paths, os="windows" if osdirs.os_name() == "linux" else "linux")
    assert rootguard.acquire_lock(paths) is None
    lock = _lock(paths)
    assert _ours(lock) and set(lock) == {"os", "boot", "pid", "pid_start", "started"}
    raw = (paths.data / rootguard.LOCK_NAME).read_bytes()
    assert b"\r\n" not in raw and str(paths.root).encode() not in raw
    assert [p.name for p in paths.data.iterdir()] == [rootguard.LOCK_NAME]  # no tmp left behind


def test_lock_from_other_boot_is_taken_over(paths):
    _write_lock(paths, boot="an-earlier-boot")
    assert rootguard.acquire_lock(paths) is None and _ours(_lock(paths))


def test_lock_dead_pid_is_taken_over(paths, finished):
    assert not rootguard.pid_alive(finished.pid)
    _write_lock(paths, pid=finished.pid)
    assert rootguard.acquire_lock(paths) is None and _ours(_lock(paths))


def test_lock_live_same_boot_refuses(paths, live_child):
    # this OS, this boot, a live pid with the start time it really has
    _write_lock(paths, pid=live_child.pid, pid_start=rootguard.process_start(live_child.pid))
    before = (paths.data / rootguard.LOCK_NAME).read_bytes()
    refusal = rootguard.acquire_lock(paths)
    assert refusal and f"pid {live_child.pid}" in refusal and "already running" in refusal
    assert rootguard.acquire_lock(paths, write=False) == refusal
    assert (paths.data / rootguard.LOCK_NAME).read_bytes() == before


def test_lock_with_our_own_pid_is_stale(paths):
    """The hub that wrote it was killed and this process got its pid: we are that pid now."""
    _write_lock(paths)
    assert rootguard.acquire_lock(paths) is None and _ours(_lock(paths))


def test_lock_with_reused_pid_is_taken_over(paths, live_child):
    """A live pid whose start time differs from the lock's was reused after the hub died."""
    if rootguard.process_start(live_child.pid) is None:
        pytest.skip("process start times can't be read here")
    _write_lock(paths, pid=live_child.pid, pid_start="proc-1" if sys.platform != "win32" else "ft-1")
    assert rootguard.acquire_lock(paths) is None and _ours(_lock(paths))


def test_unreadable_lock_is_taken_over(paths):
    (paths.data / rootguard.LOCK_NAME).write_text("{half", encoding="utf-8")
    assert rootguard.acquire_lock(paths) is None and _ours(_lock(paths))


def test_check_only_lock_writes_nothing(paths):
    assert rootguard.acquire_lock(paths, write=False) is None
    assert not (paths.data / rootguard.LOCK_NAME).exists()


def test_lock_never_creates_the_root(tmp_path):
    fresh = Paths(tmp_path / "fresh")
    fresh.root.mkdir()
    assert rootguard.acquire_lock(fresh) is None and (fresh.data / rootguard.LOCK_NAME).exists()
    missing = Paths(tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        rootguard.acquire_lock(missing)
    assert not missing.root.exists()


def test_release_only_our_lock(paths, finished):
    rootguard.release_lock(paths)  # no lock: nothing to do
    assert rootguard.acquire_lock(paths) is None
    rootguard.release_lock(paths)
    assert not (paths.data / rootguard.LOCK_NAME).exists()
    for theirs in ({"pid": finished.pid}, {"boot": "an-earlier-boot"},
                   {"os": "windows" if osdirs.os_name() == "linux" else "linux"}):
        _write_lock(paths, **theirs)
        rootguard.release_lock(paths)
        assert (paths.data / rootguard.LOCK_NAME).exists(), theirs


# -- pid liveness (each OS's branch runs on its own OS) -----------------------
def test_pid_alive_live_and_finished(finished):
    assert rootguard.pid_alive(os.getpid())
    assert not rootguard.pid_alive(finished.pid)
    assert not rootguard.pid_alive(2**31 - 4)  # no such process on either OS
    for bad in (0, -1, True, "12", None, 1.5):
        assert not rootguard.pid_alive(bad), bad
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert rootguard.pid_alive(child.pid)
        assert child.poll() is None  # checking never stops the process
    finally:
        child.kill()
        child.wait()


@posix_only
def test_pid_alive_posix_eperm_means_alive(monkeypatch):
    def kill(pid, sig):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(rootguard.os, "kill", kill)
    assert rootguard.pid_alive(12345)


@win_only
def test_pid_alive_windows_system_process():
    assert rootguard.pid_alive(4)  # System: exists, may refuse queries (access denied = alive)


# -- run_server ----------------------------------------------------------------
@pytest.fixture
def no_server(monkeypatch):
    monkeypatch.setattr(main_mod, "port_free", lambda host, port: True)

    def run_app(*a, **kw):
        raise AssertionError("the server must not start")
    monkeypatch.setattr(main_mod.web, "run_app", run_app)


def test_run_server_exits_on_missing_root(tmp_path, no_server):
    root = tmp_path / "gone" / "BrainHub"
    with pytest.raises(SystemExit) as e:
        main_mod.run_server(["--root", str(root), "--port", "8123"])
    assert "isn't there" in str(e.value.code) and str(e.value.code).startswith("BrainHub: not starting.")
    assert not (tmp_path / "gone").exists()


def test_run_server_refuses_while_another_hub_holds_the_lock(paths, no_server, live_child):
    _write_lock(paths, pid=live_child.pid, pid_start=rootguard.process_start(live_child.pid))
    with pytest.raises(SystemExit) as e:
        main_mod.run_server(["--root", str(paths.root), "--port", "8123"])
    assert "Another BrainHub" in str(e.value.code)
    assert not paths.db.exists()  # refused before any startup work
    assert _lock(paths)["started"] == "2026-10-22T09:00:00"  # the other hub's lock is untouched


def test_run_server_releases_lock_when_refusing_newer_schema(paths, no_server):
    save_map(paths.brain, default_map())
    _db_at_version(paths.db, SCHEMA_VERSION + 1)
    with pytest.raises(SystemExit) as e:
        main_mod.run_server(["--root", str(paths.root), "--port", "8123"])
    assert "newer BrainHub" in str(e.value.code)
    assert not (paths.data / rootguard.LOCK_NAME).exists()
