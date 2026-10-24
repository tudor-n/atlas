"""Root guard: what the data root is before the hub touches it (dual-boot design §5.2, §6.5).

The hub never creates a root by itself. At start it classifies the root as ok, fresh, missing,
readonly, foreign or newer; only setup, on a fresh root, creates a brain (and the marker
`data/root.json`, Plan decision P5). The hub lock `data/hub.lock` keeps two hubs off one root,
while a lock left by the other OS, an earlier boot or a dead process never blocks a start
(§6.5 item 5, P11). Both files live in shared space: LF line endings, no absolute paths.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import osdirs
from .config import Paths

BRAIN_FORMAT = 1
MARKER_NAME = "root.json"
LOCK_NAME = "hub.lock"
STATES = ("ok", "fresh", "missing", "readonly", "foreign", "newer")

UNMOUNTED = ("ATLAS's drive isn't available. Boot Windows once and shut it down normally; "
             "Windows checks and repairs the drive. Then come back.")
TZ_WARNING = ("This OS's time zone differs from the one this brain was created with; entry times "
              "would be off by the difference. Set the same time zone on both OSes.")

_OCTAL = re.compile(r"\\([0-7]{3})")  # fstab escapes a space in a path as \040


@dataclass(frozen=True)
class RootStatus:
    state: str  # one of STATES
    message: str  # what the banner says; empty when ok


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _write_json(path: Path, data: dict) -> None:
    """Atomic: a reader sees the old file or the new one, never half. The tmp name carries the
    pid, so two processes never write the same tmp file."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(data, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# -- mount guard -------------------------------------------------------------
def _unescape(field: str) -> str:
    return _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), field)


def fstab_mount_points(fstab: Path) -> list[Path]:
    """Mount points listed in an fstab, without `/`, swap and `none` entries. An unreadable or
    absent fstab (Windows has none) lists nothing, so the guard never fires there."""
    try:
        text = fstab.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0].startswith("#"):
            continue
        mp, fstype = _unescape(fields[1]), (fields[2] if len(fields) > 2 else "")
        if mp in ("none", "swap") or fstype == "swap":
            continue
        p = Path(os.path.normpath(mp))
        if p != p.parent:  # `/` is always mounted: it guards nothing
            out.append(p)
    return out


def _guarding_mount_point(root: Path, fstab: Path) -> Path | None:
    """The longest fstab mount point the root is inside (through a symlink too), or None."""
    candidates = {Path(os.path.abspath(root)), Path(os.path.realpath(root))}
    best = None
    for mp in fstab_mount_points(fstab):
        if any(c == mp or mp in c.parents for c in candidates):
            if best is None or len(mp.parts) > len(best.parts):
                best = mp
    return best


# -- marker ------------------------------------------------------------------
def _marker_path(paths: Paths) -> Path:
    return paths.data / MARKER_NAME


def _load_marker(path: Path) -> dict:
    """Parse the marker. Raises OSError or ValueError when it can't be used. A newer format is
    returned as is: only its brain_format is understood."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    fmt = data.get("brain_format")
    if type(fmt) is not int or fmt < 1:
        raise ValueError("brain_format must be a whole number from 1")
    if fmt <= BRAIN_FORMAT and not (isinstance(data.get("id"), str) and data["id"]):
        raise ValueError("id is missing")
    return data


def read_marker(paths: Paths) -> dict | None:
    """The root marker, or None when it is absent or unusable (check_root says why)."""
    try:
        return _load_marker(_marker_path(paths))
    except (OSError, ValueError):
        return None


def utc_offsets() -> dict:
    """This OS's UTC offset (seconds east) at noon on 15 January and 15 July of this year: the
    standard and the summer offset wherever the zone has one. Compared, not time.timezone and
    time.altzone: Windows' CPython always reports altzone = timezone - 3600, even for a zone
    without summer time."""
    year = time.localtime().tm_year
    def at(month: int) -> int:
        return time.localtime(time.mktime((year, month, 15, 12, 0, 0, 0, 0, -1))).tm_gmtoff
    return {"jan": at(1), "jul": at(7)}


def _write_marker(paths: Paths) -> dict:
    marker = {"id": secrets.token_hex(16), "brain_format": BRAIN_FORMAT, "created": _now(),
              "tz": utc_offsets()}
    path = _marker_path(paths)
    paths.data.mkdir(exist_ok=True)  # inside the existing root; never the root itself
    if path.exists():  # a marker is never replaced: its id is the root's identity
        raise FileExistsError(f"{path} already exists")
    _write_json(path, marker)
    return marker


def create_marker(paths: Paths) -> dict:
    """Mark a fresh root as a brain. Only setup calls this, after checking the root is fresh."""
    return _write_marker(paths)


def tz_mismatch(marker: dict) -> str | None:
    """A quiet warning when this OS's time zone offsets differ from the ones the brain was
    created with (the hub stores naive local times), else None."""
    tz = marker.get("tz")
    if not isinstance(tz, dict) or not all(type(tz.get(k)) is int for k in ("jan", "jul")):
        return None  # nothing to compare with
    here = utc_offsets()
    return None if (tz["jan"], tz["jul"]) == (here["jan"], here["jul"]) else TZ_WARNING


# -- root status -------------------------------------------------------------
def _writable(root: Path) -> bool:
    if not os.access(root, os.W_OK):
        return False
    if hasattr(os, "statvfs"):  # POSIX: a drive mounted read-only (NTFS left dirty by Windows)
        try:
            return not os.statvfs(root).f_flag & os.ST_RDONLY
        except OSError:
            return False
    return True  # Windows: os.access alone


def _exists(p: Path) -> bool:
    """Strict: only "no such file" means absent. Any other error (permissions, an I/O error on a
    damaged drive) raises, where Path.exists() would quietly say False and let a real brain pass
    for an empty folder."""
    try:
        os.stat(p)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def _is_dir(p: Path) -> bool:
    try:
        return stat.S_ISDIR(os.stat(p).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


def _empty_or_absent(folder: Path) -> bool:
    if not _exists(folder):
        return True
    return _is_dir(folder) and not any(folder.iterdir())  # errors raise: not provably empty


def _readonly(root: Path) -> RootStatus:
    return RootStatus("readonly", f"ATLAS's data folder {root} is read-only, so nothing can be "
                                  "saved there. If Windows left the drive unclean, boot Windows once "
                                  "and shut it down normally; Windows checks and repairs the drive.")


def check_root(paths: Paths, *, fstab: Path = Path("/etc/fstab"),
               ismount=os.path.ismount) -> RootStatus:
    """Classify the root before anything touches it. It never creates or writes anything,
    except the marker of a brain made before markers existed (legacy adoption). "fresh" needs
    positive evidence: a root that can't be read is "missing", never fresh."""
    try:
        return _check_root(paths, fstab, ismount)
    except OSError as e:
        return RootStatus("missing", f"ATLAS's data folder {paths.root} can't be read ({e}). If it's "
                                     "on a drive Windows left unclean, boot Windows once and shut it "
                                     "down normally; Windows checks and repairs the drive.")


def _check_root(paths: Paths, fstab: Path, ismount) -> RootStatus:
    root = paths.root
    mp = _guarding_mount_point(root, fstab)
    if mp is not None and not ismount(mp):  # an empty mount point looks like an empty folder
        return RootStatus("missing", UNMOUNTED)
    if not _is_dir(root):
        return RootStatus("missing", f"ATLAS's data folder {root} isn't there. If it's on a drive "
                                     "that isn't available, make the drive available and start "
                                     "ATLAS again. ATLAS never creates this folder by itself: "
                                     "to start a new brain, create an empty folder and run setup.")
    writable = _writable(root)
    marker = _marker_path(paths)
    has_map = _exists(paths.brain / "map.json")
    if _exists(marker):
        try:
            data = _load_marker(marker)
        except ValueError as e:
            return RootStatus("foreign", f"ATLAS's marker {marker} is unreadable ({e}). ATLAS "
                                         "won't use this folder until the file is fixed or restored. "
                                         "If brain/map.json is intact, deleting the marker lets "
                                         "ATLAS adopt the brain again.")
        if data["brain_format"] > BRAIN_FORMAT:
            return RootStatus("newer", f"This brain was made by a newer ATLAS (brain format "
                                       f"{data['brain_format']}; this copy understands up to "
                                       f"{BRAIN_FORMAT}). Update ATLAS on this OS; nothing was changed.")
        if not has_map:  # marked as a brain, but the brain is gone: never start an empty one
            return RootStatus("foreign", f"{marker} marks this folder as a brain, but "
                                         f"{paths.brain / 'map.json'} is missing. Restore brain/ (for "
                                         "example from the backup remote); ATLAS won't start a new "
                                         "brain over it.")
        return RootStatus("ok", "") if writable else _readonly(root)
    if has_map:  # a brain made before markers
        if not writable:
            return _readonly(root)
        try:
            _write_marker(paths)
        except OSError:
            return _readonly(root)
        return RootStatus("ok", "")
    if not _empty_or_absent(paths.brain):
        return RootStatus("foreign", f"{paths.brain} has files but no map.json, so this doesn't look "
                                     "like an ATLAS brain. ATLAS won't use or change it: point it "
                                     "at the right folder, or at an empty one to start a new brain.")
    if not writable:  # setup couldn't create a brain here anyway
        return _readonly(root)
    return RootStatus("fresh", "No brain here yet: setup can create one.")


# -- hub lock ----------------------------------------------------------------
def pid_alive(pid) -> bool:
    """Is a process with this pid running on this OS right now?"""
    if type(pid) is not int or pid <= 0:  # os.kill(0, 0) would signal our own process group
        return False
    if sys.platform == "win32":
        return _windows_pid_alive(pid)
    return _posix_pid_alive(pid)


def _posix_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # signal 0: existence check only
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # EPERM: it exists but belongs to another user
    except (OSError, OverflowError):
        return False  # no such pid can exist
    return True


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5
STILL_ACTIVE = 259


def _windows_pid_alive(pid: int) -> bool:
    # Never os.kill here: on Windows it terminates the process.
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # own instance: argtypes stay local
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:  # access denied: it exists but we may not query it; else it's gone
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True  # can't tell: assume alive rather than run two hubs
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def process_start(pid) -> str | None:
    """When this pid's process started, as an opaque string, or None when it can't be read.
    With it, a lock names one process, not just a pid that may since have been reused."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        if sys.platform == "win32":
            return _windows_process_start(pid)
        stat_line = Path(f"/proc/{pid}/stat").read_text("ascii", errors="replace")
        return "proc-" + stat_line.rsplit(")", 1)[1].split()[19]  # field 22: start time in ticks
    except (OSError, IndexError):
        return None


def _windows_process_start(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        created = times[0]
        return f"ft-{(created.dwHighDateTime << 32) | created.dwLowDateTime}"
    finally:
        kernel32.CloseHandle(handle)


def _holder_alive(held: dict) -> bool:
    """Is the process that wrote this lock (this OS, this boot) still running? A pid equal to our
    own is stale (we are that pid now), and so is a live pid whose start time differs from the
    lock's: the pid was reused after the hub that held it was killed."""
    pid = held.get("pid")
    if pid == os.getpid() or not pid_alive(pid):
        return False
    started = held.get("pid_start")
    now = process_start(pid)
    return started is None or now is None or now == started


def _read_lock(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def acquire_lock(paths: Paths, *, write: bool = True) -> str | None:
    """Take the hub lock; return None, or a refusal message when another hub runs on this root.

    A lock from the other OS, from an earlier boot or from a dead process is stale (only one OS
    runs at a time) and is taken over. A lock from this OS, this boot and a live process means
    another hub is running. With write=False (a read-only root) it only checks. Call it only on
    a root that exists: it creates `data/` inside it, never the root."""
    path = paths.data / LOCK_NAME
    held = _read_lock(path)
    if (held is not None and held.get("os") == osdirs.os_name()
            and held.get("boot") == osdirs.boot_id() and _holder_alive(held)):
        return (f"Another ATLAS (pid {held['pid']}, started {held.get('started', '?')}) is already "
                f"running on this root on this OS. If none is running, delete {path}.")
    if write:
        paths.data.mkdir(exist_ok=True)
        _write_json(path, {"os": osdirs.os_name(), "boot": osdirs.boot_id(), "pid": os.getpid(),
                           "pid_start": process_start(os.getpid()), "started": _now()})
    return None


def release_lock(paths: Paths) -> None:
    """Remove the hub lock, but only while it is still ours (same pid and boot)."""
    path = paths.data / LOCK_NAME
    held = _read_lock(path)
    if (held is not None and held.get("pid") == os.getpid() and held.get("boot") == osdirs.boot_id()
            and held.get("os") == osdirs.os_name()):
        try:
            path.unlink()
        except OSError:
            pass  # already gone, or the root turned read-only: the next start takes it over
