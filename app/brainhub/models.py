from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Entry:
    id: str
    ts: datetime
    source: str  # "pc" | "widget" | "tablet"
    text: str


@dataclass
class Filing:
    entry_id: str
    status: str  # queued | running | done | failed
    synopsis: str = ""
    files: list[str] = field(default_factory=list)
    commit: str | None = None
    error: str | None = None
    attempts: int = 0


@dataclass
class Proposal:
    id: str
    entry_id: str
    ptype: str  # item | update | node
    payload: dict
    status: str  # proposed | confirmed | dismissed
    warnings: list[str] = field(default_factory=list)


@dataclass
class Item:
    id: str
    kind: str  # task | deadline | reminder | waiting | next_time
    text: str
    area: str
    project: str | None
    person: str | None
    due: str | None  # local ISO "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM"
    remind_at: str | None
    status: str  # open | to_read | done | dismissed | snoozed
    snooze_until: str | None
    source_entry: str
    created: str
    updated: str


@dataclass
class Hit:
    kind: str  # entry | note
    ref: str  # entry id or "path#heading"
    date: str | None
    snippet: str
    text: str
