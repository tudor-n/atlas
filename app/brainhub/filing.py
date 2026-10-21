"""Filing: a headless Claude Code agent files each entry into the brain and proposes items.

The agent works in an isolated git worktree of brain/ (data/worktrees/<entry id>). Its
commit is cherry-picked onto brain's main branch, so concurrent captures and owner edits in
the main checkout are never touched. Proposals are stored for the owner to confirm later.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from .brainmap import BrainMap, default_map, ensure_folders, load_map, slugify
from .config import Paths, load_settings
from .gitrepo import BrainRepo
from .models import Entry, Filing, Item
from .store import Store

log = logging.getLogger(__name__)

FILING_SCHEMA: dict = json.loads(
    (Path(__file__).parent / "schemas" / "filing.schema.json").read_text(encoding="utf-8"))

ALLOWED_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]
MAX_TURNS = 25
PROTECTED = {"log", "_items", "map.json", ".gitattributes", ".gitignore"}
PROTECTED_WARNING = "agent tried to edit protected files"
FIX_IN_PLACE = "Fix the files in place; earlier text from this entry may already exist."
# Startup never mass-files a backlog it can't account for (e.g. after a lost index): past this
# many entries with no filing record and no filing commit, they wait for the owner's Retry.
MAX_AUTO_REQUEUE = 10
UNFILED_AT_STARTUP = "not filed yet (found at startup) — press Retry when ready"
# English names regardless of the process locale (calendar.day_name follows LC_TIME).
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}")
_ENTRY_ID = re.compile(r"e-[0-9]{8}-[0-9]{6}-[0-9a-f]{4}")


# -- prompt ------------------------------------------------------------------
def _item_line(i: Item) -> str:
    return f"- {i.id} [{i.kind}] {i.text} ({i.project or '-'}, {i.person or '-'}, {i.due or '-'})"


def build_filing_prompt(entry: Entry, m: BrainMap, open_items: list[Item],
                        correction: str | None = None, previous: dict | None = None,
                        fix_in_place: bool = False) -> str:
    ts = entry.ts
    lines = [
        f"Entry id: {entry.id}",
        f"Entry written: {ts:%Y-%m-%d %H:%M} ({_WEEKDAYS[ts.weekday()]})",
        "Resolve all relative dates against the entry timestamp above, not today.",
        "",
        "Brain map (JSON):",
        json.dumps(asdict(m), ensure_ascii=False, indent=2),
        "",
        "Open items (id [kind] text (project, person, due)):",
        *([_item_line(i) for i in open_items] or ["(none)"]),
    ]
    if correction:
        lines += ["", "Correction from the owner. File this entry again with it applied:", correction]
    if fix_in_place:
        lines += ["", "This entry was already filed once and that filing is still in the notes.",
                  FIX_IN_PLACE]
    if previous:
        lines += ["", "Your previous filing of this entry:",
                  json.dumps(previous, ensure_ascii=False, indent=2)]
    lines += ["", "The entry, verbatim:", "<<<ENTRY", entry.text, "ENTRY>>>"]
    return "\n".join(lines)


# -- parsing -----------------------------------------------------------------
def valid_date(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        if _DATE.fullmatch(value):
            date.fromisoformat(value)
            return True
        if _DATETIME.fullmatch(value):
            datetime.fromisoformat(value)
            return True
    except ValueError:
        pass
    return False


def parse_filing_result(raw: dict, m: BrainMap, open_item_ids: set[str]
                        ) -> tuple[str, list[str], list[tuple[str, dict, list[str]]]]:
    """Turn the agent's JSON into (synopsis, files, proposals). Problems become warnings."""
    new_nodes = raw.get("new_nodes", [])
    proposed: dict[str, set[str]] = {}
    for n in new_nodes:
        proposed.setdefault(n["type"], set()).add(slugify(n["name"]))
    courses = {c["slug"] for c in m.courses} | proposed.get("course", set())
    projects = {p["slug"] for p in m.projects} | proposed.get("project", set())
    known = {
        "area": {a["slug"] for a in m.areas} | proposed.get("area", set()),
        "project": projects | courses,  # an item may belong to a course as its "project"
        "person": {p["slug"] for p in m.people} | proposed.get("person", set()),
    }
    proposals: list[tuple[str, dict, list[str]]] = []
    for it in raw.get("items", []):
        warnings: list[str] = []
        payload = {"kind": it["kind"], "text": it["text"], "area": it["area"],
                   "project": it.get("project"), "person": it.get("person"),
                   "due": it.get("due"), "remind_at": it.get("remind_at")}
        area = slugify(payload["area"]) if isinstance(payload["area"], str) else ""
        if area in known["area"]:
            payload["area"] = area
        else:
            warnings.append(f"unknown area '{payload['area']}', filed under general")
            payload["area"] = "general"
        for key in ("project", "person"):
            value = payload[key]
            if value is None:
                continue
            slug = slugify(value)
            if slug in known[key]:
                payload[key] = slug
            else:
                warnings.append(f"unknown {key} '{value}'")
                payload[key] = None
        for key in ("due", "remind_at"):
            value = payload[key]
            if value is not None and not valid_date(value):
                warnings.append(f"could not read date '{value}'")
                payload[key] = None
        proposals.append(("item", payload, warnings))
    for u in raw.get("updates", []):
        payload = {"item_id": u["item_id"], "status": u["status"]}
        if "note" in u:
            payload["note"] = u["note"]
        proposals.append(("update", payload, [] if u["item_id"] in open_item_ids else ["unknown item id"]))
    for n in new_nodes:
        parent, warnings = n.get("parent"), []
        if parent is not None:
            slug = slugify(parent) if isinstance(parent, str) else ""
            if slug and slug in projects | courses:  # in the map, or proposed alongside
                parent = slug
            else:
                warnings.append(f"unknown parent '{parent}' dropped")
                parent = None
        proposals.append(("node", {"type": n["type"], "name": n["name"], "parent": parent}, warnings))
    return raw.get("synopsis", ""), list(raw.get("files", [])), proposals


# -- worker ------------------------------------------------------------------
def _check_entry_id(entry_id: str) -> None:
    """Entry ids name worktree folders: reject anything that isn't one before building a path."""
    if not isinstance(entry_id, str) or not _ENTRY_ID.fullmatch(entry_id):
        raise ValueError(f"invalid entry id: {entry_id!r}")


def _warn(proposals: list[tuple[str, dict, list[str]]], warning: str) -> None:
    """Attach a warning to the first proposal, or add a warning-only proposal so it is seen."""
    if proposals:
        proposals[0][2].append(warning)
    else:
        proposals.append(("update", {}, [warning]))


def sweep_message(changed: list[str]) -> str:
    """Message for committing whatever is pending in the main checkout: "owner edits", unless
    every change is the hub's own _items/ mirror (proposals.json after a filing or dismiss)."""
    return "owner edits" if any(not p.startswith("_items/") for p in changed) else "sync _items"


def protected_tops(changed: list[str]) -> list[str]:
    """Top-level names among `changed` the agent must not touch: the protected set (any case,
    since Windows folders are case-insensitive) and every dot-folder or dot-file (.git*,
    .claude, .obsidian...)."""
    tops = {p.split("/")[0] for p in changed}
    return sorted(t for t in tops if t.lower() in PROTECTED or t.startswith("."))


def _prepare_worktree(root: Path, m: BrainMap) -> None:
    """Git carries no empty folders: recreate the full structure, custom areas included."""
    ensure_folders(root, m)
    for a in m.areas:
        (root / a["slug"]).mkdir(parents=True, exist_ok=True)


class FilingWorker:
    def __init__(self, paths: Paths, store: Store, repo: BrainRepo, runner,
                 broadcast: Callable[[str, dict], Awaitable[None]],
                 on_commit: Callable[[str | None], None]):
        """on_commit(sha) runs (in a thread) after every commit this worker lands on main: it
        re-indexes the notes that commit touched and schedules a backup (store.commit_hook)."""
        self.paths = paths
        self.store = store
        self.repo = repo
        self.runner = runner
        self.broadcast = broadcast
        self.on_commit = on_commit
        self.queue: asyncio.Queue = asyncio.Queue()
        self._lock = asyncio.Lock()  # one filing or correction at a time

    def _remove_worktree(self, path: Path) -> None:
        """Unregister and delete a worktree, including a plain leftover folder git doesn't know."""
        self.repo.worktree_remove(path)
        if path.exists() and path.resolve().parent == (self.paths.data / "worktrees").resolve():
            shutil.rmtree(path)

    def mark_queued(self, entry_id: str) -> None:
        """Sync store write; safe to run in a thread."""
        f = self.store.get_filing(entry_id) or Filing(entry_id, "queued")
        f.status = "queued"
        self.store.upsert_filing(f)

    def enqueue(self, entry_id: str) -> None:
        """Loop-only convenience: mark queued and put on the queue."""
        self.mark_queued(entry_id)
        self.queue.put_nowait(entry_id)

    def recover_from_history(self) -> int:
        """Entries with no filing row whose `file <id>:` commit is in brain's history were
        filed before the index was lost: record them as done, never file them again."""
        missing = self.store.unfiled_entry_ids()
        if not missing:
            return 0
        commits = self.repo.filing_commits()
        n = 0
        for entry_id in missing:
            found = commits.get(entry_id)
            if found:
                sha, synopsis, files = found
                self.store.upsert_filing(Filing(entry_id, "done", synopsis, files, commit=sha))
                n += 1
        return n

    def requeue_unfinished(self) -> list[str]:
        """Mark unfinished filings queued and return their ids; the caller puts them on the
        queue from the event loop (asyncio.Queue is not thread-safe). Interrupted filings are
        always re-queued; entries never filed are queued only when there are at most
        MAX_AUTO_REQUEUE of them, otherwise they are marked failed for the owner to retry."""
        leftovers = self.paths.data / "worktrees"
        if leftovers.is_dir():
            for d in leftovers.iterdir():
                if d.is_dir():
                    try:
                        self._remove_worktree(d)
                    except Exception:  # never block startup; file_entry retries the removal
                        log.exception("could not remove leftover worktree %s", d)
        ids = self.store.unfinished_filings()
        unfiled = self.store.unfiled_entry_ids()
        if len(unfiled) > MAX_AUTO_REQUEUE:
            log.warning("%d unfiled entries at startup: marked failed, waiting for Retry", len(unfiled))
            for entry_id in unfiled:
                self.store.upsert_filing(Filing(entry_id, "failed", error=UNFILED_AT_STARTUP))
            skip = set(unfiled)
            ids = [i for i in ids if i not in skip]
        for entry_id in ids:
            self.mark_queued(entry_id)
        return ids

    async def run_forever(self) -> None:
        while True:
            entry_id = await self.queue.get()
            try:
                await self.file_entry(entry_id)
            except Exception:
                log.exception("filing %s crashed", entry_id)
            finally:
                self.queue.task_done()

    async def file_entry(self, entry_id: str, correction: str | None = None,
                         previous: dict | None = None) -> None:
        _check_entry_id(entry_id)
        t = asyncio.to_thread
        async with self._lock:
            # A re-run after this entry's commit already landed (a crash after the
            # cherry-pick, a retry) must fix the notes in place, not append the content again.
            old = await t(self.store.get_filing, entry_id)
            landed = bool(old and old.commit and await t(self.repo.in_history, old.commit))
            await self._file_entry(entry_id, correction, previous, fix_in_place=landed)

    async def _file_entry(self, entry_id: str, correction: str | None,
                          previous: dict | None, fix_in_place: bool = False) -> None:
        """The filing itself. The caller holds self._lock."""
        t = asyncio.to_thread
        wt_path = self.paths.data / "worktrees" / entry_id
        old = await t(self.store.get_filing, entry_id)
        # The last landed commit is kept until a new one lands, so a failed re-run still knows it.
        filing = Filing(entry_id, "running", commit=old.commit if old else None,
                        attempts=old.attempts if old else 0)
        done = False
        try:
            entry = await t(self.store.get_entry, entry_id)
            if entry is None:
                raise ValueError(f"unknown entry: {entry_id}")
            # 1. main checkout: commit owner edits (and fresh captures) as they are
            pending = await t(self.repo.changed_paths)
            if pending:
                await t(self.on_commit, await t(self.repo.commit_all, sweep_message(pending)))
            # 2-3. a re-run never duplicates proposals
            await t(self.store.delete_proposals, entry_id)
            filing.attempts += 1
            await t(self.store.upsert_filing, filing)
            await self.broadcast("filing.started", {"entry_id": entry_id})
            # 4. isolated worktree at main HEAD
            if wt_path.exists():
                await t(self._remove_worktree, wt_path)
            wt = await t(self.repo.worktree_add, wt_path)
            m = await t(load_map, self.paths.brain) or default_map()
            await t(_prepare_worktree, wt_path, m)
            # 5. run the agent
            open_items = await t(self.store.list_items, status=["open", "to_read"])
            settings = await t(load_settings, self.paths)
            raw = await self.runner.run_json(
                build_filing_prompt(entry, m, open_items, correction, previous, fix_in_place),
                cwd=wt_path, model=settings["filing_model"],
                system_prompt_file=self.paths.prompts / "filing.md",
                allowed_tools=ALLOWED_TOOLS, json_schema=FILING_SCHEMA,
                max_turns=MAX_TURNS, timeout_s=settings["filing_timeout_s"])
            synopsis, files, proposals = parse_filing_result(raw, m, {i.id for i in open_items})
            # 6. undo any edit to protected paths, in the worktree only
            changed = await t(wt.changed_paths)
            protected = protected_tops(changed)
            if protected:
                await t(wt.restore_paths, protected)
                _warn(proposals, PROTECTED_WARNING)
            # 7. commit in the worktree, cherry-pick onto main. Always a commit, even with no
            # file changes: "file <id>:" in history is how a rebuilt index knows it was filed.
            sha = await t(wt.commit_all, f"file {entry_id}: {synopsis[:60]}", True)
            if sha:
                new = await t(self.repo.cherry_pick, sha)
                if new is None:
                    raise RuntimeError("conflict with concurrent edits — retry")
                filing.commit = new  # recorded at once: a later failure must not lose it
                await t(self.store.upsert_filing, filing)
                await t(self.on_commit, new)  # re-indexes the notes it touched
            # 8. proposals and outcome
            await t(self.store.add_proposals, entry_id, proposals)
            filing.status, filing.synopsis, filing.files = "done", synopsis, files
            await t(self.store.upsert_filing, filing)
            done = True
        except Exception as exc:
            # Never discard changes in the main checkout: the agent never touched it.
            log.exception("filing %s failed", entry_id)
            filing.status, filing.error = "failed", str(exc)[:500]
            await t(self.store.upsert_filing, filing)
            await self.broadcast("filing.failed", {"entry_id": entry_id, "error": filing.error})
        finally:
            # 9. always clean up the worktree; mirror pending proposals into brain/_items
            try:
                await t(self._remove_worktree, wt_path)
            except Exception:
                log.exception("could not remove worktree %s", wt_path)
            try:
                await t(self.store.export_proposals, self.paths.brain)
            except Exception:
                log.exception("could not export proposals")
        if done:  # outside the try: a broadcast error can't turn a done filing into failed
            await self.broadcast("filing.done", {"entry_id": entry_id})

    async def correct(self, entry_id: str, text: str, *, queued: bool = False) -> None:
        """Re-file with the owner's correction. queued=True: the caller (the /correct route)
        already marked this filing queued, so the card stops offering ✓ at once."""
        _check_entry_id(entry_id)
        t = asyncio.to_thread
        async with self._lock:
            f = await t(self.store.get_filing, entry_id)
            if f and (f.status == "running" or (f.status == "queued" and not queued)):
                raise ValueError("filing in progress")
            try:
                proposals = await t(self.store.list_proposals, entry_id=entry_id)
                previous = {
                    "synopsis": f.synopsis if f else "",
                    "proposals": [{"ptype": p.ptype, "payload": p.payload, "warnings": p.warnings}
                                  for p in proposals],
                }
                reverted = None
                if f and f.commit and f.commit == await t(self.repo.head):
                    reverted = await t(self.repo.revert, f.commit)
                    if reverted:
                        await t(self.on_commit, reverted)
            except Exception as exc:  # never leave the filing stuck at "queued"
                if f:
                    f.status, f.error = "failed", str(exc)[:500]
                    await t(self.store.upsert_filing, f)
                raise
            # Reverted: file afresh. Not reverted (later commits on top): fix in place.
            await self._file_entry(entry_id, text, previous, fix_in_place=not reverted)
