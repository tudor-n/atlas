"""Holding inbox: captures typed while the root isn't writable (dual-boot design §6.5 item 3).

Each capture is one file `<state>/holding/<entry_id>.json` (`{"id", "ts", "source", "text",
"root"}`) in this OS's state folder, on disk before the API answers "saved". `root` is the root it
was typed for (`{"id", "path"}`: the marker id when one was readable, and the resolved path). When
that root is writable again, `flush` appends each one to the raw log with its own id and ts, so it
lands in its own day's file, and only then deletes the held file. Captures held for another root
stay held: a dev hub on a temp root shares this OS's state folder. A file that can't be read is
kept and reported, never deleted: it may be the only copy of what the owner wrote.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import rawlog, rootguard
from .config import Paths

log = logging.getLogger(__name__)

FOLDER = "holding"
TMP_GRACE = 60.0  # seconds: another live process's tmp younger than this may still be being written
_LOCK = threading.Lock()  # a hold and the tmp recovery in held() never run at once in one process
_TMP = re.compile(r"(e-[0-9]{8}-[0-9]{6}-[0-9a-f]{4})\.json\.([0-9]+)\.tmp")
# Unusable files already logged: /api/state and /api/widget list them again on every poll.
_REPORTED: set[str] = set()
MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_WRITE_THROUGH = 0x8


def _folder(state_dir: Path) -> Path:
    return Path(state_dir) / FOLDER


def _fsync_dir(folder: Path) -> None:
    """POSIX: make a new or replaced directory entry durable. Windows has no directory fsync: there
    the rename itself writes through (_win_move)."""
    if os.name != "posix":
        return
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdirs(folder: Path) -> None:
    """mkdir -p. On POSIX each level it creates is made durable by fsyncing that level's parent: a
    first hold on a new install creates the state folder too."""
    created, p = [], folder
    while not p.is_dir() and p.parent != p:
        created.append(p)
        p = p.parent
    folder.mkdir(parents=True, exist_ok=True)
    for level in reversed(created):
        _fsync_dir(level.parent)


def _win_move(src: Path, dst: Path, *, replace: bool) -> None:
    """Windows: rename src to dst and return only once the rename is on disk. os.replace doesn't
    write through: a power cut after "saved" could roll the rename back and leave only the tmp.
    Without `replace` it fails with FileExistsError when dst exists."""
    import ctypes
    from ctypes import wintypes
    move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    move.restype = wintypes.BOOL
    flags = MOVEFILE_WRITE_THROUGH | (MOVEFILE_REPLACE_EXISTING if replace else 0)
    if not move(str(src), str(dst), flags):
        code = ctypes.get_last_error()  # the winerror sets errno: "already exists" is FileExistsError
        raise OSError(None, ctypes.FormatError(code), str(src), code, str(dst))


def _place(tmp: Path, path: Path, *, replace: bool) -> None:
    """Put a written, fsynced tmp at `path`. Without `replace` it never overwrites: FileExistsError
    when `path` exists (two processes sharing a state folder can hold in the same second). Durable
    at once on Windows, and once the folder is fsynced on POSIX."""
    if sys.platform == "win32":
        _win_move(tmp, path, replace=replace)
    elif replace:
        os.replace(tmp, path)
    else:
        os.link(tmp, path)  # unlike a rename, fails when path exists
        try:
            os.unlink(tmp)
        except OSError:
            pass  # placed; held() puts an identical leftover tmp back over it


# -- which root a capture was held for -----------------------------------------
def _resolved(root: Path) -> str:
    try:
        return str(Path(root).resolve())
    except OSError:  # a drive that can't be read: the absolute path as given
        return os.path.abspath(root)


def root_identity(paths: Paths) -> dict:
    """The root a capture is held for: its marker id when the marker is readable now (not on a
    missing or fresh root), and its resolved path."""
    marker = rootguard.read_marker(paths)
    rid = marker.get("id") if marker else None
    return {"id": rid if isinstance(rid, str) and rid else None, "path": _resolved(paths.root)}


def _path_key(path: str) -> str:
    return path.casefold() if sys.platform == "win32" else path  # NTFS paths compare without case


def _held_before_roots_were_recorded(record: dict) -> bool:
    """A record without `root` was written before holds recorded one (none exist in the wild): it
    matches any root, so it still reaches a raw log."""
    return "root" not in record


def _for_root(record: dict, root: dict) -> bool:
    """Was this record held for `root`? The marker ids decide when both have one (the same drive
    can be mounted at another path); else the paths do."""
    if _held_before_roots_were_recorded(record):
        return True
    meant = record["root"]
    if meant["id"] and root["id"]:
        return meant["id"] == root["id"]
    return _path_key(meant["path"]) == _path_key(root["path"])


# -- hold --------------------------------------------------------------------
def hold(state_dir: Path, paths: Paths, text: str, source: str, now: datetime,
         request: str | None = None) -> dict:
    """Hold one capture for the root at `paths`, verbatim, with its timestamp and source (and, for a
    tablet measurement answer, the request it answers, linked once it is flushed). Validates
    as append_entry does (blank text or an unknown source raise ValueError). The file is written to
    a tmp, fsynced and put in place without overwriting anything (an id another process took in the
    same second draws a new one), durably: only after this returns may the API say "saved"."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("entry text is blank")
    if source not in rawlog.SOURCES:
        raise ValueError(f"unknown source: {source!r}")
    ts = now.replace(microsecond=0)
    text.encode("utf-8")  # fail before writing anything if unencodable
    root = root_identity(paths)
    folder = _folder(state_dir)
    _mkdirs(folder)
    with _LOCK:
        while True:
            entry_id = rawlog.new_entry_id(ts)
            record = {"id": entry_id, "ts": ts.isoformat(), "source": source, "text": text, "root": root}
            if request:
                record["request"] = request
            path = folder / f"{entry_id}.json"
            tmp = folder / f"{entry_id}.json.{os.getpid()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                _place(tmp, path, replace=False)
            except FileExistsError:  # same second and same 4 hex chars as a held capture: draw again
                tmp.unlink(missing_ok=True)
                continue
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            break
        _fsync_dir(folder)
    return record


# -- read --------------------------------------------------------------------
def _report(problems: list | None, path: Path, msg: str) -> None:
    """Describe an unusable file in `problems` every time; log it once per process."""
    if str(path) not in _REPORTED:
        _REPORTED.add(str(path))
        log.error(msg)
    if problems is not None:
        problems.append(msg)


def _load(path: Path, name: str | None = None) -> dict:
    """One held record, checked; `name` is the file name its id must give (default: its own).
    Raises OSError or ValueError when it can't be used."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    text, source = data.get("text"), data.get("source")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is missing or blank")
    if source not in rawlog.SOURCES:
        raise ValueError(f"unknown source: {source!r}")
    if not isinstance(data.get("ts"), str):
        raise ValueError("ts is missing")
    ts = datetime.fromisoformat(data["ts"])
    rawlog.check_entry_id(data.get("id"), ts)
    if (name or path.name) != f"{data['id']}.json":
        raise ValueError(f"file name does not match its id {data['id']}")
    record = {"id": data["id"], "ts": ts.isoformat(), "source": source, "text": text}
    if "root" in data:
        root = data["root"]
        if not (isinstance(root, dict) and isinstance(root.get("path"), str) and root["path"]
                and (root.get("id") is None or isinstance(root["id"], str))):
            raise ValueError("root is not {id, path}")
        record["root"] = {"id": root.get("id") or None, "path": root["path"]}
    if isinstance(data.get("request"), str) and data["request"]:
        record["request"] = data["request"]
    return record


def _maybe_being_written(tmp: Path, pid: int) -> bool:
    """Two hubs can share a state folder: a tmp that another live process wrote less than
    TMP_GRACE seconds ago may be a hold still in progress, so it is left alone for now. Ours never
    is: holds take _LOCK, which the caller holds."""
    if pid == os.getpid():
        return False
    try:
        age = time.time() - tmp.stat().st_mtime
    except FileNotFoundError:
        return True  # its hold just put it in place
    return age < TMP_GRACE and rootguard.pid_alive(pid)


def _recover(tmp: Path, problems: list | None) -> None:
    """A hold interrupted before its rename was on disk (a power cut) leaves only its tmp. One that
    holds a valid record is put in place, durably; one that doesn't is reported. Neither is ever
    deleted."""
    try:
        m = _TMP.fullmatch(tmp.name)
        if m is None:
            raise ValueError("not named <id>.json.<pid>.tmp")
        if _maybe_being_written(tmp, int(m.group(2))):
            return
        path = tmp.with_name(f"{m.group(1)}.json")
        _load(tmp, path.name)
        if path.exists():
            if path.read_bytes() != tmp.read_bytes():
                raise ValueError(f"{path.name} already holds a different capture")
            _place(tmp, path, replace=True)  # the same capture twice (a crash right after the link)
        else:
            _place(tmp, path, replace=False)
        _fsync_dir(tmp.parent)
        log.warning("recovered the interrupted hold %s as %s", tmp, path.name)
    except (OSError, ValueError) as e:
        _report(problems, tmp, f"interrupted hold {tmp} could not be recovered ({e}); it was kept, "
                               "nothing was changed")


def held(state_dir: Path, problems: list | None = None, paths: Paths | None = None) -> list[dict]:
    """Held captures, oldest first: all of them, or with `paths` only those held for that root.
    Interrupted holds are recovered first. A file that can't be read is skipped and described in
    `problems` (and logged once per process), never deleted."""
    folder = _folder(state_dir)
    if not folder.is_dir():
        return []
    with _LOCK:
        for tmp in sorted(folder.glob("*.json.*.tmp")):
            _recover(tmp, problems)
    root = root_identity(paths) if paths is not None else None
    out = []
    for path in sorted(folder.glob("*.json")):
        try:
            record = _load(path)
        except (OSError, ValueError) as e:
            _report(problems, path, f"held capture {path} could not be read ({e}); it was kept, "
                                    "nothing was changed")
            continue
        if root is None or _for_root(record, root):
            out.append(record)
    out.sort(key=lambda r: (r["ts"], r["id"]))
    return out


# -- flush -------------------------------------------------------------------
def _same(entry, record: dict) -> bool:
    return (entry.ts.isoformat(), entry.source, entry.text) == (record["ts"], record["source"], record["text"])


@dataclass
class Flushed:
    """What flush did: the ids it appended to the raw log, and the records held for another root
    (left held)."""
    appended: list[str] = field(default_factory=list)
    elsewhere: list[dict] = field(default_factory=list)
    tablet: list[str] = field(default_factory=list)  # appended ids from the tablet: never auto-filed
    answers: dict[str, str] = field(default_factory=dict)  # appended answer id -> the request it answers


def flush(state_dir: Path, paths: Paths, store, problems: list | None = None) -> Flushed:
    """Move the captures held for the root at `paths` into its raw log, oldest first. Each keeps
    its id and ts, so it lands in its own day's file. A record whose id the store or the log
    already holds (a crash between the append and the delete) is not appended again: its held
    file is just deleted. An id holding a different entry is never overwritten or dropped: the
    held file stays and the clash is reported. Nothing is appended to a day file the tolerant
    reader finds damaged (the reader could skip the new entry with it): that capture stays held
    and is reported. Captures held for another root stay held, listed in `elsewhere`."""
    def report(msg: str) -> None:
        log.error(msg)
        if problems is not None:
            problems.append(msg)

    root, done, records = root_identity(paths), Flushed(), []
    for r in held(state_dir, problems):
        (records if _for_root(r, root) else done.elsewhere).append(r)
    if not records:
        return done
    brain = paths.brain
    logged = {e.id: e for e in rawlog.read_log(brain, [])}  # intact records only: a torn one is re-appended
    damage: dict[Path, list[str]] = {}  # day file -> what the tolerant reader reports about it
    for r in records:
        path = _folder(state_dir) / f"{r['id']}.json"
        existing = store.get_entry(r["id"]) or logged.get(r["id"])
        if existing is not None and not _same(existing, r):
            report(f"held capture {path} has the id of a different entry already in the log; "
                   "it was kept, nothing was changed")
            continue
        if existing is None:
            ts = datetime.fromisoformat(r["ts"])
            day = rawlog.log_path(brain, ts)
            if day not in damage:
                try:
                    damage[day] = rawlog.file_problems(day)
                except OSError as e:
                    damage[day] = [f"{day}: could not be read ({e})"]
            if damage[day]:
                report(f"held capture {path} was kept: {damage[day][0]}. An entry appended there could "
                       "be skipped with the damage, so it stays held until that file is fixed")
                continue
            try:
                rawlog.append_entry(brain, r["text"], r["source"], now=ts, entry_id=r["id"])
            except OSError as e:  # the log can't be written now: keep this one and the rest held
                report(f"held captures could not be moved into the raw log ({e}); they stay held")
                break
            done.appended.append(r["id"])
            if r.get("source") == "tablet":
                done.tablet.append(r["id"])
                if r.get("request"):
                    done.answers[r["id"]] = r["request"]
        try:
            path.unlink()  # only now: the log has it
        except OSError as e:
            report(f"held capture {path} is in the raw log but could not be deleted ({e}); "
                   "the next start deletes it")
    return done
