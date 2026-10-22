"""Verbatim, append-only raw log. The source of truth for the whole app."""
from __future__ import annotations

import os
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

from .models import Entry

_APPEND_LOCK = threading.Lock()  # appends from threads must not interleave
# Ids handed out during the current second (guarded by _APPEND_LOCK): 4 hex chars can collide.
_issued: tuple[datetime | None, set[str]] = (None, set())
SOURCES = ("pc", "widget", "tablet")

_HEADER = re.compile(
    r"<!-- entry id=(\S+) ts=(\S+) source=(\S+) len=(\d+) -->\n")
_END = "\n<!-- /entry -->\n"
_SEP = "\n"  # blank line after each entry
_LOG_FILE = re.compile(r"[0-9]{4}/[0-9]{2}/[0-9]{4}-[0-9]{2}-[0-9]{2}\.md")
ENTRY_ID = re.compile(r"e-([0-9]{8}-[0-9]{6})-[0-9a-f]{4}")  # its middle part is the ts, to the second


def new_entry_id(ts: datetime) -> str:
    return f"e-{ts:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def log_path(brain: Path, ts: datetime) -> Path:
    return Path(brain) / "log" / f"{ts:%Y}" / f"{ts:%m}" / f"{ts:%Y-%m-%d}.md"


def check_entry_id(entry_id, ts: datetime) -> None:
    """Raise ValueError unless entry_id has the id shape and its timestamp part is ts."""
    m = ENTRY_ID.fullmatch(entry_id) if isinstance(entry_id, str) else None
    if m is None:
        raise ValueError(f"bad entry id: {entry_id!r}")
    if m.group(1) != f"{ts:%Y%m%d-%H%M%S}":
        raise ValueError(f"entry id {entry_id} does not match its timestamp {ts.isoformat()}")


def append_entry(brain: Path, text: str, source: str,
                 now: datetime | None = None, entry_id: str | None = None) -> Entry:
    """Append one entry and fsync it. `entry_id` is given only for a capture held while the root
    wasn't writable (holding.py): it keeps the id it was given then, with its own ts as `now`."""
    global _issued
    if not text.strip():
        raise ValueError("entry text is blank")
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source!r}")
    ts = (now or datetime.now()).replace(microsecond=0)
    if entry_id is not None:
        check_entry_id(entry_id, ts)
    text.encode("utf-8")  # fail before touching the file if unencodable
    path = log_path(brain, ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _APPEND_LOCK:
        second, ids = _issued
        if entry_id is not None:  # an old ts: the ids issued this second stay as they are
            if second == ts:
                ids.add(entry_id)
        else:
            if second != ts:
                ids = set()
                _issued = (ts, ids)
            entry_id = new_entry_id(ts)
            while entry_id in ids:  # same second and same 4 hex chars: draw again
                entry_id = new_entry_id(ts)
            ids.add(entry_id)
        entry = Entry(id=entry_id, ts=ts, source=source, text=text)
        record = (f"<!-- entry id={entry.id} ts={ts.isoformat()} source={source} "
                  f"len={len(text)} -->\n{text}{_END}{_SEP}")
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(record)
            f.flush()
            os.fsync(f.fileno())  # on disk before anyone is told "saved"
    return entry


class _Damaged(ValueError):
    """A record that can't be read; `pos` is the offset where it starts."""

    def __init__(self, message: str, pos: int):
        super().__init__(message)
        self.pos = pos


def _parse_record(path: Path, content: str, pos: int) -> tuple[Entry, int]:
    """Parse the record at `pos`; return it and the offset of the next one."""
    m = _HEADER.match(content, pos)
    if not m:
        raise _Damaged(f"{path}: malformed entry header at offset {pos}", pos)
    start = m.end()
    end = start + int(m.group(4))
    if end > len(content):
        raise _Damaged(f"{path}: truncated entry at offset {pos} "
                       f"(len={m.group(4)} runs past end of file)", pos)
    if not content.startswith(_END, end):
        raise _Damaged(f"{path}: missing end marker for entry at "
                       f"offset {pos} (bad len or truncated)", pos)
    after = end + len(_END)
    if not content.startswith(_SEP, after):
        raise _Damaged(f"{path}: missing blank line after entry at "
                       f"offset {pos} (truncated?)", pos)
    try:
        ts = datetime.fromisoformat(m.group(2))
    except ValueError:
        raise _Damaged(f"{path}: bad timestamp at offset {pos}", pos) from None
    entry = Entry(id=m.group(1), ts=ts, source=m.group(3), text=content[start:end])
    return entry, after + len(_SEP)


def _parse_file(path: Path, problems: list | None = None) -> list[Entry]:
    """Strict (problems is None): the first damage raises ValueError. Tolerant: each damaged
    record is described in `problems` and skipped; reading resumes at the next record header."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        if problems is None:
            raise ValueError(f"{path}: not valid UTF-8 ({e.reason} at byte {e.start})") from None
        problems.append(f"{path}: not valid UTF-8 ({e.reason} at byte {e.start}), file skipped")
        return []
    entries: list[Entry] = []
    pos = 0
    while pos < len(content):
        try:
            entry, pos = _parse_record(path, content, pos)
        except _Damaged as e:
            if problems is None:
                raise ValueError(str(e)) from None
            problems.append(str(e))
            nxt = _HEADER.search(content, e.pos + 1)
            if nxt is None:
                break
            pos = nxt.start()
            continue
        entries.append(entry)
    return entries


def read_log(brain: Path, problems: list | None = None) -> list[Entry]:
    """Every entry, oldest file first; the log is only ever read here, never changed.

    Without `problems`, any damage raises ValueError. With a list, damaged records and
    files (and stray non-log .md files under log/) are skipped and described in it as
    "<file>: <reason>", and every intact entry is still returned."""
    root = Path(brain) / "log"
    if not root.is_dir():
        return []
    entries: list[Entry] = []
    if problems is None:
        for path in sorted(root.glob("*/*/*.md")):
            entries.extend(_parse_file(path))
        return entries
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        if not _LOG_FILE.fullmatch(path.relative_to(root).as_posix()):
            problems.append(f"{path}: not a raw log file (expected log/YYYY/MM/YYYY-MM-DD.md), skipped")
            continue
        try:
            entries.extend(_parse_file(path, problems))
        except OSError as e:
            problems.append(f"{path}: could not be read ({e})")
    return entries
