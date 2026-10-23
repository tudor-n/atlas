"""Phase 3 HTTP: pairing and devices, measurement requests, tablet notes, the tablet's state and
long-poll. The tablet's own API (/api/tablet/*) is reachable over the LAN site through
tablet.lan_guard; everything else here is PC-only (loopback)."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from email.utils import formatdate

from aiohttp import web

from . import holding, osdirs
from .agents import resolve_project
from .brainmap import load_map
from .config import load_settings
from .rawlog import append_entry
from .routes import _today, _usage_view, body_dict, setup_done
from .tablet import COOKIE, COOKIE_MAX_AGE
from .tablet_view import build_tablet_state

log = logging.getLogger(__name__)
t = asyncio.to_thread
TITLE_MAX, ITEM_MAX, ITEMS_MAX = 80, 120, 20
ANSWER_MAX, NOTE_MAX = 200, 20000
WAIT_S = 25.0  # the long-poll's longest wait (design §6.4)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _client_id(body: dict) -> str | None:
    """The tablet's own id for one send (optional): a resend after a lost response carries it again."""
    cid = body.get("client_id")
    if cid is None:
        return None
    if not isinstance(cid, str) or not _CLIENT_ID.fullmatch(cid):
        raise ValueError("client_id must be 8-64 letters, digits, '-' or '_'")
    return cid


def _text(value, limit: int, what: str) -> str:
    """Agent text for the tablet: one line, trimmed, at most `limit` characters."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{what} is longer than {limit} characters")
    if _CONTROL.search(value):
        raise ValueError(f"{what} must be one line, without control characters")
    return value


def _need_setup(app: web.Application) -> None:
    if not setup_done(app):  # nothing may be written under a root before the setup wizard ran
        raise web.HTTPConflict(reason="run setup first")


async def _tablet_changed(app: web.Application, what: str) -> None:
    """Requests and IGNOREs live in hub.db and in brain/_items (so a rebuilt index keeps them)."""
    await t(app["store"].export_tablet, app["paths"].brain)
    await app["broadcast"](what, {})


def _pc_only(request: web.Request) -> None:
    if request.get("lan"):  # lan_guard already refuses these paths; this is the belt to its braces
        raise web.HTTPForbidden(reason="not available over Wi-Fi")


# -- PC: pairing codes and devices --------------------------------------------
async def pairing_code(request: web.Request) -> web.Response:
    _pc_only(request)
    return web.json_response(request.app["pairing"].new_code())


async def list_devices(request: web.Request) -> web.Response:
    _pc_only(request)
    return web.json_response(await t(request.app["store"].list_devices))


async def delete_device(request: web.Request) -> web.Response:
    _pc_only(request)
    if not await t(request.app["store"].delete_device, request.match_info["device_id"]):
        raise web.HTTPNotFound(reason="unknown device")
    await request.app["broadcast"]("devices.changed", {})
    return web.Response(status=204)


def this_address() -> str | None:
    """This laptop's address on its current network (the source address of its default route; a
    UDP "connect" to a TEST-NET address sends nothing), if it is a private one: Settings offers it
    as the tablet address."""
    import socket
    from .config import validate_setting
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            addr = sock.getsockname()[0]
        validate_setting("tablet_address", addr)
        return addr
    except (OSError, ValueError):
        return None


async def tablet_lan(request: web.Request) -> web.Response:
    """Settings → Tablet: the switch, the address, whether the hub listens there right now."""
    _pc_only(request)
    app = request.app
    s = await t(load_settings, app["paths"])
    return web.json_response({"tablet_lan": s["tablet_lan"], "tablet_address": s["tablet_address"],
                              "tablet_units": s["tablet_units"], "port": app["port"],
                              "this_address": await t(this_address), **app["lan"].status()})


# -- PC: measurement requests (spec §7.3; an agent posts them with brainhub-request) --------
def _request_view(r: dict, hidden: set[str]) -> dict:
    return {**r, "hidden": r["id"] in hidden}


async def post_request(request: web.Request) -> web.Response:
    _pc_only(request)
    app = request.app
    _need_setup(app)
    body = await body_dict(request)
    title = _text(body.get("title"), TITLE_MAX, "title")
    items = body.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= ITEMS_MAX:
        raise ValueError(f"items must be a list of 1 to {ITEMS_MAX} measurements")
    items = [_text(v, ITEM_MAX, f"item {n}") for n, v in enumerate(items, 1)]
    cwd = body.get("cwd")
    project = project_name = None
    if isinstance(cwd, str) and cwd.strip():
        m = await t(load_map, app["paths"].brain)
        project, project_name, _ = await t(resolve_project, cwd, m)
    req = await t(app["store"].add_request, title, items, project, project_name)
    await _tablet_changed(app, "requests.changed")
    return web.json_response({"request": req}, status=201)


async def list_requests(request: web.Request) -> web.Response:
    _pc_only(request)
    store = request.app["store"]
    hidden = await t(store.hidden_refs, "request")
    return web.json_response([_request_view(r, hidden) for r in await t(store.list_requests)])


async def _set_hidden(request: web.Request, kind: str, ref: str, hide: bool) -> None:
    app = request.app
    _need_setup(app)  # it exports into brain/_items: never before the wizard ran
    store = app["store"]
    exists = (await t(store.get_request, ref)) if kind == "request" else (await t(store.get_entry, ref))
    if exists is None or (kind == "note" and exists.source != "tablet"):
        raise web.HTTPNotFound(reason=f"unknown {kind}")
    await t(store.hide if hide else store.unhide, kind, ref)
    await _tablet_changed(app, "tablet.changed")


async def ignore_request(request: web.Request) -> web.Response:
    _pc_only(request)
    await _set_hidden(request, "request", request.match_info["request_id"], True)
    return web.Response(status=204)


async def restore_request(request: web.Request) -> web.Response:
    _pc_only(request)
    await _set_hidden(request, "request", request.match_info["request_id"], False)
    return web.Response(status=204)


# -- capturing on the tablet: the raw log first, or this OS's holding inbox ---------------------
async def _capture(request: web.Request, text: str, client_id: str | None = None,
                   request_id: str | None = None) -> tuple[str, bool]:
    """Write a tablet capture verbatim (rule 1), source "tablet", indexed for recall and never
    queued for filing (owner): (entry id, held). A read-only or vanished root holds it on this OS
    instead, as a PC capture is held (rule 14). `client_id` is recorded with the entry, so a resend
    of the same capture finds it (see _resent)."""
    app = request.app
    if not setup_done(app) and app["root_status"].state != "ok":
        raise web.HTTPConflict(reason="run setup first")
    if app["root_status"].state != "readonly":
        try:
            entry = await t(append_entry, app["paths"].brain, text, "tablet")
        except OSError as e:
            log.error("the raw log can't be written (%s); the tablet capture is held on this OS", e)
        else:
            try:
                if client_id:
                    await t(app["store"].record_sent, client_id, entry.id)
                await t(app["store"].add_entry, entry)
                await t(app["store"].index_entry, entry)
            except Exception:  # the log has it; startup backfill recovers the index
                log.exception("tablet entry %s saved to the log but not indexed", entry.id)
            return entry.id, False
    record = await t(holding.hold, osdirs.state_dir(), app["paths"], text, "tablet", datetime.now(),
                     request_id)  # an answer held: linked to its request once it is flushed
    return record["id"], True


def answer_text(req: dict, answers: dict[int, str]) -> str:
    """The raw-log text of one set of answers (design §5.3): the request, then each answered item;
    items left empty are named on a last line, so nothing is silently missing."""
    head = f"Measurements: {req['title']}" + (f" ({req['project_name'] or req['project']})"
                                              if req.get("project_name") or req.get("project") else "")
    lines = [head] + [f"- {item}: {answers[i]}" for i, item in enumerate(req["items"]) if i in answers]
    skipped = [item for i, item in enumerate(req["items"]) if i not in answers]
    if skipped:
        lines.append("(not answered: " + "; ".join(skipped) + ")")
    return "\n".join(lines)


# -- the tablet ---------------------------------------------------------------
async def tablet_state(request: web.Request) -> web.Response:
    app = request.app
    if not setup_done(app):
        raise web.HTTPConflict(reason="run setup on the PC first")
    # Read first: a change landing while the state is gathered then leaves the tablet behind the
    # feed, so its next wait answers at once, instead of holding a newer version with older data.
    version = app["feed"].version

    def gather() -> dict:
        store = app["store"]
        settings = load_settings(app["paths"])
        requests = store.list_requests()
        return {"today": _today(app), "requests": requests, "hidden_requests": store.hidden_refs("request"),
                "notes": store.list_entries(source="tablet", limit=50), "hidden_notes": store.hidden_refs("note"),
                "answered": {r["answer_entry"]: r["title"] for r in requests if r.get("answer_entry")},
                "agents": app["agents"].rows(), "usage": _usage_view(app), "arm_url": settings["arm_url"],
                "units": settings["tablet_units"]}
    data = await t(gather)
    reachable = await app["arm"].reachable(data["arm_url"])
    return web.json_response(build_tablet_state(
        version=version, device=request.get("device"), arm_reachable=reachable,
        host=request.host, now=datetime.now(), **data))


async def tablet_wait(request: web.Request) -> web.Response:
    """Long-poll (design §6.4): answers when the feed moves past `since`, or after WAIT_S."""
    try:
        since = int(request.query.get("since", ""))
    except ValueError:
        raise ValueError("since must be an integer") from None
    return web.json_response({"version": await request.app["feed"].wait(since, WAIT_S)})


async def _resent(app: web.Application, client_id: str | None) -> web.Response | None:
    """The answer to a resend whose first try already landed (its response was lost on the way)."""
    if client_id and app["root_status"].state != "readonly":
        entry_id = await t(app["store"].sent_entry, client_id)
        if entry_id:
            return web.json_response({"entry_id": entry_id, "held": False, "duplicate": True})
    return None


async def tablet_note(request: web.Request) -> web.Response:
    body = await body_dict(request)
    text, client_id = body.get("text"), _client_id(body)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the note is blank")
    if len(text) > NOTE_MAX:
        raise ValueError(f"the note is longer than {NOTE_MAX} characters")
    again = await _resent(request.app, client_id)
    if again is not None:
        return again
    entry_id, held = await _capture(request, text, client_id)
    if not held:
        await request.app["broadcast"]("tablet.changed", {})
    await _readback(request.app, "readback_note", text)
    return web.json_response({"entry_id": entry_id, "held": held}, status=200 if held else 201)


async def _readback(app: web.Application, method: str, *args) -> None:
    """The voice reads back what was just saved (phase 4 design §4.1). A resend that already
    landed returns before this, so a line is never read twice."""
    voice = app.get("voice")
    if voice is not None:
        await t(getattr(voice, method), *args)


async def tablet_answer(request: web.Request) -> web.Response:
    app = request.app
    body = await body_dict(request)
    async with app["answer_lock"]:  # check, write and close as one: two answers at once land once
        return await _answer(request, app, body)


async def _answer(request: web.Request, app: web.Application, body: dict) -> web.Response:
    client_id = _client_id(body)
    again = await _resent(app, client_id)  # before "already answered": this very answer may be it
    if again is not None:
        return again
    req = await t(app["store"].get_request, request.match_info["request_id"])
    if req is None:
        raise web.HTTPNotFound(reason="unknown request")
    if req["status"] != "open":
        raise web.HTTPConflict(reason="this request was already answered")
    raw = body.get("answers")
    if not isinstance(raw, dict):
        raise ValueError("answers must be an object: item number to value")
    answers: dict[int, str] = {}
    for key, value in raw.items():
        try:
            i = int(key)
        except ValueError:
            raise ValueError(f"unknown item {key!r}") from None
        if not 0 <= i < len(req["items"]):
            raise ValueError(f"unknown item {key!r}")
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        answers[i] = _text(value, ANSWER_MAX, f"the answer to item {i + 1}")
    if not answers:
        raise ValueError("fill in at least one value")
    entry_id, held = await _capture(request, answer_text(req, answers), client_id, req["id"])
    if not held:
        try:  # the log has it now: whatever fails next, the answer is saved and a resend would duplicate it
            await t(app["store"].set_request_answered, req["id"], entry_id)
            await _tablet_changed(app, "requests.changed")
        except Exception:
            log.exception("answer %s to %s is in the raw log, but closing the request failed", entry_id, req["id"])
    await _readback(app, "readback_answer", req["items"], answers)
    return web.json_response({"entry_id": entry_id, "held": held}, status=200 if held else 201)


async def tablet_ignore(request: web.Request) -> web.Response:
    body = await body_dict(request)
    kind, ref = body.get("kind"), body.get("id")
    if kind not in ("note", "request") or not isinstance(ref, str):
        raise ValueError('kind must be "note" or "request", with an id')
    await _set_hidden(request, kind, ref, True)
    return web.Response(status=204)


# -- PC: the Tablet notes view ------------------------------------------------
async def tablet_notes(request: web.Request) -> web.Response:
    _pc_only(request)
    app = request.app
    show_hidden = request.query.get("hidden") == "1"

    def gather() -> dict:
        store = app["store"]
        hidden_n, hidden_r = store.hidden_refs("note"), store.hidden_refs("request")
        requests = store.list_requests()
        titles = {r["answer_entry"]: r["title"] for r in requests if r.get("answer_entry")}
        entries = store.list_entries(source="tablet", limit=500)
        filings = {f.entry_id: f for f in store.list_filings(entry_ids=[e.id for e in entries])}
        notes = [{"id": e.id, "ts": e.ts.isoformat(), "text": e.text, "hidden": e.id in hidden_n,
                  "answer_to": titles.get(e.id),
                  "filing": filings[e.id].status if e.id in filings else None}
                 for e in entries if show_hidden or e.id not in hidden_n]
        reqs = [{**r, "hidden": r["id"] in hidden_r} for r in requests
                if r["status"] == "open" and (show_hidden or r["id"] not in hidden_r)]
        return {"requests": reqs, "notes": notes,
                "hidden": {"notes": len(hidden_n), "requests": len(hidden_r)}}
    return web.json_response(await t(gather))


async def _tablet_entry(request: web.Request):
    e = await t(request.app["store"].get_entry, request.match_info["entry_id"])
    if e is None or e.source != "tablet":
        raise web.HTTPNotFound(reason="unknown tablet note")
    return e


async def file_tablet_note(request: web.Request) -> web.Response:
    """"File this" (owner): the note goes through the normal filing, and its confirm card."""
    _pc_only(request)
    app = request.app
    _need_setup(app)
    e = await _tablet_entry(request)
    f = await t(app["store"].get_filing, e.id)
    if f and f.status in ("queued", "running"):
        raise web.HTTPConflict(reason="filing in progress")
    await t(app["worker"].mark_queued, e.id)
    app["worker"].queue.put_nowait(e.id)
    await app["broadcast"]("tablet.changed", {})
    return web.json_response({"ok": True}, status=202)


async def ignore_tablet_note(request: web.Request) -> web.Response:
    _pc_only(request)
    await _set_hidden(request, "note", request.match_info["entry_id"], True)
    return web.Response(status=204)


async def restore_tablet_note(request: web.Request) -> web.Response:
    _pc_only(request)
    await _set_hidden(request, "note", request.match_info["entry_id"], False)
    return web.Response(status=204)


# -- the page (served to the LAN without pairing: it holds no data) -------------------------------
ASSETS = {"index.html": "text/html", "tablet.css": "text/css", "tablet.js": "application/javascript",
          "kb.js": "application/javascript", "logic.js": "application/javascript"}


async def tablet_page(request: web.Request) -> web.StreamResponse:
    name = request.match_info.get("name", "index.html")
    path = request.app["paths"].web / "tablet" / name
    if name not in ASSETS or not path.is_file():
        raise web.HTTPNotFound()
    # Fully Kiosk caches hard: always revalidate (a 304 is cheap). The charset is explicit: the old
    # WebKit would read UTF-8 text in a script as Latin-1 otherwise.
    return web.FileResponse(path, headers={"Cache-Control": "no-cache",
                                           "Content-Type": f"{ASSETS[name]}; charset=utf-8"})


async def pair(request: web.Request) -> web.Response:
    body = await body_dict(request)
    app = request.app
    token = await t(app["pairing"].redeem, body.get("code"), body.get("name"))
    device = await t(app["pairing"].device, token)
    resp = web.json_response({"device": device})
    # Expires as well as Max-Age: the old Android WebKit may only honour Expires.
    expires = formatdate(time.time() + COOKIE_MAX_AGE, usegmt=True)
    resp.set_cookie(COOKIE, token, max_age=COOKIE_MAX_AGE, expires=expires, path="/", httponly=True,
                    samesite="Strict")
    await app["broadcast"]("devices.changed", {})
    return resp


async def me(request: web.Request) -> web.Response:
    """Who the tablet is paired as (None on the PC, which needs no pairing)."""
    return web.json_response({"device": request.get("device")})


def add_tablet_routes(app: web.Application) -> None:
    app.add_routes([
        web.get("/api/tablet-lan", tablet_lan),
        web.post("/api/devices/pairing-code", pairing_code),
        web.get("/api/devices", list_devices),
        web.delete("/api/devices/{device_id}", delete_device),
        web.post("/api/requests", post_request),
        web.get("/api/requests", list_requests),
        web.post("/api/requests/{request_id}/ignore", ignore_request),
        web.post("/api/requests/{request_id}/restore", restore_request),
        web.post("/api/tablet/pair", pair),
        web.get("/api/tablet/me", me),
        web.get("/tablet", tablet_page),
        web.get("/tablet/{name}", tablet_page),
        web.get("/api/tablet/state", tablet_state),
        web.get("/api/tablet/wait", tablet_wait),
        web.post("/api/tablet/notes", tablet_note),
        web.post("/api/tablet/requests/{request_id}/answer", tablet_answer),
        web.post("/api/tablet/ignore", tablet_ignore),
        web.get("/api/tablet-notes", tablet_notes),
        web.post("/api/tablet-notes/{entry_id}/file", file_tablet_note),
        web.post("/api/tablet-notes/{entry_id}/ignore", ignore_tablet_note),
        web.post("/api/tablet-notes/{entry_id}/restore", restore_tablet_note),
    ])
