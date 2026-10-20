"""HTTP route handlers: JSON API, SSE for recall, WebSocket push."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .brainmap import BrainMap, hierarchy, load_map, save_map
from .claude_runner import ClaudeError
from .config import load_settings, save_settings
from .filing import _prepare_worktree
from .rawlog import append_entry
from .recall import deeper_answer, quick_answer, search_hits
from .store import Store
from .today import compute_today

log = logging.getLogger(__name__)

CAPTURE_SOURCES = ("pc", "widget")
PLACEHOLDER = "<!doctype html><meta charset=utf-8><title>BrainHub</title><p>BrainHub UI coming.</p>"
LIVE_STATUSES = ["open", "to_read", "snoozed"]
t = asyncio.to_thread


def err(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _msg(e: Exception) -> str:
    if e.args and isinstance(e.args[0], list):
        return "; ".join(str(x) for x in e.args[0])
    return str(e.args[0]) if e.args else type(e).__name__


ALLOWED_HOSTS = ("127.0.0.1", "localhost")


@web.middleware
async def local_only(request: web.Request, handler):
    """Outermost guard: blocks DNS rebinding (Host) and cross-site requests (Origin)."""
    if request.url.host not in ALLOWED_HOSTS:
        return err(403, "forbidden host")
    origin = request.headers.get("Origin")
    if origin is not None and origin != f"http://{request.host}":
        return err(403, "forbidden origin")
    if request.path.startswith("/api/ask") and request.headers.get("Sec-Fetch-Site") in (
            "cross-site", "same-site"):
        return err(403, "forbidden origin")
    return await handler(request)


@web.middleware
async def errors(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException as e:
        if e.status >= 400:
            return err(e.status, e.reason)
        raise
    except ValueError as e:
        return err(400, _msg(e))
    except Exception as e:
        log.exception("unhandled error in %s", request.path)
        return err(500, str(e) or type(e).__name__)


async def body_dict(request: web.Request) -> dict:
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(reason="content type must be application/json")
    try:
        data = await request.json()
    except ValueError:
        raise ValueError("body must be JSON") from None
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    return data


def ids_from(body: dict) -> list[str]:
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValueError("ids must be a list of strings")
    return ids


def setup_done(app: web.Application) -> bool:
    return (app["paths"].brain / "map.json").exists()


def _folders(brain, m: BrainMap) -> None:
    _prepare_worktree(brain, m)  # base folders plus one per custom area


def _map_from(data) -> BrainMap:
    if not isinstance(data, dict):
        raise ValueError("map must be a JSON object")
    try:
        return BrainMap(**data)
    except TypeError as e:
        raise ValueError(f"bad map: {e}") from None


# -- handlers: pages and state -----------------------------------------------
async def index(request: web.Request) -> web.StreamResponse:
    page = request.app["paths"].web / "index.html"
    if page.is_file():
        return web.FileResponse(page)
    return web.Response(text=PLACEHOLDER, content_type="text/html")


async def state(request: web.Request) -> web.Response:
    app = request.app
    return web.json_response({
        "setup_done": setup_done(app), "settings": await t(load_settings, app["paths"]),
        "backup": app["backup"].status(), "log_error": app["log_error"],
        "brain_path": str(app["paths"].brain)})


async def setup(request: web.Request) -> web.Response:
    app, paths = request.app, request.app["paths"]
    if setup_done(app):
        return err(409, "setup already done")
    body = await body_dict(request)
    m = _map_from(body.get("map"))
    remote = body.get("backup_remote")
    if remote is not None and not isinstance(remote, str):
        raise ValueError("backup_remote must be a string or null")
    await t(save_map, paths.brain, m)
    await t(_folders, paths.brain, m)
    await t(app["repo"].init)
    await t(app["repo"].commit_all, "setup")
    if remote:
        await t(save_settings, paths, {"backup_remote": remote})
    app["backup"].notify_commit()
    return web.json_response({"ok": True})


async def get_map(request: web.Request) -> web.Response:
    m = await t(load_map, request.app["paths"].brain)
    if m is None:
        return err(409, "run setup first")
    return web.json_response(asdict(m))


async def put_map(request: web.Request) -> web.Response:
    app, paths = request.app, request.app["paths"]
    if not setup_done(app):
        return err(409, "run setup first")
    m = _map_from(await body_dict(request))
    await t(save_map, paths.brain, m)
    await t(_folders, paths.brain, m)
    await t(app["repo"].commit_all, "map edit")
    app["backup"].notify_commit()
    await app["broadcast"]("map.changed", {})
    return web.json_response(asdict(m))


def _hierarchy(app) -> dict | None:
    m = load_map(app["paths"].brain)
    if m is None:
        return None
    items = app["store"].list_items(status=LIVE_STATUSES)
    node_counts: dict[str, dict[str, int]] = {"project": {}, "person": {}, "area": {}}
    for i in items:
        for kind, key in (("project", i.project), ("person", i.person), ("area", i.area)):
            if key:
                node_counts[kind][key] = node_counts[kind].get(key, 0) + 1
    # hierarchy() looks counts up by slug only: merge, but projects/people take precedence
    # over an area that happens to share the slug.
    merged = {**node_counts["area"], **node_counts["person"], **node_counts["project"]}
    tree = hierarchy(app["paths"].brain, m, merged)
    tree["items"] = len(items)

    def fix(node: dict) -> None:
        kind = node["kind"]
        if kind in node_counts:
            node["items"] = node_counts[kind].get(node["slug"], 0)
        for c in node["children"]:
            fix(c)

    fix(tree)
    return tree


async def get_hierarchy(request: web.Request) -> web.Response:
    h = await t(_hierarchy, request.app)
    return web.json_response(h) if h else err(409, "run setup first")


async def get_settings(request: web.Request) -> web.Response:
    return web.json_response(await t(load_settings, request.app["paths"]))


async def put_settings(request: web.Request) -> web.Response:
    return web.json_response(await t(save_settings, request.app["paths"], await body_dict(request)))


async def backup_push(request: web.Request) -> web.Response:
    await request.app["backup"].tick(force=True)
    return web.json_response(request.app["backup"].status())


# -- handlers: capture and filing --------------------------------------------
async def post_entry(request: web.Request) -> web.Response:
    app = request.app
    body = await body_dict(request)
    text, source = body.get("text"), body.get("source")
    if not setup_done(app):
        return err(409, "run setup first")
    if not isinstance(text, str) or not text.strip():
        return err(400, "entry text is blank")
    if source not in CAPTURE_SOURCES:
        return err(400, f"source must be one of {list(CAPTURE_SOURCES)}")
    entry = await t(append_entry, app["paths"].brain, text, source)  # the log comes first
    try:
        await t(app["store"].add_entry, entry)
        await t(app["store"].index_entry, entry)
        await t(app["worker"].mark_queued, entry.id)
        app["worker"].queue.put_nowait(entry.id)
    except Exception:  # the log has it; startup backfill recovers the index
        log.exception("entry %s saved to the log but not indexed or queued", entry.id)
    await app["broadcast"]("entry.saved", {"entry_id": entry.id})
    return web.json_response({"entry_id": entry.id}, status=201)


def _journal(store: Store, query) -> list[dict]:
    try:
        limit = max(1, min(int(query.get("limit", 100)), 500))
    except ValueError:
        raise ValueError("limit must be an integer") from None
    entries = store.list_entries(query.get("day"), query.get("before"), limit)
    filings = {f.entry_id: f for f in store.list_filings(entry_ids=[e.id for e in entries])}
    out = []
    for e in entries:
        f = filings.get(e.id)
        out.append({"id": e.id, "ts": e.ts.isoformat(), "source": e.source, "text": e.text,
                    "filing": {"status": f.status, "synopsis": f.synopsis, "files": f.files,
                               "error": f.error} if f else None})
    return out


async def get_entries(request: web.Request) -> web.Response:
    return web.json_response(await t(_journal, request.app["store"], request.query))


async def _require_entry(app, entry_id: str) -> None:
    if await t(app["store"].get_entry, entry_id) is None:
        raise web.HTTPNotFound(reason=f"unknown entry: {entry_id}")


async def get_filing(request: web.Request) -> web.Response:
    app, eid = request.app, request.match_info["entry_id"]
    await _require_entry(app, eid)
    filing = await t(app["store"].get_filing, eid)
    proposals = await t(app["store"].list_proposals, entry_id=eid)
    return web.json_response({"filing": asdict(filing) if filing else None,
                              "proposals": [asdict(p) for p in proposals]})


async def _in_progress(app, eid: str) -> bool:
    f = await t(app["store"].get_filing, eid)
    return bool(f and f.status in ("queued", "running"))


async def retry_filing(request: web.Request) -> web.Response:
    app, eid = request.app, request.match_info["entry_id"]
    await _require_entry(app, eid)
    if await _in_progress(app, eid):
        return err(409, "filing in progress")
    await t(app["worker"].mark_queued, eid)
    app["worker"].queue.put_nowait(eid)
    return web.json_response({"ok": True}, status=202)


async def correct_filing(request: web.Request) -> web.Response:
    app, eid = request.app, request.match_info["entry_id"]
    text = (await body_dict(request)).get("text")
    if not isinstance(text, str) or not text.strip():
        return err(400, "correction text is blank")
    await _require_entry(app, eid)
    if await _in_progress(app, eid):
        return err(409, "filing in progress")

    async def run() -> None:
        try:
            await app["worker"].correct(eid, text)
        except Exception as e:
            log.exception("correction of %s failed", eid)
            await app["broadcast"]("filing.failed", {"entry_id": eid, "error": str(e)[:500]})

    task = asyncio.create_task(run())
    app["background"].add(task)
    task.add_done_callback(app["background"].discard)
    return web.json_response({"ok": True}, status=202)


# -- handlers: review --------------------------------------------------------
async def get_proposals(request: web.Request) -> web.Response:
    store = request.app["store"]
    proposals = await t(store.list_proposals)
    groups: dict[str, list] = {}
    for p in proposals:
        groups.setdefault(p.entry_id, []).append(asdict(p))
    filings = {f.entry_id: f for f in await t(store.list_filings, entry_ids=list(groups))}
    return web.json_response([
        {"entry_id": eid, "synopsis": filings[eid].synopsis if eid in filings else "",
         "proposals": ps} for eid, ps in groups.items()])


async def patch_proposal(request: web.Request) -> web.Response:
    p = await t(request.app["reviewer"].edit_proposal, request.match_info["id"],
                await body_dict(request))
    return web.json_response(asdict(p))


async def confirm(request: web.Request) -> web.Response:
    app = request.app
    items = await t(app["reviewer"].confirm, ids_from(await body_dict(request)))
    await app["broadcast"]("items.changed", {})
    await app["broadcast"]("map.changed", {})
    return web.json_response([asdict(i) for i in items])


async def dismiss(request: web.Request) -> web.Response:
    app = request.app
    await t(app["reviewer"].dismiss, ids_from(await body_dict(request)))
    await app["broadcast"]("items.changed", {})
    return web.json_response({"ok": True})


async def get_items(request: web.Request) -> web.Response:
    q = request.query
    filters = {k: (q.get(k) or None) for k in ("status", "kind", "project", "person", "area")}
    items = await t(lambda: request.app["store"].list_items(**filters))
    return web.json_response([asdict(i) for i in items])


async def patch_item(request: web.Request) -> web.Response:
    app = request.app
    item = await t(app["reviewer"].edit_item, request.match_info["id"], await body_dict(request))
    await app["broadcast"]("items.changed", {})
    return web.json_response(asdict(item))


def _today(app) -> dict:
    store = app["store"]
    return compute_today(
        store.list_items(status=LIVE_STATUSES), store.list_proposals(),
        [f.entry_id for f in store.list_filings("failed")], datetime.now(),
        load_settings(app["paths"])["nudge_days"])


async def get_today(request: web.Request) -> web.Response:
    return web.json_response(await t(_today, request.app))


# -- handlers: SSE and WebSocket ---------------------------------------------
async def _event(resp: web.StreamResponse, name: str, data) -> None:
    await resp.write(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())


async def _stream(request: web.Request, hits, chunks) -> web.StreamResponse:
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                       "Cache-Control": "no-cache"})
    await resp.prepare(request)
    try:
        if hits is not None:
            await _event(resp, "hits", [asdict(h) for h in hits])
        try:
            async with contextlib.aclosing(chunks) as stream:
                async for chunk in stream:
                    await _event(resp, "delta", chunk)
        except (ClaudeError, ValueError, OSError) as e:
            await _event(resp, "error", str(e) or type(e).__name__)
        await _event(resp, "done", {})
    except ConnectionResetError:
        pass
    return resp


def _question(request: web.Request) -> str:
    q = request.query.get("q", "").strip()
    if not q:
        raise ValueError("q is required")
    return q


async def ask(request: web.Request) -> web.StreamResponse:
    app, paths = request.app, request.app["paths"]
    q = _question(request)
    settings = await t(load_settings, paths)
    hits = await t(search_hits, app["store"], q, settings["recall_top_n"])
    return await _stream(request, hits, quick_answer(
        app["runner"], q, hits, settings, paths.prompts, paths.brain))


async def ask_deeper(request: web.Request) -> web.StreamResponse:
    app, paths = request.app, request.app["paths"]
    q = _question(request)
    settings = await t(load_settings, paths)
    return await _stream(request, None, deeper_answer(
        app["runner"], q, settings, paths.prompts, paths.brain))


async def search(request: web.Request) -> web.Response:
    """Instant local full-text hits. Never calls Claude. A blank or missing q is a 400."""
    q = _question(request)
    settings = await t(load_settings, request.app["paths"])
    hits = await t(search_hits, request.app["store"], q, settings["recall_top_n"])
    return web.json_response([asdict(h) for h in hits])


NOTES_HIDDEN = ("log", "_items", ".git")
NOTES_CAP = 200


def _notes(brain: Path, prefix: str) -> list[dict] | None:
    """Markdown notes under brain/<prefix>; None when the prefix is not allowed."""
    root = brain.resolve()
    if not prefix.strip() or "\0" in prefix:
        return None
    try:
        target = (root / prefix).resolve()
        parts = target.relative_to(root).parts
    except (ValueError, OSError):
        return None
    if not parts or parts[0].lower() in NOTES_HIDDEN or parts[0].startswith("."):
        return None
    files = [target] if target.is_file() else sorted(target.rglob("*.md")) if target.is_dir() else []
    out = []
    for f in files:
        try:
            rel = f.resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        if f.suffix != ".md" or not f.is_file() or any(
                x.lower() in NOTES_HIDDEN or x.startswith(".") for x in rel.parts):
            continue
        out.append({"path": rel.as_posix(), "text": f.read_text(encoding="utf-8", errors="replace")})
        if len(out) >= NOTES_CAP:
            break
    return out


async def get_notes(request: web.Request) -> web.Response:
    notes = await t(_notes, request.app["paths"].brain, request.query.get("prefix", ""))
    if notes is None:
        return err(400, "prefix must be a folder inside the brain notes")
    return web.json_response(notes)


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    request.app["sockets"].add(ws)
    try:
        async for _ in ws:
            pass
    finally:
        request.app["sockets"].discard(ws)
    return ws


def add_routes(app: web.Application) -> None:
    app.add_routes([
        web.get("/", index), web.get("/api/state", state), web.post("/api/setup", setup),
        web.get("/api/map", get_map), web.put("/api/map", put_map),
        web.get("/api/hierarchy", get_hierarchy),
        web.get("/api/settings", get_settings), web.put("/api/settings", put_settings),
        web.post("/api/backup/push", backup_push),
        web.post("/api/entries", post_entry), web.get("/api/entries", get_entries),
        web.get("/api/filings/{entry_id}", get_filing),
        web.post("/api/filings/{entry_id}/retry", retry_filing),
        web.post("/api/filings/{entry_id}/correct", correct_filing),
        web.get("/api/proposals", get_proposals),
        web.post("/api/proposals/confirm", confirm), web.post("/api/proposals/dismiss", dismiss),
        web.patch("/api/proposals/{id}", patch_proposal),
        web.get("/api/items", get_items), web.patch("/api/items/{id}", patch_item),
        web.get("/api/today", get_today),
        web.get("/api/search", search), web.get("/api/notes", get_notes),
        web.get("/api/ask", ask), web.get("/api/ask/deeper", ask_deeper),
        web.get("/ws", ws_handler),
    ])
