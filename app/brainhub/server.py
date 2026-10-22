"""aiohttp app wiring: startup recovery, workers, WebSocket broadcast. Binds 127.0.0.1 only."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import warnings
import webbrowser
from datetime import datetime
from pathlib import Path

from aiohttp import web

from . import holding, osdirs, winwatch
from .agents import AgentTracker
from .backup import BackupPusher
from .claude_integration import default_settings_path
from .claude_runner import ClaudeRunner
from .config import Paths, load_settings
from .filing import FilingWorker, sweep_message
from .gitrepo import BrainRepo
from .rawlog import read_log
from .review import Reviewer
from .rootguard import RootStatus, check_root, read_marker, tz_mismatch
from .routes import add_reduced_routes, add_routes, errors, local_only, readonly_guard, setup_done
from .store import SchemaTooNew, Store, commit_hook
from .usage import UsageStore
from .widget import ArmProbe

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
t = asyncio.to_thread
# Root states the full app can't run on: run_server serves the reduced app for them instead.
UNAVAILABLE = ("missing", "foreign", "newer")
RECHECK_SECONDS = 15.0  # P8: how often a reduced or read-only hub looks at the root again
EXIT_RESTART = 75  # P8: the root is usable again; the shell restarts the hub at once, with no backoff


class RootUnavailable(Exception):
    """The full app can't run on this root (missing, foreign or newer than this code, or read-only
    with an index that can't be read): nothing was created or changed. run_server serves the
    reduced app instead."""

    def __init__(self, status: RootStatus):
        super().__init__(status.message)
        self.status = status


# -- startup -----------------------------------------------------------------
# Every step is wrapped: a failure is logged and listed in /api/state as `startup_error`, and
# the hub keeps serving. Capture only needs the raw log to be writable.
def _clear_stale_git_locks(app: web.Application) -> None:
    """No hub git process runs before startup finishes, so a git lock is a crash leftover."""
    removed = ", ".join(f"brain/{p}" for p in app["repo"].clear_stale_locks())
    if removed:
        log.warning("removed stale %s left by a crash", removed)
        app["startup_errors"].append(
            f"removed stale {removed} left by a crash (nothing else was changed)")


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


def _flush_held(app: web.Application) -> None:
    """Captures held while the root wasn't writable go into the raw log with their own id and ts;
    the raw log step right after indexes them. Each is marked queued as a fresh capture is, so
    the filing queue step files them all (the cap on never-filed entries doesn't apply)."""
    for entry_id in holding.flush(osdirs.state_dir(), app["paths"].brain, app["store"],
                                  app["startup_errors"]):
        app["worker"].mark_queued(entry_id)


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
    step("git lock", _clear_stale_git_locks, app)
    step("agents", app["agents"].end_previous_boot)
    step("git setup", repo.init)
    step("owner edits", _commit_owner_edits, app)
    step("git attributes", _ensure_gitattributes, app)
    step("restoring items", store.restore_items_if_empty, paths.brain)
    step("restoring proposals", store.restore_proposals_if_empty, paths.brain)
    step("held captures", _flush_held, app)
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


def _request_exit(app: web.Application, code: int) -> None:
    """Stop the server gracefully; run_server exits with `code`."""
    with warnings.catch_warnings():  # aiohttp warns on changing a started app; read only after the stop
        warnings.simplefilter("ignore", DeprecationWarning)
        app["exit_code"] = code
    app["stop"].set()


async def _watch_root(app: web.Application) -> None:
    """Reduced or read-only mode (P8): look at the root again every recheck_interval seconds.
    Once it is ok, stop with EXIT_RESTART, so the shell restarts the hub at once and the full
    app starts fresh (and flushes the held captures)."""
    while True:
        await asyncio.sleep(app["recheck_interval"])
        try:
            status = await t(check_root, app["paths"])
        except Exception:
            log.exception("root re-check failed")
            continue
        if status.state == "ok":
            log.warning("the root %s is usable again: stopping to restart in full", app["paths"].root)
            _request_exit(app, EXIT_RESTART)
            return


async def on_startup(app: web.Application) -> None:
    if app["root_status"].state == "readonly":
        # Every startup step writes (git, hub.db, the brain), and so do the workers, the backup
        # and the agents prune: none of them runs. Only the watch for a writable root does.
        app["tasks"] = [asyncio.create_task(_watch_root(app))]
        return
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
    if "store" in app:  # the reduced app has none
        app["store"].close()


CORRUPTION = ("malformed", "not a database", "file is encrypted")  # what SQLite says about real damage


def _corrupt(e: sqlite3.DatabaseError) -> bool:
    return any(m in str(e).lower() for m in CORRUPTION)


def _open_store(paths: Paths, problems: list[str], *, readonly: bool = False) -> Store:
    """hub.db is a rebuildable index: a damaged one is moved aside and rebuilt from brain/.
    Damaged means real corruption only: SQLite says malformed, not a database or encrypted, or
    PRAGMA quick_check finds damage. Anything else is re-raised and nothing is moved: a newer
    schema (SchemaTooNew), a locked file, a read-only root, a file that can't be opened (dual-boot
    design §6.5 item 2). With readonly=True (a read-only root) nothing is ever moved or written."""
    store = None
    try:
        store = Store(paths.db, readonly=readonly)
        damage = store.quick_check()
        if not damage:
            store.init_schema()
            return store
    except SchemaTooNew:  # not a DatabaseError: never moved aside
        store.close()
        raise
    except sqlite3.DatabaseError as e:
        if store is not None:
            store.close()
        if readonly or not _corrupt(e):
            raise
        reason = str(e)
    else:
        store.close()
        reason = "quick_check: " + "; ".join(damage[:3])
        if readonly:
            raise sqlite3.DatabaseError(f"{paths.db.name} is damaged ({reason})")
    aside = paths.db.with_name(f"hub.db.damaged-{datetime.now():%Y%m%d-%H%M%S}")
    for suffix in ("", "-wal", "-shm"):
        src = paths.db.with_name(paths.db.name + suffix)
        if src.exists():
            src.replace(aside.with_name(aside.name + suffix))
    log.error("damaged %s moved to %s: %s", paths.db, aside, reason)
    problems.append(f"the index {paths.db.name} was damaged ({reason}); moved it to {aside.name} "
                    "and rebuilt it from brain/")
    return Store(paths.db)


def _default_opener(url: str) -> None:
    """Open a validated http(s) URL in the default browser."""
    if sys.platform == "win32":
        os.startfile(url)
    else:
        webbrowser.open(url)


def _warnings(paths: Paths) -> list[str]:
    """Quiet one-line notes for the UI (never popups): time zone, Fast Startup (Windows)."""
    marker = read_marker(paths)
    tz = tz_mismatch(marker) if marker else None
    return ([tz] if tz else []) + winwatch.fast_startup_warnings()


def _run_state(app: web.Application, recheck_interval: float) -> None:
    """What run_server and the root watch share: the exit code (P8) and the stop event."""
    app["exit_code"], app["stop"], app["recheck_interval"] = 0, asyncio.Event(), recheck_interval


def create_app(paths: Paths, *, runner=None, start_workers: bool = True,
               claude_settings_path: Path | None = None, status: RootStatus | None = None,
               recheck_interval: float = RECHECK_SECONDS) -> web.Application:
    # The root is classified before anything touches it: the hub never creates a root. run_server
    # passes the status it took the lock on, so both agree.
    status = status or check_root(paths)
    if status.state in UNAVAILABLE:
        raise RootUnavailable(status)
    readonly = status.state == "readonly"
    if status.state in ("ok", "fresh"):
        paths.data.mkdir(exist_ok=True)  # inside the existing root; never the root itself
    app = web.Application(middlewares=[local_only, errors, readonly_guard] if readonly
                          else [local_only, errors])
    startup_errors: list[str] = []
    settings = load_settings(paths, startup_errors)
    try:
        store = _open_store(paths, startup_errors, readonly=readonly)
    except SchemaTooNew as e:
        raise RootUnavailable(RootStatus("newer", str(e))) from e
    except sqlite3.Error as e:
        if not readonly:
            raise  # locked or can't be opened: run_server reports it; nothing was moved
        raise RootUnavailable(RootStatus("readonly", f"{status.message} Its index {paths.db.name} "
                                                     f"can't be read ({e}), so only capture works "
                                                     "until then.")) from e
    repo = BrainRepo(paths.brain)
    app["sockets"], app["tasks"], app["background"] = set(), [], set()
    app["log_error"], app["start_workers"] = None, start_workers
    app["startup_errors"] = startup_errors
    app["root_status"] = status  # as found at start (setup later turns a fresh root into ok)
    app["warnings"] = _warnings(paths)
    _run_state(app, recheck_interval)

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
    # The port the hub listens on: run_server sets the bound one; default to the settings port.
    app["port"] = settings["port"]
    app["opener"] = _default_opener

    add_routes(app)
    if paths.web.is_dir():  # code-relative; never created under the data root
        app.router.add_static("/static/", paths.web)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app


async def _start_reduced(app: web.Application) -> None:
    if app["root_status"].state != "newer":  # newer data never turns older while this code runs
        app["tasks"] = [asyncio.create_task(_watch_root(app))]


def create_reduced_app(paths: Paths, status: RootStatus, *,
                       recheck_interval: float = RECHECK_SECONDS) -> web.Application:
    """The hub when the full app can't run on the root (dual-boot design §6.5 item 2): the pages,
    the drive banner, and capture into this OS's holding inbox. It never opens hub.db, never runs
    git and never writes under the root. Same middleware as the full app."""
    app = web.Application(middlewares=[local_only, errors])
    app["paths"], app["root_status"] = paths, status
    app["sockets"], app["tasks"], app["background"] = set(), [], set()
    app["warnings"] = _warnings(paths)
    app["port"] = load_settings(paths)["port"]  # run_server sets the bound one
    app["opener"] = _default_opener
    _run_state(app, recheck_interval)
    add_reduced_routes(app)
    if paths.web.is_dir():
        app.router.add_static("/static/", paths.web)
    app.on_startup.append(_start_reduced)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app
