"""aiohttp app wiring: startup recovery, workers, WebSocket broadcast. Binds 127.0.0.1 only."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import time
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
from .lines import ensure_line_pools, ensure_lines
from .quotes import ensure_chatter_pools, ensure_quotes, ensure_reply_pools, ensure_workout_pool
from .rawlog import read_log
from .review import Reviewer
from .rootguard import RootStatus, check_root, read_marker, tz_mismatch
from .routes import (add_reduced_routes, add_routes, errors, local_only, readonly_guard, record_usage,
                     repair_legacy_integration, reply_to_filing, setup_done)
from .store import SchemaTooNew, Store, commit_hook
from .tablet import LanSite, Pairing, TabletFeed, lan_guard, watch_lan
from .tablet_routes import add_tablet_routes
from .usage import UsageStore, rate_limits_from_event
from .voice_routes import add_voice_routes, agent_transition, close_voice, setup_voice, voice_tick
from .widget import ArmProbe

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
t = asyncio.to_thread
# Root states the full app can't run on: run_server serves the reduced app for them instead.
UNAVAILABLE = ("missing", "foreign", "newer")
RECHECK_SECONDS = 15.0  # P8: how often a reduced or read-only hub looks at the root again
GOODBYE_BY = 6.0  # seconds after shutdown began; then the tasks' kills and close_voice still fit in 10
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


def _ensure_quotes(app: web.Application) -> None:
    """Brains set up before phase 4 get the voice's quote file, and before chatter its info lines, each in a
    commit of its own."""
    if ensure_lines(app["paths"].brain):  # the info lines' wordings (chatter, 2026-10-26)
        app["repo"].commit_all("add the voice's info lines")
        app["backup"].notify_commit()
    elif ensure_line_pools(app["paths"].brain):  # wordings for lines added since the file was seeded
        app["repo"].commit_all("add the voice's new info line wordings")
        app["backup"].notify_commit()
    if ensure_quotes(app["paths"].brain):
        app["repo"].commit_all("add the voice quotes")
        app["backup"].notify_commit()
        return
    if ensure_workout_pool(app["paths"].brain):  # a file seeded before the workout pool (2026-10-24)
        app["repo"].commit_all("add the voice's workout quips")
        app["backup"].notify_commit()
    if ensure_reply_pools(app["paths"].brain):  # a file seeded before the replies (2026-10-25)
        app["repo"].commit_all("add the voice's reply quips")
        app["backup"].notify_commit()
    if ensure_chatter_pools(app["paths"].brain):  # a file seeded before chatter (2026-10-26)
        app["repo"].commit_all("add the voice's chatter quips")
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
    """Captures held for this root while it wasn't writable go into the raw log with their own id
    and ts; the raw log step right after indexes them. Each is marked queued as a fresh capture
    is, so the filing queue step files them all (the cap on never-filed entries doesn't apply).
    Captures held for another ATLAS folder (this OS's state folder is shared by every hub on
    it) stay held, in one line of startup_errors."""
    done = holding.flush(osdirs.state_dir(), app["paths"], app["store"], app["startup_errors"])
    app["held_flushed"] = sum(1 for e in done.appended if e not in done.tablet)
    for entry_id in done.appended:
        if entry_id not in done.tablet:  # tablet notes are kept, never filed automatically (phase 3)
            app["worker"].mark_queued(entry_id)
    store = app["store"]
    for entry_id, request_id in done.answers.items():  # answers held while the root was read-only
        req = store.get_request(request_id)
        if req and req["status"] == "open":
            store.set_request_answered(request_id, entry_id)
    if done.answers:
        store.export_tablet(app["paths"].brain)
    if done.elsewhere:
        n = len(done.elsewhere)
        folders = ", ".join(sorted({r["root"]["path"] for r in done.elsewhere}))
        held, left = ("1 capture held on this OS belongs", "it was") if n == 1 else (
            f"{n} captures held on this OS belong", "they were")
        app["startup_errors"].append(f"{held} to another ATLAS folder ({folders}); {left} left in "
                                     "the holding folder")


def _rebuild_notes(app: web.Application) -> None:
    """Notes are tens of MB at most: re-index all of them on every start, so edits made while
    the hub was off (or a deleted note) can never leave recall quoting stale text."""
    for failure in app["store"].reindex_notes(app["paths"].brain):
        app["startup_errors"].append(f"notes index: could not index {failure}")


def _repair_claude_integration(app: web.Application, data_dir: Path | None) -> None:
    """Once per start, in every mode (rename spec §2.2), and only in the owner's real hub (a
    runtime hub run from a slot of its own state folder; see routes.repair_legacy_integration):
    our own pre-rename Claude Code hooks, status line, wrapper and CLAUDE.md block are replaced at
    the current paths, then the shell's compat link at the old state folder goes. `data_dir` is
    None in reduced and read-only mode. Quiet: a failure is only logged (to hub.log), never a
    startup error; Settings → Claude Code shows "out of date" with its install button anyway."""
    try:
        repaired, removed = repair_legacy_integration(app, data_dir)
        if repaired:
            log.warning("replaced the pre-rename Claude Code integration")
        if removed:
            log.warning("removed the pre-rename compat path %s", removed)
    except Exception as e:  # never fails a start
        log.warning("could not replace the pre-rename Claude Code integration: %s", e)


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
    _repair_claude_integration(app, paths.data)  # about Claude Code, not the brain: before setup too
    if not setup_done(app):
        return []
    step("git lock", _clear_stale_git_locks, app)
    step("agents", app["agents"].end_previous_boot)
    step("git setup", repo.init)
    step("owner edits", _commit_owner_edits, app)
    step("git attributes", _ensure_gitattributes, app)
    step("voice quotes", _ensure_quotes, app)
    step("restoring items", store.restore_items_if_empty, paths.brain)
    step("restoring proposals", store.restore_proposals_if_empty, paths.brain)
    step("restoring tablet requests", store.restore_tablet_if_empty, paths.brain)
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
    app["loop"] = asyncio.get_running_loop()  # threads (the voice download) broadcast through it
    if app["root_status"].state == "readonly":
        # Every startup step writes (git, hub.db, the brain), and so do the workers, the backup
        # and the agents prune: none of them runs. Only the watch for a writable root does.
        app["tasks"] = [asyncio.create_task(_watch_root(app)), asyncio.create_task(watch_lan(app))]
        await t(_repair_claude_integration, app, None)  # writes nothing under the root
        return
    for entry_id in await t(_startup_sync, app):
        app["worker"].queue.put_nowait(entry_id)  # on the loop: asyncio.Queue is not thread-safe
    app["tasks"] = [asyncio.create_task(watch_lan(app))]
    if app["start_workers"]:
        app["tasks"] += [asyncio.create_task(app["worker"].run_forever()),
                         asyncio.create_task(app["backup"].run_forever()),
                         asyncio.create_task(_prune_agents(app))]
        if app.get("voice") is not None:
            await t(app["chatter"].startup, app.get("held_flushed", 0))  # an update, held notes flushed
            app["tasks"].append(asyncio.create_task(voice_tick(app)))


async def on_shutdown(app: web.Application) -> None:
    app["shutdown"]["since"] = time.monotonic()  # the goodbye fits inside the shell's 10 s grace from here
    for ws in list(app["sockets"]):
        await ws.close()


async def on_cleanup(app: web.Application) -> None:
    if app.get("chatter") is not None and app["start_workers"] and app["exit_code"] != EXIT_RESTART:
        # The owner quit ATLAS: one line before the voice goes, first, and only in what's left of the
        # shell's 10 s grace (GOODBYE_BY after shutdown began), so the store still closes cleanly.
        left = GOODBYE_BY - (time.monotonic() - app["shutdown"].get("since", time.monotonic()))
        await t(app["chatter"].mark_quit)  # the next start says hello, even a minute from now
        if left > 1:
            await t(app["chatter"].goodbye, left)
    tasks = [*app["tasks"], *app["background"]]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await t(close_voice, app)  # the worker (and whatever it's playing) goes with the hub
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
    app["shutdown"] = {}  # filled in on_shutdown (a started app's own keys can't change)


def create_app(paths: Paths, *, runner=None, start_workers: bool = True,
               claude_settings_path: Path | None = None, status: RootStatus | None = None,
               recheck_interval: float = RECHECK_SECONDS, voice_env: dict | None = None) -> web.Application:
    # The root is classified before anything touches it: the hub never creates a root. run_server
    # passes the status it took the lock on, so both agree.
    status = status or check_root(paths)
    if status.state in UNAVAILABLE:
        raise RootUnavailable(status)
    readonly = status.state == "readonly"
    if status.state in ("ok", "fresh"):
        paths.data.mkdir(exist_ok=True)  # inside the existing root; never the root itself
    app = web.Application(middlewares=[lan_guard, local_only, errors, readonly_guard] if readonly
                          else [lan_guard, local_only, errors])
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
        if type_ not in TabletFeed.QUIET:
            app["feed"].bump()  # the tablet long-polls instead of a WebSocket (phase 3)
        extra = [("items.changed", {})] if type_ in ("filing.done", "filing.failed") else []
        for name, payload in [(type_, data), *extra]:
            for ws in list(app["sockets"]):
                try:
                    await ws.send_json({"type": name, "data": payload})
                except Exception:
                    app["sockets"].discard(ws)

    app["usage"] = UsageStore(paths.data / "usage.json")
    app["statusline_seen"] = {}  # "at": the last status line post (memory only)
    # Phase 3: the tablet's pairing, its LAN listener (serve() hands it the runner) and change feed.
    app["feed"] = TabletFeed()
    app["pairing"] = Pairing(store)
    app["answer_lock"] = asyncio.Lock()  # one answer at a time: two at once must not both land
    app["lan"] = LanSite(lambda: load_settings(paths), lambda: app["port"])

    async def usage_from_run(info: dict) -> None:
        # The hub's own claude runs use the plan too, and no status line reports them. Recorded in
        # the background: an fsync or a slow page must never hold up the answer or the filing.
        if limits := rate_limits_from_event(info):
            task = asyncio.create_task(record_usage(app, {"rate_limits": limits}))
            app["background"].add(task)
            task.add_done_callback(_usage_recorded)

    def _usage_recorded(task: asyncio.Task) -> None:
        app["background"].discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.warning("could not record usage from a claude run: %s", task.exception())

    backup = BackupPusher(repo, lambda: load_settings(paths), broadcast)
    app.update(paths=paths, store=store, repo=repo, broadcast=broadcast, backup=backup,
               runner=runner or ClaudeRunner(settings["claude_path"],
                                             on_rate_limit=None if readonly else usage_from_run))
    # Every hub commit (owner edits, filing, revert, confirm, edit, setup, map edit) reports
    # here: the notes it touched are re-indexed, then a backup is scheduled.
    app["on_commit"] = commit_hook(store, paths.brain, repo, backup.notify_commit)
    app["worker"] = FilingWorker(paths, store, repo, app["runner"], broadcast, app["on_commit"],
                                 on_filed=lambda eid, done: reply_to_filing(app, eid, done))
    app["reviewer"] = Reviewer(paths, store, repo, app["on_commit"])
    app["claude_settings_path"] = Path(claude_settings_path) if claude_settings_path else default_settings_path()
    # Phase 4: the voice (None on a read-only root). It hears the agents' status changes.
    setup_voice(app, speaker_env=voice_env)
    app["agents"] = AgentTracker(store, paths, lambda: load_settings(paths),
                                 on_transition=agent_transition(app) if app["voice"] else None)

    app["arm"] = ArmProbe()
    # The port the hub listens on: run_server sets the bound one; default to the settings port.
    app["port"] = settings["port"]
    app["opener"] = _default_opener

    add_routes(app)
    add_tablet_routes(app)
    add_voice_routes(app)
    if paths.web.is_dir():  # code-relative; never created under the data root
        app.router.add_static("/static/", paths.web)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app


async def _start_reduced(app: web.Application) -> None:
    await t(_repair_claude_integration, app, None)  # writes nothing under the root
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
