"""aiohttp app wiring: startup recovery, workers, WebSocket broadcast. Binds 127.0.0.1 only."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .agents import AgentTracker
from .backup import BackupPusher
from .claude_integration import default_settings_path
from .claude_runner import ClaudeRunner
from .config import Paths, load_settings
from .filing import FilingWorker, sweep_message
from .gitrepo import BrainRepo
from .rawlog import read_log
from .review import Reviewer
from .routes import add_routes, errors, local_only, setup_done
from .store import Store, commit_hook
from .usage import UsageStore
from .widget import ArmProbe

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
t = asyncio.to_thread


# -- startup -----------------------------------------------------------------
# Every step is wrapped: a failure is logged and listed in /api/state as `startup_error`, and
# the hub keeps serving. Capture only needs the raw log to be writable.
def _clear_stale_git_lock(app: web.Application) -> None:
    """No hub git process runs before startup finishes, so an index.lock is a crash leftover."""
    lock = app["paths"].brain / ".git" / "index.lock"
    if lock.is_file():
        lock.unlink()
        log.warning("removed a stale %s left by a crash", lock)
        app["startup_errors"].append(
            "removed a stale brain/.git/index.lock left by a crash (nothing else was changed)")


def _commit_owner_edits(app: web.Application) -> None:
    repo = app["repo"]
    pending = repo.changed_paths()
    if pending:  # the notes index is rebuilt in full later in startup
        repo.commit_all(sweep_message(pending))
        app["backup"].notify_commit()


def _ensure_gitattributes(app: web.Application) -> None:
    """Brains set up before .gitattributes existed get it now, in a commit of its own."""
    repo = app["repo"]
    if repo.ensure_gitattributes():
        repo.commit_all("add .gitattributes")
        app["backup"].notify_commit()


def _backfill_log(app: web.Application) -> None:
    """Index every intact raw-log entry the store is missing. Damage is reported, never fixed."""
    store, problems = app["store"], []
    entries = read_log(app["paths"].brain, problems)
    indexed = store.indexed_entry_ids()
    for e in entries:
        try:
            store.add_entry(e)  # idempotent; raises on an id holding a different entry
            if e.id not in indexed:
                store.index_entry(e)
        except Exception as exc:
            problems.append(f"{e.id}: {exc}")
    if problems:
        log.error("raw log problems at startup: %s", problems)
        app["log_error"] = "; ".join(problems)


def _rebuild_notes(app: web.Application) -> None:
    """Notes are tens of MB at most: re-index all of them on every start, so edits made while
    the hub was off (or a deleted note) can never leave recall quoting stale text."""
    for failure in app["store"].reindex_notes(app["paths"].brain):
        app["startup_errors"].append(f"notes index: could not index {failure}")


def _startup_sync(app: web.Application) -> list[str]:
    paths, store, repo = app["paths"], app["store"], app["repo"]
    errors_: list[str] = app["startup_errors"]

    def step(label: str, fn, *args, default=None):
        try:
            return fn(*args)
        except Exception as e:
            log.exception("startup step %r failed", label)
            errors_.append(f"{label}: {e}")
            return default

    step("database", store.init_schema)
    if not setup_done(app):
        return []
    step("git lock", _clear_stale_git_lock, app)
    step("git setup", repo.init)
    step("owner edits", _commit_owner_edits, app)
    step("git attributes", _ensure_gitattributes, app)
    step("restoring items", store.restore_items_if_empty, paths.brain)
    step("restoring proposals", store.restore_proposals_if_empty, paths.brain)
    step("raw log", _backfill_log, app)
    step("notes index", _rebuild_notes, app)
    step("filing history", app["worker"].recover_from_history)
    return step("filing queue", app["worker"].requeue_unfinished, default=[])


async def _prune_agents(app: web.Application) -> None:
    while True:
        await asyncio.sleep(30)
        try:
            if await t(app["agents"].prune):
                await app["broadcast"]("agents.changed", {})
        except Exception:
            logging.getLogger(__name__).exception("agent prune failed")


async def on_startup(app: web.Application) -> None:
    for entry_id in await t(_startup_sync, app):
        app["worker"].queue.put_nowait(entry_id)  # on the loop: asyncio.Queue is not thread-safe
    if app["start_workers"]:
        app["tasks"] = [asyncio.create_task(app["worker"].run_forever()),
                        asyncio.create_task(app["backup"].run_forever()),
                        asyncio.create_task(_prune_agents(app))]


async def on_shutdown(app: web.Application) -> None:
    for ws in list(app["sockets"]):
        await ws.close()


async def on_cleanup(app: web.Application) -> None:
    tasks = [*app["tasks"], *app["background"]]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    app["store"].close()


def _open_store(paths: Paths, problems: list[str]) -> Store:
    """hub.db is a rebuildable index: a damaged one is moved aside and rebuilt from brain/."""
    store = None
    try:
        store = Store(paths.db)
        store.init_schema()
        return store
    except sqlite3.DatabaseError as e:
        if store is not None:
            store.close()
        aside = paths.db.with_name(f"hub.db.damaged-{datetime.now():%Y%m%d-%H%M%S}")
        for suffix in ("", "-wal", "-shm"):
            src = paths.db.with_name(paths.db.name + suffix)
            if src.exists():
                src.replace(aside.with_name(aside.name + suffix))
        log.error("damaged %s moved to %s: %s", paths.db, aside, e)
        problems.append(f"the index {paths.db.name} was damaged ({e}); moved it to {aside.name} "
                        "and rebuilt it from brain/")
        return Store(paths.db)


def _default_opener(url: str) -> None:
    """Open a validated http(s) URL in the default browser."""
    if sys.platform == "win32":
        os.startfile(url)
    else:
        webbrowser.open(url)


def create_app(paths: Paths, *, runner=None, start_workers: bool = True,
               claude_settings_path: Path | None = None) -> web.Application:
    app = web.Application(middlewares=[local_only, errors])
    paths.data.mkdir(parents=True, exist_ok=True)
    startup_errors: list[str] = []
    settings = load_settings(paths, startup_errors)
    store = _open_store(paths, startup_errors)
    repo = BrainRepo(paths.brain)
    app["sockets"], app["tasks"], app["background"] = set(), [], set()
    app["log_error"], app["start_workers"] = None, start_workers
    app["startup_errors"] = startup_errors

    async def broadcast(type_: str, data: dict) -> None:
        extra = [("items.changed", {})] if type_ in ("filing.done", "filing.failed") else []
        for name, payload in [(type_, data), *extra]:
            for ws in list(app["sockets"]):
                try:
                    await ws.send_json({"type": name, "data": payload})
                except Exception:
                    app["sockets"].discard(ws)

    backup = BackupPusher(repo, lambda: load_settings(paths), broadcast)
    app.update(paths=paths, store=store, repo=repo, broadcast=broadcast, backup=backup,
               runner=runner or ClaudeRunner(settings["claude_path"]))
    # Every hub commit (owner edits, filing, revert, confirm, edit, setup, map edit) reports
    # here: the notes it touched are re-indexed, then a backup is scheduled.
    app["on_commit"] = commit_hook(store, paths.brain, repo, backup.notify_commit)
    app["worker"] = FilingWorker(paths, store, repo, app["runner"], broadcast, app["on_commit"])
    app["reviewer"] = Reviewer(paths, store, repo, app["on_commit"])
    app["claude_settings_path"] = Path(claude_settings_path) if claude_settings_path else default_settings_path()
    app["usage"] = UsageStore(paths.data / "usage.json")
    app["agents"] = AgentTracker(store, paths, lambda: load_settings(paths))

    app["arm"] = ArmProbe()
    app["opener"] = _default_opener

    add_routes(app)
    if paths.web.is_dir():  # code-relative; never created under the data root
        app.router.add_static("/static/", paths.web)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app
