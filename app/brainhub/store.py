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


def is_note_path(rel: str) -> bool:
    """A brain-relative path the notes index covers: a .md file outside log/ and _items/ and
    outside any dot-folder (.git, .obsidian, .trash...)."""
    parts = rel.replace("\\", "/").split("/")
    return (rel.lower().endswith(".md") and parts[0].lower() not in _SKIP_DIRS
            and not any(p.startswith(".") for p in parts))

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
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            self._db.close()  # release the file so a damaged one can be moved aside
            raise

    def init_schema(self) -> None:
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

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
