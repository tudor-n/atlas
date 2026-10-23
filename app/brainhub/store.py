"""SQLite index and state. Everything here is rebuildable from the brain/ folder."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from .models import Entry, Filing, Hit, Item, Proposal
from .rawlog import read_log
from .today import snooze_active

log = logging.getLogger(__name__)

_ITEM_FIELDS = [f.name for f in fields(Item)]
_SKIP_DIRS = ("log", "_items")
_HEADING = re.compile(r"^#+\s+(.*?)\s*$")
BUSY_TIMEOUT = 5.0  # seconds a statement waits for another connection's lock before "database is locked"


def is_note_path(rel: str) -> bool:
    """A brain-relative path the notes index covers: a .md file outside log/ and _items/ and
    outside any dot-folder (.git, .obsidian, .trash...)."""
    parts = rel.replace("\\", "/").split("/")
    return (rel.lower().endswith(".md") and parts[0].lower() not in _SKIP_DIRS
            and not any(p.startswith(".") for p in parts))


def _migrate_2(db: sqlite3.Connection) -> None:
    """Agents rows record the OS and the boot they were seen in (dual-boot design §6.6). A db
    made before the agents table existed gets it, with both columns, from _SCHEMA."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(agents)")}
    for col in ("os", "boot"):
        if cols and col not in cols:
            db.execute(f"ALTER TABLE agents ADD COLUMN {col} TEXT")


def _migrate_3(db: sqlite3.Connection) -> None:
    """Phase 3 (the workbench tablet) only adds tables: devices, requests and tablet_hidden come
    from _SCHEMA's CREATE TABLE IF NOT EXISTS right after the migrations run."""


# The schema version lives in PRAGMA user_version (dual-boot design §5.2). _SCHEMA is always the
# latest schema; MIGRATIONS[n] moves a version n-1 database to version n.
SCHEMA_VERSION = 3
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {2: _migrate_2, 3: _migrate_3}
HIDDEN_KINDS = ("note", "request")  # what IGNORE can hide on the tablet (design §6.2, §7)


class SchemaTooNew(Exception):
    """hub.db was written by a newer BrainHub. It is never migrated, rebuilt or moved aside: code
    meeting data newer than it understands refuses it (a dev copy pointed at the real root, or an
    OS whose runtime is behind the other's)."""

    def __init__(self, found: int, known: int):
        super().__init__(f"hub.db was written by a newer BrainHub (schema {found}; this copy knows up "
                         f"to {known}). Update BrainHub on this OS; nothing was changed.")
        self.found, self.known = found, known


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries(
    id TEXT PRIMARY KEY, ts TEXT NOT NULL, source TEXT NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS entries_ts ON entries(ts);
CREATE TABLE IF NOT EXISTS filings(
    entry_id TEXT PRIMARY KEY, status TEXT NOT NULL, synopsis TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '[]', "commit" TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS proposals(
    id TEXT PRIMARY KEY, seq INTEGER NOT NULL, entry_id TEXT NOT NULL, ptype TEXT NOT NULL,
    payload TEXT NOT NULL, status TEXT NOT NULL, warnings TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS items(
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, area TEXT NOT NULL,
    project TEXT, person TEXT, due TEXT, remind_at TEXT, status TEXT NOT NULL,
    snooze_until TEXT, source_entry TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents(
    session_id TEXT PRIMARY KEY, cwd TEXT NOT NULL, project TEXT NOT NULL, project_name TEXT NOT NULL,
    mapped INTEGER NOT NULL, branch TEXT, title TEXT, part_override TEXT, entrypoint TEXT,
    where_override TEXT, status TEXT NOT NULL, note TEXT, started_at TEXT NOT NULL,
    status_since TEXT NOT NULL, last_event_at TEXT NOT NULL, ended_at TEXT, os TEXT, boot TEXT);
CREATE TABLE IF NOT EXISTS devices(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
    paired_at TEXT NOT NULL, last_seen TEXT);
CREATE TABLE IF NOT EXISTS requests(
    id TEXT PRIMARY KEY, title TEXT NOT NULL, items TEXT NOT NULL, project TEXT, project_name TEXT,
    created TEXT NOT NULL, status TEXT NOT NULL, answer_entry TEXT);
CREATE TABLE IF NOT EXISTS tablet_hidden(
    kind TEXT NOT NULL, ref TEXT NOT NULL, hidden_at TEXT NOT NULL, PRIMARY KEY(kind, ref));
CREATE TABLE IF NOT EXISTS tablet_sends(
    client_id TEXT PRIMARY KEY, entry_id TEXT NOT NULL, at TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    kind UNINDEXED, ref UNINDEXED, date UNINDEXED, body,
    tokenize='unicode61 remove_diacritics 2');
"""


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _entry(r) -> Entry:
    return Entry(r["id"], datetime.fromisoformat(r["ts"]), r["source"], r["text"])


def _filing(r) -> Filing:
    return Filing(r["entry_id"], r["status"], r["synopsis"], json.loads(r["files"]),
                  r["commit"], r["error"], r["attempts"])


def _proposal(r) -> Proposal:
    return Proposal(r["id"], r["entry_id"], r["ptype"], json.loads(r["payload"]),
                    r["status"], json.loads(r["warnings"]))


def _item(r) -> Item:
    return Item(**{k: r[k] for k in _ITEM_FIELDS})


class Store:
    def __init__(self, db_path: Path, *, readonly: bool = False):
        """readonly: the root isn't writable (dual-boot design §6.5). The file is opened immutable,
        so SQLite takes no lock and writes nothing, not even a journal or the -shm file. A -wal left
        by a crash is not read then: the view is the one at the last checkpoint."""
        self._lock = threading.RLock()
        self.readonly = readonly
        if readonly:
            uri = f"{Path(db_path).resolve().as_uri()}?mode=ro&immutable=1"
            self._db = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            return
        self._db = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            self._db.close()  # release the file so a damaged one can be moved aside
            raise

    def init_schema(self) -> None:
        """Create, migrate or check the schema. user_version 0 is a new database, or one made
        before versioning (whose tables are version 1); lower versions are migrated in order;
        a higher one raises SchemaTooNew before anything is written. A read-only store is only
        checked: one that would need creating or migrating can't be read, so it raises
        sqlite3.OperationalError (the hub then serves the reduced app)."""
        with self._lock:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise SchemaTooNew(version, SCHEMA_VERSION)
            if version == 0 and self._db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
                version = 1  # made before versioning: the version 1 tables
            if self.readonly:
                if version < SCHEMA_VERSION:
                    raise sqlite3.OperationalError(
                        f"hub.db has schema {version} and this BrainHub reads {SCHEMA_VERSION}; it is "
                        "updated at the first start on a writable root")
                return
            if 0 < version < SCHEMA_VERSION:
                self._migrate(version)
            self._db.executescript(_SCHEMA)  # IF NOT EXISTS: fills a new db, keeps existing tables
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._db.commit()

    def _migrate(self, version: int) -> None:
        """Run MIGRATIONS[version+1..SCHEMA_VERSION] and record the new version, all or nothing."""
        self._db.execute("BEGIN")
        try:
            for n in range(version + 1, SCHEMA_VERSION + 1):
                MIGRATIONS[n](self._db)
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def schema_version(self) -> int:
        with self._lock:
            return self._db.execute("PRAGMA user_version").fetchone()[0]

    def quick_check(self) -> list[str]:
        """SQLite's own quick integrity check: [] when the file is sound, else what it found."""
        with self._lock:
            rows = [r[0] for r in self._db.execute("PRAGMA quick_check").fetchall()]
        return [] if rows == ["ok"] else rows

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # entries
    def add_entry(self, e: Entry) -> None:
        """Idempotent for the same entry; a different entry under an existing id raises
        ValueError instead of silently replacing it."""
        row = (e.id, e.ts.isoformat(), e.source, e.text)
        with self._lock, self._db:
            r = self._db.execute("SELECT * FROM entries WHERE id=?", (e.id,)).fetchone()
            if r is not None:
                if tuple(r) != row:
                    raise ValueError(f"entry id collision: {e.id} already holds a different entry")
                return
            self._db.execute("INSERT INTO entries VALUES(?,?,?,?)", row)

    def get_entry(self, id: str) -> Entry | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM entries WHERE id=?", (id,)).fetchone()
        return _entry(r) if r else None

    def list_entries(self, day: str | None = None, before: str | None = None,
                     limit: int = 200, source: str | None = None) -> list[Entry]:
        sql, args = "SELECT * FROM entries WHERE 1=1", []
        if source:
            sql += " AND source=?"
            args.append(source)
        if day:
            sql += " AND substr(ts,1,10)=?"
            args.append(day)
        with self._lock:
            if before:
                b = self._db.execute("SELECT ts FROM entries WHERE id=?", (before,)).fetchone()
                if b:
                    sql += " AND (ts<? OR (ts=? AND id<?))"
                    args += [b["ts"], b["ts"], before]
            rows = self._db.execute(sql + " ORDER BY ts DESC, id DESC LIMIT ?",
                                    args + [limit]).fetchall()
        return [_entry(r) for r in rows]

    # filings
    def upsert_filing(self, f: Filing) -> None:
        with self._lock, self._db:
            self._db.execute(
                'INSERT OR REPLACE INTO filings(entry_id,status,synopsis,files,"commit",error,attempts)'
                " VALUES(?,?,?,?,?,?,?)",
                (f.entry_id, f.status, f.synopsis, json.dumps(f.files), f.commit, f.error, f.attempts))

    def get_filing(self, entry_id: str) -> Filing | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM filings WHERE entry_id=?", (entry_id,)).fetchone()
        return _filing(r) if r else None

    def list_filings(self, status: str | None = None,
                     entry_ids: list[str] | None = None) -> list[Filing]:
        sql, args = "SELECT * FROM filings WHERE 1=1", []
        if status is not None:
            sql += " AND status=?"
            args.append(status)
        if entry_ids is not None:
            sql += f" AND entry_id IN ({','.join('?' * len(entry_ids))})"
            args += entry_ids
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY entry_id", args).fetchall()
        return [_filing(r) for r in rows]

    def unfinished_filings(self) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT e.id FROM entries e LEFT JOIN filings f ON f.entry_id=e.id"
                " WHERE f.status IN ('queued','running')"
                " OR (f.entry_id IS NULL AND e.source!='tablet')"
                " ORDER BY e.ts, e.id").fetchall()
        return [r["id"] for r in rows]

    def unfiled_entry_ids(self) -> list[str]:
        """Non-tablet entries with no filing row at all, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT e.id FROM entries e LEFT JOIN filings f ON f.entry_id=e.id"
                " WHERE f.entry_id IS NULL AND e.source!='tablet' ORDER BY e.ts, e.id").fetchall()
        return [r["id"] for r in rows]

    def filings_with_entries(self, status: str) -> list[dict]:
        """[{entry_id, error, ts, text}] for every filing in `status`, newest entry first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT f.entry_id, f.error, e.ts, e.text FROM filings f"
                " LEFT JOIN entries e ON e.id=f.entry_id WHERE f.status=?"
                " ORDER BY e.ts DESC, f.entry_id DESC", (status,)).fetchall()
        return [dict(r) for r in rows]

    # proposals
    def add_proposals(self, entry_id: str,
                      ptype_payloads: list[tuple[str, dict, list[str]]]) -> list[Proposal]:
        out = []
        with self._lock, self._db:
            seq = self._db.execute("SELECT COALESCE(MAX(seq),0) FROM proposals").fetchone()[0]
            for ptype, payload, warnings in ptype_payloads:
                seq += 1
                p = Proposal(f"p-{secrets.token_hex(4)}", entry_id, ptype, payload,
                             "proposed", list(warnings))
                self._db.execute("INSERT INTO proposals VALUES(?,?,?,?,?,?,?)",
                                 (p.id, seq, entry_id, ptype, json.dumps(payload),
                                  p.status, json.dumps(p.warnings)))
                out.append(p)
        return out

    def list_proposals(self, status: str = "proposed",
                       entry_id: str | None = None) -> list[Proposal]:
        sql, args = "SELECT * FROM proposals WHERE status=?", [status]
        if entry_id:
            sql += " AND entry_id=?"
            args.append(entry_id)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY seq", args).fetchall()
        return [_proposal(r) for r in rows]

    def update_proposal_payload(self, id: str, patch: dict) -> Proposal:
        with self._lock, self._db:
            r = self._db.execute("SELECT * FROM proposals WHERE id=?", (id,)).fetchone()
            if not r:
                raise KeyError(id)
            p = _proposal(r)
            p.payload.update(patch)
            self._db.execute("UPDATE proposals SET payload=? WHERE id=?",
                             (json.dumps(p.payload), id))
        return p

    def set_proposal_status(self, ids: list[str], status: str) -> None:
        with self._lock, self._db:
            self._db.executemany("UPDATE proposals SET status=? WHERE id=?",
                                 [(status, i) for i in ids])

    def delete_proposals(self, entry_id: str, status: str = "proposed") -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM proposals WHERE entry_id=? AND status=?",
                             (entry_id, status))

    # items
    def create_item(self, **item_fields) -> Item:
        unknown = set(item_fields) - set(_ITEM_FIELDS)
        if unknown:
            raise ValueError(f"unknown item fields: {sorted(unknown)}")
        now = _now()
        data = {"project": None, "person": None, "due": None, "remind_at": None,
                "snooze_until": None, "status": "open", **item_fields}
        data["id"] = f"i-{secrets.token_hex(4)}"
        data["created"] = data["updated"] = now
        item = Item(**data)
        with self._lock, self._db:
            self._db.execute(
                f"INSERT INTO items({','.join(_ITEM_FIELDS)}) "
                f"VALUES({','.join('?' * len(_ITEM_FIELDS))})",
                [getattr(item, k) for k in _ITEM_FIELDS])
        return item

    def update_item(self, id: str, patch: dict) -> Item:
        bad = (set(patch) - set(_ITEM_FIELDS)) | (set(patch) & {"id", "created"})
        if bad:
            raise ValueError(f"cannot update item fields: {sorted(bad)}")
        with self._lock, self._db:
            if not self._db.execute("SELECT 1 FROM items WHERE id=?", (id,)).fetchone():
                raise KeyError(id)
            changes = {**patch, "updated": _now()}
            self._db.execute(
                f"UPDATE items SET {','.join(k + '=?' for k in changes)} WHERE id=?",
                [*changes.values(), id])
            r = self._db.execute("SELECT * FROM items WHERE id=?", (id,)).fetchone()
        return _item(r)

    def get_item(self, id: str) -> Item | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM items WHERE id=?", (id,)).fetchone()
        return _item(r) if r else None

    def list_items(self, status: str | list[str] | None = None, kind: str | None = None,
                   project: str | None = None, person: str | None = None,
                   area: str | None = None) -> list[Item]:
        where, args = [], []
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            args += statuses
        for col, val in (("kind", kind), ("project", project), ("person", person), ("area", area)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        sql = "SELECT * FROM items" + (" WHERE " + " AND ".join(where) if where else "")
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY created, id", args).fetchall()
        return [_item(r) for r in rows]

    # agents (live Claude Code sessions, fed by hook events)
    _AGENT_COLS = ("session_id", "cwd", "project", "project_name", "mapped", "branch", "title",
                   "part_override", "entrypoint", "where_override", "status", "note",
                   "started_at", "status_since", "last_event_at", "ended_at", "os", "boot")

    @staticmethod
    def _agent(r) -> dict:
        d = dict(r)
        d["mapped"] = bool(d["mapped"])
        return d

    def upsert_agent(self, row: dict) -> None:
        vals = [int(bool(row[c])) if c == "mapped" else row[c] for c in self._AGENT_COLS]
        with self._lock, self._db:
            self._db.execute(
                f"INSERT OR REPLACE INTO agents({','.join(self._AGENT_COLS)}) "
                f"VALUES({','.join('?' * len(self._AGENT_COLS))})", vals)

    def get_agent(self, session_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM agents WHERE session_id=?", (session_id,)).fetchone()
        return self._agent(r) if r else None

    def list_agents(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM agents ORDER BY started_at, session_id").fetchall()
        return [self._agent(r) for r in rows]

    def delete_agents(self, ids: list[str]) -> None:
        if self.readonly:  # pruning runs on reads too; on a read-only root the rows wait for a writable one
            return
        with self._lock, self._db:
            self._db.executemany("DELETE FROM agents WHERE session_id=?", [(i,) for i in ids])

    def end_agents_not_of_boot(self, boot: str, stamp: str) -> int:
        """Set ended_at=stamp on every open row seen in another boot (or before rows recorded
        one); the number of rows ended."""
        with self._lock, self._db:
            return self._db.execute(
                "UPDATE agents SET ended_at=? WHERE ended_at IS NULL AND (boot IS NULL OR boot<>?)",
                (stamp, boot)).rowcount

    # paired tablets (phase 3). Only a token's SHA-256 is stored, and it never leaves the store.
    _DEVICE_PUBLIC = "id, name, paired_at, last_seen"

    def add_device(self, name: str, token_hash: str) -> dict:
        row = {"id": f"d-{secrets.token_hex(4)}", "name": name, "paired_at": _now(), "last_seen": None}
        with self._lock, self._db:
            self._db.execute("INSERT INTO devices VALUES(?,?,?,?,?)",
                             (row["id"], name, token_hash, row["paired_at"], None))
        return row

    def device_by_hash(self, token_hash: str) -> dict | None:
        with self._lock:
            r = self._db.execute(f"SELECT {self._DEVICE_PUBLIC} FROM devices WHERE token_hash=?",
                                 (token_hash,)).fetchone()
        return dict(r) if r else None

    def touch_device(self, id: str, stamp: str) -> None:
        if self.readonly:
            return
        with self._lock, self._db:
            self._db.execute("UPDATE devices SET last_seen=? WHERE id=?", (stamp, id))

    def list_devices(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(f"SELECT {self._DEVICE_PUBLIC} FROM devices ORDER BY paired_at, id").fetchall()
        return [dict(r) for r in rows]

    def delete_device(self, id: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM devices WHERE id=?", (id,)).rowcount > 0

    # measurement requests (an agent asks; the owner answers on the tablet, spec §7.3)
    @staticmethod
    def _request(r) -> dict:
        d = dict(r)
        d["items"] = json.loads(d["items"])
        return d

    def add_request(self, title: str, items: list[str], project: str | None,
                    project_name: str | None) -> dict:
        now = datetime.now()
        rid = f"r-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        with self._lock, self._db:
            self._db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)",
                             (rid, title, json.dumps(items, ensure_ascii=False), project, project_name,
                              now.replace(microsecond=0).isoformat(), "open", None))
        return self.get_request(rid)

    def get_request(self, id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM requests WHERE id=?", (id,)).fetchone()
        return self._request(r) if r else None

    def list_requests(self) -> list[dict]:
        """Newest first."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM requests ORDER BY created DESC, id DESC").fetchall()
        return [self._request(r) for r in rows]

    def set_request_answered(self, id: str, entry_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE requests SET status='answered', answer_entry=? WHERE id=?",
                             (entry_id, id))

    def request_for_entry(self, entry_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM requests WHERE answer_entry=?", (entry_id,)).fetchone()
        return self._request(r) if r else None

    # what the tablet sent, by its own id: a resend after a lost response is never written twice
    def sent_entry(self, client_id: str) -> str | None:
        with self._lock:
            r = self._db.execute("SELECT entry_id FROM tablet_sends WHERE client_id=?", (client_id,)).fetchone()
        return r["entry_id"] if r else None

    def record_sent(self, client_id: str, entry_id: str) -> None:
        if self.readonly:
            return
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO tablet_sends VALUES(?,?,?)", (client_id, entry_id, _now()))

    # IGNORE on the tablet: hidden, never deleted (spec §3.3)
    def hide(self, kind: str, ref: str) -> None:
        if kind not in HIDDEN_KINDS:
            raise ValueError(f"unknown kind: {kind}")
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO tablet_hidden VALUES(?,?,?)", (kind, ref, _now()))

    def unhide(self, kind: str, ref: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM tablet_hidden WHERE kind=? AND ref=?",
                                    (kind, ref)).rowcount > 0

    def hidden_refs(self, kind: str) -> set[str]:
        with self._lock:
            rows = self._db.execute("SELECT ref FROM tablet_hidden WHERE kind=?", (kind,)).fetchall()
        return {r["ref"] for r in rows}

    # fts
    def index_entry(self, e: Entry) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM fts WHERE kind='entry' AND ref=?", (e.id,))
            self._db.execute("INSERT INTO fts(kind,ref,date,body) VALUES('entry',?,?,?)",
                             (e.id, e.ts.strftime("%Y-%m-%d"), e.text))

    def _delete_note_rows(self, rel: str) -> None:
        """Caller holds the lock and a transaction."""
        escaped = re.sub(r"([\\%_])", r"\\\1", rel)
        self._db.execute(
            "DELETE FROM fts WHERE kind='note' AND (ref=? OR ref LIKE ? ESCAPE '\\')",
            (rel, escaped + "#%"))

    def index_note_file(self, brain: Path, rel: str) -> None:
        """(Re)index one note, one row per heading section. Bytes that aren't UTF-8 are
        replaced, so a stray encoding never blocks the rest of the note."""
        text = (Path(brain) / rel).read_text(encoding="utf-8", errors="replace")
        sections: list[tuple[str, list[str]]] = [(rel, [])]
        for line in text.splitlines():
            m = _HEADING.match(line)
            if m:
                sections.append((f"{rel}#{m.group(1)}", [line]))
            else:
                sections[-1][1].append(line)
        with self._lock, self._db:
            self._delete_note_rows(rel)
            for ref, lines in sections:
                body = "\n".join(lines).strip()
                if body:
                    self._db.execute(
                        "INSERT INTO fts(kind,ref,date,body) VALUES('note',?,NULL,?)",
                        (ref, body))

    def drop_note_file(self, rel: str) -> None:
        with self._lock, self._db:
            self._delete_note_rows(rel)

    def reindex_notes(self, brain: Path) -> list[str]:
        """Drop every note row and index every note again. Entries are untouched. A file
        that fails is logged, listed in the result and skipped; the rest still get indexed."""
        brain = Path(brain)
        with self._lock, self._db:
            self._db.execute("DELETE FROM fts WHERE kind='note'")
        failed: list[str] = []
        for rel in note_files(brain):
            try:
                self.index_note_file(brain, rel)
            except Exception as e:
                log.warning("could not index note %s: %s", rel, e)
                failed.append(f"{rel} ({e})")
        return failed

    def reindex_all(self, brain: Path) -> list[str]:
        brain = Path(brain)
        entries = read_log(brain)
        with self._lock:
            with self._db:
                self._db.execute("DELETE FROM fts")
            for e in entries:
                self.add_entry(e)
                self.index_entry(e)
        return self.reindex_notes(brain)

    def indexed_entry_ids(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT ref FROM fts WHERE kind='entry'")}

    def fts_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM fts").fetchone()[0]

    def search(self, fts_query: str, limit: int) -> list[Hit]:
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, ref, date, body, snippet(fts, 3, '[', ']', '…', 12) AS snip"
                " FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
                (fts_query, limit)).fetchall()
        return [Hit(r["kind"], r["ref"], r["date"], r["snip"], r["body"]) for r in rows]

    # export / restore
    def export_items(self, brain: Path) -> None:
        out = Path(brain) / "_items"
        out.mkdir(parents=True, exist_ok=True)
        items = sorted(self.list_items(), key=lambda i: i.id)
        with open(out / "items.json", "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps([asdict(i) for i in items], indent=2, ensure_ascii=False) + "\n")
        groups: dict[str, list[Item]] = {}
        for i in items:
            groups.setdefault(i.project or i.area, []).append(i)
        now = datetime.now()

        def is_open(i: Item) -> bool:  # an expired snooze is open again, as in Today
            return i.status in ("open", "to_read") or (
                i.status == "snoozed" and not snooze_active(i, now))

        for group, members in sorted(groups.items()):
            lines = [f"# {group}", ""]
            for i in sorted(members, key=lambda i: (i.due or "9999", i.id)):
                if not is_open(i):
                    continue
                due = f" (due {i.due})" if i.due else ""
                lines.append(f"- [ ] {i.text}{due} (from {i.source_entry})")
            with open(out / f"{group}.md", "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
        self.export_proposals(brain)

    def export_proposals(self, brain: Path) -> None:
        """Write every pending proposal to brain/_items/proposals.json (deterministic), so a
        rebuilt index can restore them. Committed with the next hub commit."""
        out = Path(brain) / "_items"
        out.mkdir(parents=True, exist_ok=True)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM proposals WHERE status='proposed' ORDER BY seq, id").fetchall()
        data = [{"id": r["id"], "seq": r["seq"], "entry_id": r["entry_id"], "ptype": r["ptype"],
                 "payload": json.loads(r["payload"]), "warnings": json.loads(r["warnings"])}
                for r in rows]
        text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        path = out / "proposals.json"
        if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
            return  # unchanged: don't touch the file
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def restore_proposals_if_empty(self, brain: Path) -> int:
        """Rebuild pending proposals from brain/_items/proposals.json when the proposals table
        has no rows at all (a rebuilt index). A corrupt file raises ValueError naming it."""
        path = Path(brain) / "_items" / "proposals.json"
        with self._lock:
            if self._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] or not path.exists():
                return 0
            rows = _load_json_list(path)
            try:
                values = [(d["id"], int(d["seq"]), d["entry_id"], d["ptype"],
                           json.dumps(d["payload"]), "proposed", json.dumps(d.get("warnings", [])))
                          for d in rows]
                with self._db:
                    self._db.executemany("INSERT INTO proposals VALUES(?,?,?,?,?,?,?)", values)
            except (KeyError, TypeError, ValueError, sqlite3.Error) as e:
                raise ValueError(f"{path} is damaged ({e!r}); proposals were not restored") from None
        return len(rows)

    def export_tablet(self, brain: Path) -> None:
        """Requests and IGNOREs to brain/_items/requests.json and tablet.json (deterministic,
        rewritten only when changed), so a rebuilt index keeps them. Paired devices are not
        exported: their token hashes stay in hub.db, out of the brain's backup (phase 3 decisions)."""
        out = Path(brain) / "_items"
        out.mkdir(parents=True, exist_ok=True)
        with self._lock:  # snapshot and write together: two exports at once can't leave a stale file
            requests = sorted(self.list_requests(), key=lambda r: r["id"])
            hidden = [dict(r) for r in self._db.execute(
                "SELECT kind, ref, hidden_at FROM tablet_hidden ORDER BY kind, ref").fetchall()]
            _write_if_changed(out / "requests.json", requests)
            _write_if_changed(out / "tablet.json", {"hidden": hidden})

    def restore_tablet_if_empty(self, brain: Path) -> int:
        """Rebuild requests and IGNOREs from their exports, each only into an empty table. A
        damaged file raises ValueError naming it; nothing is restored from it."""
        items, restored = Path(brain) / "_items", 0
        req_path, tab_path = items / "requests.json", items / "tablet.json"
        with self._lock:
            if not self._db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] and req_path.exists():
                rows = _load_json_list(req_path)
                try:
                    values = [(d["id"], d["title"], json.dumps(d["items"], ensure_ascii=False), d.get("project"),
                               d.get("project_name"), d["created"], d["status"], d.get("answer_entry"))
                              for d in rows]
                    with self._db:
                        self._db.executemany("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)", values)
                except (KeyError, TypeError, AttributeError, sqlite3.Error) as e:
                    raise ValueError(f"{req_path} is damaged ({e!r}); requests were not restored") from None
                restored += len(rows)
            if not self._db.execute("SELECT COUNT(*) FROM tablet_hidden").fetchone()[0] and tab_path.exists():
                try:
                    data = json.loads(tab_path.read_text(encoding="utf-8"))
                    values = [(h["kind"], h["ref"], h["hidden_at"]) for h in data["hidden"]]
                    with self._db:
                        self._db.executemany("INSERT INTO tablet_hidden VALUES(?,?,?)", values)
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as e:
                    raise ValueError(f"{tab_path} is damaged ({e!r}); ignored items were not restored") from None
                restored += len(values)
        return restored

    def restore_items_if_empty(self, brain: Path) -> int:
        """Rebuild the items table from brain/_items/items.json when it is empty. A corrupt
        file raises ValueError naming it, and nothing is restored."""
        path = Path(brain) / "_items" / "items.json"
        with self._lock:
            if self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0] or not path.exists():
                return 0
            rows = _load_json_list(path)
            try:
                values = [[d.get(k) for k in _ITEM_FIELDS] for d in rows]
                with self._db:
                    for v in values:
                        self._db.execute(
                            f"INSERT OR REPLACE INTO items({','.join(_ITEM_FIELDS)})"
                            f" VALUES({','.join('?' * len(_ITEM_FIELDS))})", v)
            except (AttributeError, sqlite3.Error) as e:
                raise ValueError(f"{path} is damaged ({e}); items were not restored") from None
        return len(rows)


def _write_if_changed(path: Path, data) -> None:
    """Deterministic JSON, rewritten only when it changed, and atomically: a reader (or a crash)
    never sees half a file."""
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
        return
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _load_json_list(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{path} is damaged ({e}); nothing was restored from it") from None
    if not isinstance(data, list):
        raise ValueError(f"{path} is damaged (not a JSON list); nothing was restored from it")
    return data


def note_files(brain: Path) -> list[str]:
    """Brain-relative paths of every indexable note, sorted. Dot-folders, log/ and _items/
    are pruned, not walked."""
    brain = Path(brain)
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(brain):
        rel_dir = Path(dirpath).relative_to(brain).as_posix()
        top = rel_dir == "."
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and not (top and d.lower() in _SKIP_DIRS)]
        for name in filenames:
            rel = name if top else f"{rel_dir}/{name}"
            if is_note_path(rel):
                out.append(rel)
    return sorted(out)


def reindex_changed(store: Store, brain: Path, repo, sha: str | None) -> list[str]:
    """Bring the notes index in line with one commit: re-index the notes it changed and drop
    the rows of notes it deleted. Per-file failures are logged and returned, never raised."""
    failed: list[str] = []
    if not sha:
        return failed
    brain = Path(brain)
    for rel in repo.changed_files(sha):
        if not is_note_path(rel):
            continue
        try:
            if (brain / rel).is_file():
                store.index_note_file(brain, rel)
            else:
                store.drop_note_file(rel)
        except Exception as e:
            log.warning("could not re-index %s after %s: %s", rel, sha[:10], e)
            failed.append(rel)
    return failed


def commit_hook(store: Store, brain: Path, repo,
                notify: Callable[[], None] = lambda: None) -> Callable[[str | None], None]:
    """The single place every hub commit reports to: `on_commit(sha)` re-indexes the notes
    that commit touched, then calls `notify` (the backup scheduler). An indexing failure is
    logged and never undoes or fails the commit."""
    def on_commit(sha: str | None) -> None:
        if not sha:  # nothing was committed: nothing to index or push
            return
        try:
            reindex_changed(store, brain, repo, sha)
        except Exception:
            log.exception("could not re-index notes after commit %s", sha)
        notify()
    return on_commit
