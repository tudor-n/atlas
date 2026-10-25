"""HTTP route handlers: JSON API, SSE for recall, WebSocket push."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .brainmap import BrainMap, hierarchy, load_map, save_map, validate_map
from . import claude_integration as ci
from . import claude_sync
from . import holding
from . import osdirs
from . import rootguard
from . import runtime
from .claude_runner import ClaudeError
from .config import load_settings, save_settings, validate_setting
from .filing import _prepare_worktree
from .quotes import ensure_quotes
from .rawlog import append_entry
from .recall import NOTHING, deeper_answer, quick_answer, search_hits
from .store import Store
from .today import compute_today
from .voice_lines import reply_line
from .widget import build_widget

log = logging.getLogger(__name__)

CAPTURE_SOURCES = ("pc", "widget")
PLACEHOLDER = "<!doctype html><meta charset=utf-8><title>ATLAS</title><p>ATLAS UI coming.</p>"
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
    """Blocks DNS rebinding (Host) and cross-site requests (Origin) on loopback. A request on the
    tablet's LAN site was already checked by tablet.lan_guard, the outermost middleware."""
    if request.get("lan"):
        return await handler(request)
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
    app, held_problems = request.app, []
    drive_ = await t(drive, app, held_problems)
    return web.json_response({
        "setup_done": setup_done(app), "settings": await t(load_settings, app["paths"]),
        "backup": app["backup"].status(), "log_error": app["log_error"],
        "startup_error": [*app["startup_errors"], *held_problems] or None,
        "brain_path": str(app["paths"].brain), "os": osdirs.os_name(),
        "warnings": list(app["warnings"]), "drive": drive_,
        "runtime": await t(runtime.hub_view, osdirs.state_dir())})


async def setup(request: web.Request) -> web.Response:
    app, paths = request.app, request.app["paths"]
    if setup_done(app):
        return err(409, "setup already done")
    # A brain is only ever created on a fresh root, checked now: the drive may have gone since start.
    status = await t(rootguard.check_root, paths)
    if status.state != "fresh":
        return err(409, status.message or "setup only runs on an empty folder, and this root is "
                                          "already marked as a brain (data/root.json)")
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
    await t(ensure_quotes, paths.brain)  # the voice's quote file (phase 4), the owner's to edit
    sha = await t(app["repo"].commit_all, "setup")
    if remote:
        await t(save_settings, paths, {"backup_remote": remote})
    await t(app["on_commit"], sha)
    # Last, so a marker always means a finished setup (an interrupted one is adopted at next start).
    await t(rootguard.create_marker, paths)
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
    before = await t(load_settings, request.app["paths"])
    saved = await t(save_settings, request.app["paths"], await body_dict(request))
    if "lan" in request.app:
        await request.app["lan"].sync()  # "Tablet over Wi-Fi" takes effect now, not in 15 s
    if request.app.get("voice") is not None:
        if before["voice_enabled"] and not saved["voice_enabled"]:
            await t(request.app["voice"].stop)  # switched off: silent now, mid-sentence included
        await request.app["broadcast"]("voice.changed", {})
    return web.json_response(saved)


async def backup_push(request: web.Request) -> web.Response:
    await request.app["backup"].tick(force=True)
    return web.json_response(request.app["backup"].status())


# -- ATLAS's replies (voice-input decisions VI4) -----------------------------
async def atlas_reply(app: web.Application, kind: str, count: int = 0, others: int = 0, *,
                      entry_id: str | None = None) -> str:
    """What ATLAS says after the one input filed a note or answered a question: spoken through the
    voice's gates, and broadcast as `atlas.reply` for the ATLAS screen's caption either way. Never
    raises: the note is saved and the answer given whatever the voice does (V39)."""
    text = reply_line(kind, count, others)
    voice = app.get("voice")  # None on a read-only root: the plain line, nothing spoken
    if voice is not None:
        try:
            text = await t(voice.reply, kind, count, others)
        except Exception:
            log.exception("voice: the reply failed")
    try:
        await app["broadcast"]("atlas.reply", {"kind": kind, "text": text, "entry_id": entry_id})
    except Exception:
        log.exception("could not broadcast the reply")
    return text


async def reply_to_filing(app: web.Application, entry_id: str, done: bool) -> None:
    """A filing the owner waited on has ended: its item proposals are the possible tasks."""
    if not done:
        await atlas_reply(app, "failed", entry_id=entry_id)
        return
    proposals = await t(app["store"].list_proposals, entry_id=entry_id)
    items = sum(1 for p in proposals if p.ptype == "item")
    others = len(proposals) - items
    if items:
        await atlas_reply(app, "tasks", items, others, entry_id=entry_id)
    else:
        await atlas_reply(app, "filed", others, entry_id=entry_id)


# The widget opens a main-window view only by name (the shell lets nothing else into a URL), so a
# question typed there waits here, in memory, for the ATLAS screen to take it (VI1): the latest one,
# for HANDOFF_S. Nothing is written, so it works on a read-only root too.
HANDOFF_S = 120


async def post_handoff(request: web.Request) -> web.Response:
    text = (await body_dict(request)).get("text")
    if not isinstance(text, str) or not text.strip():
        return err(400, "the question is blank")
    if len(text) > 4000:
        return err(400, "the question is too long")
    request.app["atlas_handoff"].update(text=text.strip(), at=time.monotonic())
    return web.json_response({"ok": True}, status=202)


async def take_handoff(request: web.Request) -> web.Response:
    held = request.app["atlas_handoff"]
    text, at = held.pop("text", None), held.pop("at", 0.0)
    fresh = text is not None and time.monotonic() - at <= HANDOFF_S
    return web.json_response({"text": text if fresh else None})


# -- handlers: capture and filing --------------------------------------------
async def post_entry(request: web.Request) -> web.Response:
    app = request.app
    body = await body_dict(request)
    text, source = body.get("text"), body.get("source")
    # A root that was ok at start had a brain: if map.json is gone now, the drive went away mid-run
    # and the capture is held below, not refused.
    if not setup_done(app) and app["root_status"].state != "ok":
        return err(409, "run setup first")
    if not isinstance(text, str) or not text.strip():
        return err(400, "entry text is blank")
    if source not in CAPTURE_SOURCES:
        return err(400, f"source must be one of {list(CAPTURE_SOURCES)}")
    try:
        entry = await t(append_entry, app["paths"].brain, text, source)  # the log comes first
    except OSError as e:  # the root went read-only or away mid-run: hold it on this OS instead
        log.error("the raw log can't be written (%s); the capture is held on this OS", e)
        record = await t(holding.hold, osdirs.state_dir(), app["paths"], text, source, datetime.now())
        return web.json_response({"entry_id": record["id"], "held": True})
    try:
        await t(app["store"].add_entry, entry)
        await t(app["store"].index_entry, entry)
        await t(app["worker"].mark_queued, entry.id)
        if body.get("reply") is True:  # the one input waits on it: ATLAS replies when it's filed
            app["worker"].replies.add(entry.id)
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
                              "proposals": [asdict(p) for p in proposals],
                              "progress": app["worker"].progress.get(eid)})


async def _in_progress(app, eid: str) -> bool:
    f = await t(app["store"].get_filing, eid)
    return bool(f and f.status in ("queued", "running"))


async def retry_filing(request: web.Request) -> web.Response:
    app, eid = request.app, request.match_info["entry_id"]
    await _require_entry(app, eid)
    if await _in_progress(app, eid):
        return err(409, "filing in progress")
    await t(app["worker"].mark_queued, eid)
    app["worker"].replies.add(eid)  # the owner pressed RETRY: they're waiting on it
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
    app["worker"].replies.add(eid)

    async def run() -> None:
        try:
            await app["worker"].correct(eid, text, queued=True)
        except Exception as e:
            log.exception("correction of %s failed", eid)
            await app["broadcast"]("filing.failed", {"entry_id": eid, "error": str(e)[:500]})
            await app["worker"].reply_to(eid, False)  # failed before the filing started

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
    proposals, failed = store.list_proposals(), store.list_filings("failed")
    # Entries to review, as Today's TO REVIEW counts them: each with proposals, or a failed filing.
    entries = len({p.entry_id for p in proposals} | {f.entry_id for f in failed})
    return {"items": store.list_items(status=LIVE_STATUSES), "proposals": len(proposals),
            "failed": len(failed), "entries": entries, "map": load_map(paths.brain),
            "usage": app["usage"].view(settings["usage_warn_pct"], settings["fable_usage_url"]),
            "agents": app["agents"].rows(), "settings": settings, "now": datetime.now(),
            "requests": _open_requests(store)}


def _open_requests(store: Store) -> int:
    """Measurement requests waiting for the owner (phase 3): open and not ignored."""
    hidden = store.hidden_refs("request")
    return sum(1 for r in store.list_requests() if r["status"] == "open" and r["id"] not in hidden)


async def get_widget(request: web.Request) -> web.Response:
    app = request.app
    state = await t(_widget_state, app)
    state["arm_reachable"] = await app["arm"].reachable(state["settings"]["arm_url"])
    summary = await t(build_widget, state)
    summary.update(drive=await t(drive, app), warnings=list(app["warnings"]))
    return web.json_response(summary)


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
        url = f"http://127.0.0.1:{app['port']}/" + (f"#/{view}" if view else "")
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


async def _stream(request: web.Request, hits, chunks, on_answer=None) -> web.StreamResponse:
    """hits, the answer's deltas, then done. on_answer(text) (an answer that streamed without an
    error) returns ATLAS's reply, sent as one more `reply` event before done."""
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                       "Cache-Control": "no-cache"})
    await resp.prepare(request)
    try:
        if hits is not None:
            await _event(resp, "hits", [asdict(h) for h in hits])
        answer, failed = [], False
        try:
            async with contextlib.aclosing(chunks) as stream:
                async for chunk in stream:
                    answer.append(chunk if isinstance(chunk, str) else "")
                    await _event(resp, "delta", chunk)
        except (ClaudeError, ValueError, OSError) as e:
            failed = True
            await _event(resp, "error", str(e) or type(e).__name__)
        if on_answer is not None and not failed:
            await _event(resp, "reply", await on_answer("".join(answer)))
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

    async def replied(answer: str) -> str:  # ?reply=1: the ATLAS screen asked (VI4)
        if not hits or answer.strip() == NOTHING:
            return await atlas_reply(app, "nothing")
        return await atlas_reply(app, "found", len(hits))

    return await _stream(request, hits, quick_answer(
        app["runner"], q, hits, settings, paths.prompts, paths.brain),
        replied if request.query.get("reply") == "1" else None)


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


async def record_usage(app: web.Application, payload: dict) -> None:
    """A status line post or one of the hub's own claude runs: store it, tell the pages."""
    if await t(app["usage"].update, payload):
        await app["broadcast"]("usage.changed", await t(_usage_view, app))


async def post_usage(request: web.Request) -> web.Response:
    body = await body_dict(request)
    # Settings' "Last usage update" says whether the status line integration posts; the hub's
    # own runs feed the meter too, so they can't be the clock for it (memory only).
    request.app["statusline_seen"]["at"] = datetime.now().isoformat(timespec="seconds")
    await record_usage(request.app, body)
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


def _map_committer(app: web.Application):
    def commit(m: BrainMap) -> None:
        save_map(app["paths"].brain, m)
        app["on_commit"](app["repo"].commit_all("map edit"))
    return commit


async def link_agent(request: web.Request) -> web.Response:
    """The "link to <name>" button on an Agents row: its folder joins that project on this OS
    (dual-boot design §6.4). Same JSON and local_only rules as every other POST."""
    app = request.app
    slug = (await body_dict(request)).get("slug")
    if not isinstance(slug, str) or not slug:
        raise ValueError("slug must be a project slug")
    try:
        row = await t(app["agents"].link, request.match_info["session_id"], slug, _map_committer(app))
    except KeyError:
        raise web.HTTPNotFound(reason="unknown session") from None
    if row is None:
        return err(409, "this session has no link to that project any more")
    await app["broadcast"]("map.changed", {})
    await app["broadcast"]("agents.changed", {})
    return web.json_response(row)


async def delete_agent(request: web.Request) -> web.Response:
    app = request.app
    await t(app["agents"].remove, request.match_info["session_id"])
    await app["broadcast"]("agents.changed", {})
    return web.Response(status=204)


def _integration_view(app: web.Application) -> dict:
    # No legacy adoption here: moving the passthrough file on a GET would cut the live status line
    # off from it until a reinstall. install/uninstall adopt and rewrite the line together.
    view = ci.status(app["claude_settings_path"], port=app["port"])
    return {**view, "last_hook_at": app["agents"].last_event_at(), "last_usage_at": app["statusline_seen"].get("at")}


async def get_integration(request: web.Request) -> web.Response:
    try:
        return web.json_response(await t(_integration_view, request.app))
    except ci.IntegrationError as e:
        raise web.HTTPBadRequest(reason=str(e)) from None


def _integration_change(app: web.Application, install: bool) -> dict:
    paths, target, state = app["paths"], app["claude_settings_path"], osdirs.state_dir()
    if install:
        python, script = ci.hook_paths(state)
        ci.install(target, python=python, script=script, port=app["port"], state_dir=state, data_dir=paths.data)
    else:
        ci.uninstall(target, state_dir=state, data_dir=paths.data)
    return _integration_view(app)


def repair_legacy_integration(app: web.Application, data_dir: Path | None) -> tuple[bool, str | None]:
    """The pre-rename hooks replaced at the current paths (rename spec §2.2), with exactly what
    _integration_change's install uses: the target settings, this OS's state folder (its
    hook_paths()) and the hub's port; `data_dir` is the shared data/ folder (None in reduced and
    read-only mode: nothing is adopted from it). Then the shell's compat link or copy at the old
    state folder goes, once nothing names it. Only for the owner's real hub (runs_from_own_slot):
    a dev or manual hub never touches Claude Code's settings by itself. Returns (reinstalled,
    the compat path removed)."""
    state = osdirs.state_dir()
    if not ci.runs_from_own_slot(state):
        return False, None
    target = app.get("claude_settings_path") or ci.default_settings_path()
    repaired = ci.repair_legacy(target, port=app["port"], state_dir=state, data_dir=data_dir)
    return repaired, ci.remove_legacy_compat(target, state)


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


# -- Claude Code sync (plan Task 11): the last summary and the per-OS keys -------------------
def _claude_sync_view() -> dict:
    return claude_sync.view(osdirs.state_dir(), claude_sync.default_shared_dir())


async def get_claude_sync(request: web.Request) -> web.Response:
    """The last claude-sync summary (<state>/claude-sync.json, None before the first run), whether
    the shared store is there, and the per-OS settings keys (the defaults and the owner's)."""
    return web.json_response(await t(_claude_sync_view))


async def put_claude_sync_per_os_keys(request: web.Request) -> web.Response:
    """{"keys": [...]}: the owner's extra per-OS keys, kept in the shared store's
    settings.shared.json. 409 while the store isn't available, or while a sync runs."""
    body = await body_dict(request)
    state, shared = osdirs.state_dir(), claude_sync.default_shared_dir()
    try:
        await t(claude_sync.set_per_os_keys, shared, body.get("keys"), state)
    except (claude_sync.StoreUnavailable, claude_sync.Busy) as e:
        return err(409, str(e))
    return web.json_response(await t(_claude_sync_view))


# -- reduced and read-only modes (dual-boot design §6.5 items 2 and 3) -----------
def drive(app: web.Application, problems: list | None = None) -> dict | None:
    """The drive banner: None while the root is fine, else its state, the message and how many
    captures this OS holds for this root. Held files that can't be read are described in
    `problems`."""
    status = app["root_status"]
    if status.state in ("ok", "fresh"):
        return None
    return {"state": status.state, "message": status.message,
            "held": len(holding.held(osdirs.state_dir(), problems, app["paths"]))}


async def hold_entry(request: web.Request) -> web.Response:
    """POST /api/entries while the root isn't writable: the capture goes to this OS's holding
    inbox, on disk before the answer, marked with this root. It reaches the raw log, with its own
    id and ts, at the first start of a hub on this root once it is writable."""
    body = await body_dict(request)
    text, source = body.get("text"), body.get("source")
    if not isinstance(text, str) or not text.strip():
        return err(400, "entry text is blank")
    if source not in CAPTURE_SOURCES:
        return err(400, f"source must be one of {list(CAPTURE_SOURCES)}")
    record = await t(holding.hold, osdirs.state_dir(), request.app["paths"], text, source, datetime.now())
    return web.json_response({"entry_id": record["id"], "held": True})


async def ignored(request: web.Request) -> web.Response:
    """Hook events and usage while the root isn't usable: accepted and dropped, so the owner's
    Claude Code hooks stay fast and silent. The JSON rules still apply."""
    await body_dict(request)
    return web.Response(status=204)


async def unavailable(request: web.Request) -> web.Response:
    app = request.app
    return web.json_response({"error": app["root_status"].message, "drive": await t(drive, app)},
                             status=503)


async def reduced_state(request: web.Request) -> web.Response:
    """GET /api/state in reduced mode: setup_done is null and `drive` is set, so the UI shows the
    banner and the capture box and never offers setup."""
    app, held_problems = request.app, []
    drive_ = await t(drive, app, held_problems)
    return web.json_response({"setup_done": None, "drive": drive_, "warnings": list(app["warnings"]),
                              "os": osdirs.os_name(), "log_error": None,
                              "startup_error": held_problems or None})


async def reduced_widget(request: web.Request) -> web.Response:
    """GET /api/widget in reduced mode: the banner is the synopsis; there are no tiles."""
    app = request.app
    return web.json_response({"synopsis": app["root_status"].message, "attention": True, "tiles": [],
                              "drive": await t(drive, app), "warnings": list(app["warnings"])})


# Opening a page writes nothing, so it works in every mode (the widget's "Open in ATLAS").
_HARMLESS_POSTS = ("/api/open", "/api/atlas/handoff", "/api/atlas/handoff/take")  # memory only
_TABLET_CAPTURE = re.compile(r"/api/tablet/notes|/api/tablet/requests/[^/]+/answer")
_IGNORED_POSTS = ("/api/hooks/event", "/api/usage")


@web.middleware
async def readonly_guard(request: web.Request, handler):
    """Read-only root: reads work (Today, journal, recall); a capture goes to the holding inbox;
    hook events and usage are ignored; anything else that would write is refused with the
    reason (503)."""
    method, path = request.method, request.path
    if method in ("GET", "HEAD", "OPTIONS") or (method == "POST" and path in _HARMLESS_POSTS):
        return await handler(request)
    if method == "POST" and path == "/api/entries":
        return await hold_entry(request)
    if method == "POST" and _TABLET_CAPTURE.fullmatch(path):
        return await handler(request)  # a tablet note or answer: its handler holds it (phase 3)
    if method == "POST" and path in _IGNORED_POSTS:
        return await ignored(request)
    if request.get("lan") and "device" not in request:
        return err(503, "ATLAS can't take that right now")  # nothing about the drive before pairing
    return await unavailable(request)


def add_reduced_routes(app: web.Application) -> None:
    """The reduced app's routes (no hub.db, no git, nothing written under the root)."""
    app.add_routes([
        web.get("/", index), web.get("/widget", widget_page), web.get("/api/state", reduced_state),
        web.post("/api/entries", hold_entry),
        web.post("/api/hooks/event", ignored), web.post("/api/usage", ignored),
        web.get("/api/widget", reduced_widget), web.post("/api/open", post_open),
        web.get("/ws", ws_handler),
        web.route("*", "/api/{tail:.*}", unavailable),
    ])


def add_routes(app: web.Application) -> None:
    app["atlas_handoff"] = {}  # the widget's question for the ATLAS screen (post_handoff)
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
        web.post("/api/atlas/handoff", post_handoff), web.post("/api/atlas/handoff/take", take_handoff),
        web.post("/api/usage", post_usage), web.get("/api/usage", get_usage),
        web.post("/api/hooks/event", post_hook_event), web.get("/api/agents", get_agents),
        web.patch("/api/agents/{session_id}", patch_agent),
        web.post("/api/agents/{session_id}/link", link_agent),
        web.delete("/api/agents/{session_id}", delete_agent),
        web.get("/api/integration", get_integration),
        web.post("/api/integration/install", post_integration_install),
        web.post("/api/integration/uninstall", post_integration_uninstall),
        web.get("/api/claude-sync", get_claude_sync),
        web.put("/api/claude-sync/per-os-keys", put_claude_sync_per_os_keys),
        web.get("/api/widget", get_widget), web.get("/widget", widget_page),
        web.post("/api/open", post_open),
        web.get("/ws", ws_handler),
    ])
