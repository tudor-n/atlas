"""HTTP route handlers: JSON API, SSE for recall, WebSocket push."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .brainmap import BrainMap, hierarchy, load_map, save_map, validate_map
from . import claude_integration as ci
from .claude_runner import ClaudeError
from .config import load_settings, save_settings, validate_setting
from .filing import _prepare_worktree
from .rawlog import append_entry
from .recall import deeper_answer, quick_answer, search_hits
from .store import Store
from .today import compute_today
from .widget import build_widget

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
        "startup_error": list(app["startup_errors"]) or None,
        "brain_path": str(app["paths"].brain)})


async def setup(request: web.Request) -> web.Response:
    app, paths = request.app, request.app["paths"]
    if setup_done(app):
        return err(409, "setup already done")
    body = await body_dict(request)
    m = _map_from(body.get("map"))
    remote = body.get("backup_remote")
    if isinstance(remote, str):
        remote = remote.strip() or None
    # Validate everything before writing anything: a half-done setup can't be retried (409).
    validate_setting("backup_remote", remote)
    errors_ = validate_map(m)
    if errors_:
        raise ValueError(errors_)
    await t(save_map, paths.brain, m)
    await t(_folders, paths.brain, m)
    await t(app["repo"].init)
    await t(app["repo"].ensure_gitattributes)  # committed with setup: line endings stay as written
    sha = await t(app["repo"].commit_all, "setup")
    if remote:
        await t(save_settings, paths, {"backup_remote": remote})
    await t(app["on_commit"], sha)
    return web.json_response({"ok": True})


async def get_map(request: web.Request) -> web.Response:
    m = await t(load_map, request.app["paths"].brain)
    if m is None:
        return err(409, "run setup first")
    return web.json_response(asdict(m))


def _has_md(folder: Path) -> bool:
    return folder.is_dir() and any(f.is_file() for f in folder.rglob("*.md"))


def _stranded_slugs(store: Store, brain: Path, old: BrainMap, new: BrainMap) -> list[str]:
    """Slugs the new map drops (a rename drops the old slug) that still have items (any
    status) or notes: removing them would strand those, so the map edit is refused."""
    out = []
    kinds = (("area", "areas", "area"), ("project", "projects", "project"),
             ("course", "courses", "project"), ("person", "people", "person"))
    for label, attr, item_field in kinds:
        kept = {n.get("slug") for n in getattr(new, attr)}
        for node in getattr(old, attr):
            slug = node.get("slug")
            if not slug or slug in kept:
                continue
            notes = {"area": lambda s: _has_md(brain / s),
                     "project": lambda s: _has_md(brain / "projects" / s),
                     "course": lambda s: _has_md(brain / "uni" / s),
                     "person": lambda s: (brain / "people" / f"{s}.md").is_file()}[label](slug)
            if notes or store.list_items(**{item_field: slug}):
                out.append(f"{label} '{slug}' has notes or items; it can't be removed or renamed yet")
    return out


async def put_map(request: web.Request) -> web.Response:
    app, paths = request.app, request.app["paths"]
    if not setup_done(app):
        return err(409, "run setup first")
    m = _map_from(await body_dict(request))
    old = await t(load_map, paths.brain)
    if old is not None:
        stranded = await t(_stranded_slugs, app["store"], paths.brain, old, m)
        if stranded:
            raise ValueError(stranded)
    await t(save_map, paths.brain, m)
    await t(_folders, paths.brain, m)
    await t(app["on_commit"], await t(app["repo"].commit_all, "map edit"))
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
        elif kind == "course":  # items name a course in their "project" field
            node["items"] = node_counts["project"].get(node["slug"], 0)
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


FILING_STATUSES = ("queued", "running", "done", "failed")


async def list_filings(request: web.Request) -> web.Response:
    """GET /api/filings?status=failed -> [{entry_id, error, ts, text}], newest first. Today's
    To review uses it, so failed filings never drop out however many entries come after."""
    status = request.query.get("status", "failed")
    if status not in FILING_STATUSES:
        return err(400, f"status must be one of {list(FILING_STATUSES)}")
    return web.json_response(await t(request.app["store"].filings_with_entries, status))


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
    # Queued from this moment, as a retry is: the card stops offering ✓ on the stale proposals.
    await t(app["worker"].mark_queued, eid)

    async def run() -> None:
        try:
            await app["worker"].correct(eid, text, queued=True)
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
    entries = {eid: await t(store.get_entry, eid) for eid in groups}
    return web.json_response([
        {"entry_id": eid, "synopsis": filings[eid].synopsis if eid in filings else "",
         "text": entries[eid].text if entries[eid] else "", "proposals": ps}
        for eid, ps in groups.items()])


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


# -- handlers: widget ---------------------------------------------------------
_VIEW_RX = re.compile(r"[a-z]+(/[a-z0-9-]+)?")
_HTTP_RX = re.compile(r"https?://\S+")
_HTTPS_RX = re.compile(r"https://\S+")


def _widget_state(app) -> dict:
    store, paths = app["store"], app["paths"]
    settings = load_settings(paths)
    return {"items": store.list_items(status=LIVE_STATUSES), "proposals": len(store.list_proposals()),
            "failed": len(store.list_filings("failed")), "map": load_map(paths.brain),
            "usage": app["usage"].view(settings["usage_warn_pct"], settings["fable_usage_url"]),
            "agents": app["agents"].rows(), "settings": settings, "now": datetime.now()}


async def get_widget(request: web.Request) -> web.Response:
    app = request.app
    state = await t(_widget_state, app)
    state["arm_reachable"] = await app["arm"].reachable(state["settings"]["arm_url"])
    return web.json_response(await t(build_widget, state))


async def widget_page(request: web.Request) -> web.StreamResponse:
    f = request.app["paths"].web / "widget.html"
    if not f.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(f)


async def post_open(request: web.Request) -> web.Response:
    app = request.app
    body = await body_dict(request)
    target, view = body.get("target"), body.get("view")
    settings = load_settings(app["paths"])
    if target == "app":
        if view is not None and not (isinstance(view, str) and _VIEW_RX.fullmatch(view)):
            raise web.HTTPBadRequest(reason="bad view")
        url = f"http://127.0.0.1:{settings['port']}/" + (f"#/{view}" if view else "")
    elif target == "arm":
        url = settings["arm_url"]
        if not _HTTP_RX.fullmatch(url):
            raise web.HTTPBadRequest(reason="arm_url is not an http(s) URL")
    elif target == "fable_usage":
        url = settings["fable_usage_url"]
        if not _HTTPS_RX.fullmatch(url):
            raise web.HTTPBadRequest(reason="fable_usage_url is not an https URL")
    else:
        raise web.HTTPBadRequest(reason="unknown target")
    await t(app["opener"], url)
    return web.json_response({"ok": True, "url": url})


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


def _usage_view(app: web.Application) -> dict:
    s = load_settings(app["paths"])
    return app["usage"].view(s["usage_warn_pct"], s["fable_usage_url"])


async def post_usage(request: web.Request) -> web.Response:
    app = request.app
    if await t(app["usage"].update, await body_dict(request)):
        await app["broadcast"]("usage.changed", await t(_usage_view, app))
    return web.Response(status=204)


async def get_usage(request: web.Request) -> web.Response:
    return web.json_response(await t(_usage_view, request.app))


async def post_hook_event(request: web.Request) -> web.Response:
    app = request.app
    if await t(app["agents"].handle, await body_dict(request)):
        await app["broadcast"]("agents.changed", {})
    return web.Response(status=204)


async def get_agents(request: web.Request) -> web.Response:
    return web.json_response(await t(request.app["agents"].rows))


async def patch_agent(request: web.Request) -> web.Response:
    app = request.app
    try:
        row = await t(app["agents"].edit, request.match_info["session_id"], await body_dict(request))
    except KeyError:
        raise web.HTTPNotFound(reason="unknown session") from None
    await app["broadcast"]("agents.changed", {})
    return web.json_response(row)


async def delete_agent(request: web.Request) -> web.Response:
    app = request.app
    await t(app["agents"].remove, request.match_info["session_id"])
    await app["broadcast"]("agents.changed", {})
    return web.Response(status=204)


def _integration_view(app: web.Application) -> dict:
    port = load_settings(app["paths"])["port"]
    view = ci.status(app["claude_settings_path"], port=port)
    return {**view, "last_hook_at": app["agents"].last_event_at(), "last_usage_at": app["usage"].updated_at()}


async def get_integration(request: web.Request) -> web.Response:
    try:
        return web.json_response(await t(_integration_view, request.app))
    except ci.IntegrationError as e:
        raise web.HTTPBadRequest(reason=str(e)) from None


def _integration_change(app: web.Application, install: bool) -> dict:
    paths, target = app["paths"], app["claude_settings_path"]
    if install:
        ci.install(target, python=str(ci.console_python(Path(sys.executable))), script=str(ci.hook_script()),
                   port=load_settings(paths)["port"], data_dir=paths.data)
    else:
        ci.uninstall(target, data_dir=paths.data)
    return _integration_view(app)


async def post_integration_install(request: web.Request) -> web.Response:
    try:
        return web.json_response(await t(_integration_change, request.app, True))
    except ci.IntegrationError as e:
        raise web.HTTPBadRequest(reason=str(e)) from None


async def post_integration_uninstall(request: web.Request) -> web.Response:
    try:
        return web.json_response(await t(_integration_change, request.app, False))
    except ci.IntegrationError as e:
        raise web.HTTPBadRequest(reason=str(e)) from None


def add_routes(app: web.Application) -> None:
    app.add_routes([
        web.get("/", index), web.get("/api/state", state), web.post("/api/setup", setup),
        web.get("/api/map", get_map), web.put("/api/map", put_map),
        web.get("/api/hierarchy", get_hierarchy),
        web.get("/api/settings", get_settings), web.put("/api/settings", put_settings),
        web.post("/api/backup/push", backup_push),
        web.post("/api/entries", post_entry), web.get("/api/entries", get_entries),
        web.get("/api/filings", list_filings), web.get("/api/filings/{entry_id}", get_filing),
        web.post("/api/filings/{entry_id}/retry", retry_filing),
        web.post("/api/filings/{entry_id}/correct", correct_filing),
        web.get("/api/proposals", get_proposals),
        web.post("/api/proposals/confirm", confirm), web.post("/api/proposals/dismiss", dismiss),
        web.patch("/api/proposals/{id}", patch_proposal),
        web.get("/api/items", get_items), web.patch("/api/items/{id}", patch_item),
        web.get("/api/today", get_today),
        web.get("/api/search", search), web.get("/api/notes", get_notes),
        web.get("/api/ask", ask), web.get("/api/ask/deeper", ask_deeper),
        web.post("/api/usage", post_usage), web.get("/api/usage", get_usage),
        web.post("/api/hooks/event", post_hook_event), web.get("/api/agents", get_agents),
        web.patch("/api/agents/{session_id}", patch_agent),
        web.delete("/api/agents/{session_id}", delete_agent),
        web.get("/api/integration", get_integration),
        web.post("/api/integration/install", post_integration_install),
        web.post("/api/integration/uninstall", post_integration_uninstall),
        web.get("/api/widget", get_widget), web.get("/widget", widget_page),
        web.post("/api/open", post_open),
        web.get("/ws", ws_handler),
    ])
