"""Verbatim, append-only raw log. The source of truth for the whole app."""
from __future__ import annotations

import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

from .models import Entry

_APPEND_LOCK = threading.Lock()  # appends from threads must not interleave
SOURCES = ("pc", "widget", "tablet")

_HEADER = re.compile(
    r"<!-- entry id=(\S+) ts=(\S+) source=(\S+) len=(\d+) -->\n")
_END = "\n<!-- /entry -->\n"
_SEP = "\n"  # blank line after each entry


def new_entry_id(ts: datetime) -> str:
    return f"e-{ts:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def log_path(brain: Path, ts: datetime) -> Path:
    return Path(brain) / "log" / f"{ts:%Y}" / f"{ts:%m}" / f"{ts:%Y-%m-%d}.md"


def append_entry(brain: Path, text: str, source: str,
                 now: datetime | None = None) -> Entry:
    if not text.strip():
        raise ValueError("entry text is blank")
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source!r}")
    ts = (now or datetime.now()).replace(microsecond=0)
    entry = Entry(id=new_entry_id(ts), ts=ts, source=source, text=text)
    record = (f"<!-- entry id={entry.id} ts={ts.isoformat()} source={source} "
              f"len={len(text)} -->\n{text}{_END}{_SEP}")
    record.encode("utf-8")  # fail before touching the file if unencodable
    path = log_path(brain, ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _APPEND_LOCK:
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(record)
            f.flush()
    return entry


def _parse_file(path: Path) -> list[Entry]:
    with open(path, encoding="utf-8", newline="") as f:
        content = f.read()
    entries: list[Entry] = []
    pos = 0
    while pos < len(content):
        m = _HEADER.match(content, pos)
        if not m:
            raise ValueError(f"{path}: malformed entry header at offset {pos}")
        start = m.end()
        end = start + int(m.group(4))
        if end > len(content):
            raise ValueError(f"{path}: truncated entry at offset {pos} "
                             f"(len={m.group(4)} runs past end of file)")
        if not content.startswith(_END, end):
            raise ValueError(f"{path}: missing end marker for entry at "
                             f"offset {pos} (bad len or truncated)")
        after = end + len(_END)
        if not content.startswith(_SEP, after):
            raise ValueError(f"{path}: missing blank line after entry at "
                             f"offset {pos} (truncated?)")
        try:
            ts = datetime.fromisoformat(m.group(2))
        except ValueError:
            raise ValueError(f"{path}: bad timestamp at offset {pos}") from None
        entries.append(Entry(id=m.group(1), ts=ts, source=m.group(3),
                             text=content[start:end]))
        pos = after + len(_SEP)
    return entries


def read_log(brain: Path) -> list[Entry]:
    root = Path(brain) / "log"
    if not root.is_dir():
        return []
    entries: list[Entry] = []
    for path in sorted(root.glob("*/*/*.md")):
        entries.extend(_parse_file(path))
    return entries
