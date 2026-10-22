"""Holding inbox: captures typed while the root isn't writable (dual-boot design §6.5 item 3).

Each capture is one file `<state>/holding/<entry_id>.json` (`{"id", "ts", "source", "text"}`) in
this OS's state folder, on disk before the API answers "saved". When the root is writable again,
`flush` appends each one to the raw log with its own id and ts, so it lands in its own day's
file, and only then deletes the held file. A file that can't be read is kept and reported, never
deleted: it may be the only copy of what the owner wrote.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from . import rawlog

log = logging.getLogger(__name__)

FOLDER = "holding"
_LOCK = threading.Lock()  # two holds in one second must not pick the same id


def _folder(state_dir: Path) -> Path:
    return Path(state_dir) / FOLDER


def _fsync_dir(folder: Path) -> None:
    """POSIX: make a new or replaced directory entry durable. Windows has no directory fsync
    (os.replace there is durable once the file itself is flushed)."""
    if os.name != "posix":
        return
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def hold(state_dir: Path, text: str, source: str, now: datetime) -> dict:
    """Hold one capture, verbatim, with its timestamp and source. Validates as append_entry does
    (blank text or an unknown source raise ValueError). The file is written to a tmp, fsynced,
    renamed into place and the folder fsynced: only after this returns may the API say "saved"."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("entry text is blank")
    if source not in rawlog.SOURCES:
        raise ValueError(f"unknown source: {source!r}")
    ts = now.replace(microsecond=0)
    text.encode("utf-8")  # fail before writing anything if unencodable
    folder = _folder(state_dir)
    created = not folder.is_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        entry_id = rawlog.new_entry_id(ts)
        while (folder / f"{entry_id}.json").exists():  # same second and same 4 hex chars
            entry_id = rawlog.new_entry_id(ts)
        record = {"id": entry_id, "ts": ts.isoformat(), "source": source, "text": text}
        path = folder / f"{entry_id}.json"
        tmp = folder / f"{entry_id}.json.{os.getpid()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        _fsync_dir(folder)
        if created:
            _fsync_dir(folder.parent)
    return record


def _load(path: Path) -> dict:
    """One held record, checked. Raises OSError or ValueError when it can't be used."""
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
    if path.name != f"{data['id']}.json":
        raise ValueError(f"file name does not match its id {data['id']}")
    return {"id": data["id"], "ts": ts.isoformat(), "source": source, "text": text}


def held(state_dir: Path, problems: list | None = None) -> list[dict]:
    """Every held capture, oldest first. A file that can't be read is skipped and described in
    `problems` (and logged), never deleted."""
    folder = _folder(state_dir)
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.json")):
        try:
            out.append(_load(path))
        except (OSError, ValueError) as e:
            msg = f"held capture {path} could not be read ({e}); it was kept, nothing was changed"
            log.error(msg)
            if problems is not None:
                problems.append(msg)
    out.sort(key=lambda r: (r["ts"], r["id"]))
    return out


def _same(entry, record: dict) -> bool:
    return (entry.ts.isoformat(), entry.source, entry.text) == (record["ts"], record["source"], record["text"])


def flush(state_dir: Path, brain: Path, store, problems: list | None = None) -> list[str]:
    """Move held captures into the raw log, oldest first, and return the ids appended. Each keeps
    its id and ts, so it lands in its own day's file. A record whose id the store or the log
    already holds (a crash between the append and the delete) is not appended again: its held
    file is just deleted. An id holding a different entry is never overwritten or dropped: the
    held file stays and the clash is reported."""
    def report(msg: str) -> None:
        log.error(msg)
        if problems is not None:
            problems.append(msg)

    records = held(state_dir, problems)
    if not records:
        return []
    logged = {e.id: e for e in rawlog.read_log(brain, [])}  # intact records only: a torn one is re-appended
    appended = []
    for r in records:
        path = _folder(state_dir) / f"{r['id']}.json"
        existing = store.get_entry(r["id"]) or logged.get(r["id"])
        if existing is not None and not _same(existing, r):
            report(f"held capture {path} has the id of a different entry already in the log; "
                   "it was kept, nothing was changed")
            continue
        if existing is None:
            try:
                rawlog.append_entry(brain, r["text"], r["source"], now=datetime.fromisoformat(r["ts"]),
                                    entry_id=r["id"])
            except OSError as e:  # the log can't be written now: keep this one and the rest held
                report(f"held captures could not be moved into the raw log ({e}); they stay held")
                break
            appended.append(r["id"])
        try:
            path.unlink()  # only now: the log has it
        except OSError as e:
            report(f"held capture {path} is in the raw log but could not be deleted ({e}); "
                   "the next start deletes it")
    return appended
