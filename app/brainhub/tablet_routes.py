"""Phase 3 HTTP: pairing and paired devices. The tablet's own API (/api/tablet/*) is reachable over
the LAN site through tablet.lan_guard; everything else here is PC-only (loopback)."""
from __future__ import annotations

import asyncio
import re

from aiohttp import web

from .agents import resolve_project
from .brainmap import load_map
from .routes import body_dict, setup_done
from .tablet import COOKIE, COOKIE_MAX_AGE

t = asyncio.to_thread
TITLE_MAX, ITEM_MAX, ITEMS_MAX = 80, 120, 20
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


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


# -- the tablet ---------------------------------------------------------------
async def pair(request: web.Request) -> web.Response:
    body = await body_dict(request)
    app = request.app
    token = await t(app["pairing"].redeem, body.get("code"), body.get("name"))
    device = await t(app["pairing"].device, token)
    resp = web.json_response({"device": device})
    resp.set_cookie(COOKIE, token, max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="Strict")
    await app["broadcast"]("devices.changed", {})
    return resp


async def me(request: web.Request) -> web.Response:
    """Who the tablet is paired as (None on the PC, which needs no pairing)."""
    return web.json_response({"device": request.get("device")})


def add_tablet_routes(app: web.Application) -> None:
    app.add_routes([
        web.post("/api/devices/pairing-code", pairing_code),
        web.get("/api/devices", list_devices),
        web.delete("/api/devices/{device_id}", delete_device),
        web.post("/api/requests", post_request),
        web.get("/api/requests", list_requests),
        web.post("/api/requests/{request_id}/ignore", ignore_request),
        web.post("/api/requests/{request_id}/restore", restore_request),
        web.post("/api/tablet/pair", pair),
        web.get("/api/tablet/me", me),
    ])
