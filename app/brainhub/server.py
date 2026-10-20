"""aiohttp app wiring: startup recovery, workers, WebSocket broadcast. Binds 127.0.0.1 only."""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from .backup import BackupPusher
from .claude_runner import ClaudeRunner
from .config import Paths, load_settings
from .filing import FilingWorker
from .gitrepo import BrainRepo
from .rawlog import read_log
from .review import Reviewer
from .routes import add_routes, errors, local_only, setup_done
from .store import Store

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
t = asyncio.to_thread


# -- startup -----------------------------------------------------------------
def _startup_sync(app: web.Application) -> list[str]:
    paths, store, repo = app["paths"], app["store"], app["repo"]
    store.init_schema()
    if not setup_done(app):
        return []
    repo.init()
    if repo.is_dirty():
        repo.commit_all("owner edits")
        app["backup"].notify_commit()
    store.restore_items_if_empty(paths.brain)
    try:
        if store.fts_count() == 0:
            store.reindex_all(paths.brain)
        else:
            for e in read_log(paths.brain):
                if store.get_entry(e.id) is None:
                    store.add_entry(e)
                    store.index_entry(e)
    except (ValueError, OSError) as e:  # damaged log: keep serving, never touch the file
        log.error("raw log problem at startup: %s", e)
        app["log_error"] = str(e)
    return app["worker"].requeue_unfinished()


async def on_startup(app: web.Application) -> None:
    for entry_id in await t(_startup_sync, app):
        app["worker"].queue.put_nowait(entry_id)  # on the loop: asyncio.Queue is not thread-safe
    if app["start_workers"]:
        app["tasks"] = [asyncio.create_task(app["worker"].run_forever()),
                        asyncio.create_task(app["backup"].run_forever())]


async def on_shutdown(app: web.Application) -> None:
    for ws in list(app["sockets"]):
        await ws.close()


async def on_cleanup(app: web.Application) -> None:
    tasks = [*app["tasks"], *app["background"]]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def create_app(paths: Paths, *, runner=None, start_workers: bool = True) -> web.Application:
    app = web.Application(middlewares=[local_only, errors])
    paths.data.mkdir(parents=True, exist_ok=True)
    settings = load_settings(paths)
    store = Store(paths.db)
    repo = BrainRepo(paths.brain)
    app["sockets"], app["tasks"], app["background"] = set(), [], set()
    app["log_error"], app["start_workers"] = None, start_workers

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
    app["worker"] = FilingWorker(paths, store, repo, app["runner"], broadcast, backup.notify_commit)
    app["reviewer"] = Reviewer(paths, store, repo, backup.notify_commit)

    add_routes(app)
    if paths.web.is_dir():  # code-relative; never created under the data root
        app.router.add_static("/static/", paths.web)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app
