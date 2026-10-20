"""SQLite index and state. Everything here is rebuildable from the brain/ folder."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from .models import Entry, Filing, Hit, Item, Proposal
from .rawlog import read_log

_ITEM_FIELDS = [f.name for f in fields(Item)]
_SKIP_DIRS = ("log", "_items")
_HEADING = re.compile(r"^#+\s+(.*?)\s*$")

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
    def __init__(self, db_path: Path):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")

    def init_schema(self) -> None:
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    # entries
    def add_entry(self, e: Entry) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO entries VALUES(?,?,?,?)",
                             (e.id, e.ts.isoformat(), e.source, e.text))

    def get_entry(self, id: str) -> Entry | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM entries WHERE id=?", (id,)).fetchone()
        return _entry(r) if r else None

    def list_entries(self, day: str | None = None, before: str | None = None,
                     limit: int = 200) -> list[Entry]:
        sql, args = "SELECT * FROM entries WHERE 1=1", []
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

    def unfinished_filings(self) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT e.id FROM entries e LEFT JOIN filings f ON f.entry_id=e.id"
                " WHERE f.status IN ('queued','running')"
                " OR (f.entry_id IS NULL AND e.source!='tablet')"
                " ORDER BY e.ts, e.id").fetchall()
        return [r["id"] for r in rows]

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

    # fts
    def index_entry(self, e: Entry) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM fts WHERE kind='entry' AND ref=?", (e.id,))
            self._db.execute("INSERT INTO fts(kind,ref,date,body) VALUES('entry',?,?,?)",
                             (e.id, e.ts.strftime("%Y-%m-%d"), e.text))

    def index_note_file(self, brain: Path, rel: str) -> None:
        text = (Path(brain) / rel).read_text(encoding="utf-8")
        sections: list[tuple[str, list[str]]] = [(rel, [])]
        for line in text.splitlines():
            m = _HEADING.match(line)
            if m:
                sections.append((f"{rel}#{m.group(1)}", [line]))
            else:
                sections[-1][1].append(line)
        escaped = re.sub(r"([\\%_])", r"\\\1", rel)
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM fts WHERE kind='note' AND (ref=? OR ref LIKE ? ESCAPE '\\')",
                (rel, escaped + "#%"))
            for ref, lines in sections:
                body = "\n".join(lines).strip()
                if body:
                    self._db.execute(
                        "INSERT INTO fts(kind,ref,date,body) VALUES('note',?,NULL,?)",
                        (ref, body))

    def reindex_all(self, brain: Path) -> None:
        brain = Path(brain)
        entries = read_log(brain)
        with self._lock:
            with self._db:
                self._db.execute("DELETE FROM fts")
            for e in entries:
                self.add_entry(e)
                self.index_entry(e)
            for path in sorted(brain.rglob("*.md")):
                rel = path.relative_to(brain).as_posix()
                if rel.split("/")[0] in _SKIP_DIRS or rel.startswith(".git/"):
                    continue
                self.index_note_file(brain, rel)

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
        for group, members in sorted(groups.items()):
            lines = [f"# {group}", ""]
            for i in sorted(members, key=lambda i: (i.due or "9999", i.id)):
                if i.status not in ("open", "to_read"):
                    continue
                due = f" (due {i.due})" if i.due else ""
                lines.append(f"- [ ] {i.text}{due} (from {i.source_entry})")
            with open(out / f"{group}.md", "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")

    def restore_items_if_empty(self, brain: Path) -> int:
        path = Path(brain) / "_items" / "items.json"
        with self._lock:
            if self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0] or not path.exists():
                return 0
            rows = json.loads(path.read_text(encoding="utf-8"))
            with self._db:
                for d in rows:
                    self._db.execute(
                        f"INSERT OR REPLACE INTO items({','.join(_ITEM_FIELDS)})"
                        f" VALUES({','.join('?' * len(_ITEM_FIELDS))})",
                        [d.get(k) for k in _ITEM_FIELDS])
        return len(rows)
