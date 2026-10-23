"""Phase 3 HTTP: pairing and paired devices. The tablet's own API (/api/tablet/*) is reachable over
the LAN site through tablet.lan_guard; everything else here is PC-only (loopback)."""
from __future__ import annotations

import asyncio

from aiohttp import web

from .routes import body_dict
from .tablet import COOKIE, COOKIE_MAX_AGE

t = asyncio.to_thread


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
        web.post("/api/tablet/pair", pair),
        web.get("/api/tablet/me", me),
    ])
