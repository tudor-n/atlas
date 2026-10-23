"""Phase 3, the workbench tablet's way in (design §3, §4): pairing codes and paired-device tokens,
the LAN guard, the LAN site that follows the laptop's home address, and the change feed the tablet
long-polls (it has no WebSocket)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import secrets
import socket
import threading
import time
from collections.abc import Callable
from datetime import datetime

from aiohttp import web

log = logging.getLogger(__name__)

COOKIE = "bh_device"
COOKIE_MAX_AGE = 10 * 365 * 24 * 3600  # "paired once, remembered" (spec §13.3)
CODE_TTL_S = 300
CODE_TRIES = 5
DEFAULT_NAME = "Workbench tablet"
TOUCH_EVERY_S = 60  # last_seen is written at most this often per device, not on every poll
LAN_RECHECK_S = 15.0  # how often the LAN site looks at the address (the root watch's interval)


class PairingError(ValueError):
    """A wrong, expired or missing pairing code: a 400 with this message."""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Pairing:
    """One pairing code at a time, made in PC Settings: 6 digits, CODE_TTL_S, one use, CODE_TRIES
    wrong tries. It lives in memory only (a hub restart cancels it). Redeeming it stores a new
    device with the SHA-256 of a random token; the token itself only ever goes to the tablet."""

    def __init__(self, store, now: Callable[[], float] = time.monotonic):
        self.store, self.now = store, now
        self._code: str | None = None
        self._expires = 0.0
        self._tries = 0
        self._touched: dict[str, float] = {}
        self._lock = threading.Lock()  # redeem runs in worker threads: one at a time

    def new_code(self) -> dict:
        self._code = f"{secrets.randbelow(10 ** 6):06d}"
        self._expires, self._tries = self.now() + CODE_TTL_S, 0
        return {"code": self._code, "expires_in": CODE_TTL_S}

    def _clear(self) -> None:
        self._code, self._tries = None, 0

    # One message for every failure: it never tells a neighbour on the Wi-Fi whether a code is live.
    REFUSED = "that code isn't right, or it ran out: make a new one in BrainHub's Settings on the PC"

    def redeem(self, code, name) -> str:
        with self._lock:
            if self._code is None or self.now() > self._expires:
                self._clear()
                raise PairingError(self.REFUSED)
            given = code.strip().encode("utf-8") if isinstance(code, str) else b""
            if not hmac.compare_digest(given, self._code.encode("ascii")):
                self._tries += 1
                if self._tries >= CODE_TRIES:
                    self._clear()
                raise PairingError(self.REFUSED)
            self._clear()
        name = name.strip()[:40] if isinstance(name, str) and name.strip() else DEFAULT_NAME
        token = secrets.token_urlsafe(32)
        self.store.add_device(name, token_hash(token))
        return token

    def device(self, token) -> dict | None:
        """The paired device a cookie belongs to (its last_seen kept roughly current), or None."""
        if not isinstance(token, str) or not token:
            return None
        dev = self.store.device_by_hash(token_hash(token))
        if dev is not None:
            now = self.now()
            if now - self._touched.get(dev["id"], -TOUCH_EVERY_S) >= TOUCH_EVERY_S:
                self._touched[dev["id"]] = now
                stamp = datetime.now().replace(microsecond=0).isoformat()
                self.store.touch_device(dev["id"], stamp)
                dev["last_seen"] = stamp
        return dev


def holds_address(addr: str) -> bool:
    """Does this machine have `addr` on one of its interfaces right now? A UDP socket "connected" to
    an address takes the source address the OS would use to reach it; for one of our own addresses
    that is the address itself (checked on Kubuntu; the same routing rule holds on Windows).
    Nothing is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((addr, 9))
            return s.getsockname()[0] == addr
    except OSError:
        return False


class LanSite:
    """The hub's second listener (design §3): `tablet_address`:port, only while `tablet_lan` is on
    and the laptop holds that address, so on another network (uni, a café) the hub stays on
    loopback. serve() hands over the AppRunner; sync() runs at start, every LAN_RECHECK_S and
    after a settings change. Binding the one address (not 0.0.0.0) never clashes with the loopback
    bind and never exposes the hub on Docker bridges."""

    def __init__(self, settings: Callable[[], dict], port: Callable[[], int],
                 holds: Callable[[str], bool] = holds_address):
        self._settings, self._port, self._holds = settings, port, holds
        self.runner: web.AppRunner | None = None
        self.bound: str | None = None
        self.error: str | None = None
        self._site: web.TCPSite | None = None
        self._lock = asyncio.Lock()
        # Every address the LAN site has listened on: a kept-alive connection outlives site.stop(),
        # and must still be judged a LAN request (then refused: nothing is bound to its address).
        self._used: set[str] = set()

    def configure(self, *, settings: Callable[[], dict] | None = None,
                  holds: Callable[[str], bool] | None = None) -> None:
        """Tests swap the settings source or the address check."""
        if settings is not None:
            self._settings = settings
        if holds is not None:
            self._holds = holds

    def wanted(self) -> str | None:
        s = self._settings()
        addr = s.get("tablet_address") or ""
        return addr if s.get("tablet_lan") and addr and self._holds(addr) else None

    async def sync(self) -> None:
        if self.runner is None:
            return  # not serving (tests through aiohttp_client): no LAN, ever
        async with self._lock:
            want = await asyncio.to_thread(self.wanted)
            if want == self.bound:
                return
            if self._site is not None:
                await self._site.stop()
                log.warning("tablet over Wi-Fi: stopped listening on %s", self.bound)
                self._site, self.bound = None, None
            if want is None:
                return
            site = web.TCPSite(self.runner, want, self._port())
            try:
                await site.start()
            except OSError as e:
                try:
                    await site.stop()  # start() registered it before the bind failed
                except Exception:
                    pass
                if self.error != str(e):
                    log.warning("tablet over Wi-Fi: can't listen on %s:%s (%s)", want, self._port(), e)
                self.error = str(e)
                return
            self._site, self.bound, self.error = site, want, None
            self._used.add(want)
            log.warning("tablet over Wi-Fi: listening on %s:%s", want, self._port())

    async def stop(self) -> None:
        async with self._lock:
            if self._site is not None:
                await self._site.stop()
            self._site, self.bound = None, None

    def status(self) -> dict:
        return {"listening": self.bound, "error": self.error}

    def is_lan(self, request: web.Request) -> bool:
        """Did this request arrive on the LAN site? Judged by the local socket it came in on, not
        by the peer: any non-loopback socket counts, and so does any address the LAN site has
        used (in tests, 127.0.0.2)."""
        sock = request.transport.get_extra_info("sockname") if request.transport else None
        if not sock:
            return True  # unknown: judged as the LAN, the stricter side
        ip = sock[0]
        if ip in self._used:
            return True
        try:
            return not ipaddress.ip_address(ip).is_loopback
        except ValueError:
            return True


async def watch_lan(app: web.Application) -> None:
    while True:
        try:
            await app["lan"].sync()
        except Exception:
            log.exception("tablet LAN site check failed")
        await asyncio.sleep(LAN_RECHECK_S)


class TabletFeed:
    """A version number the tablet long-polls. Every hub broadcast that changes what it shows bumps
    it (create_app's broadcast); a waiter wakes at once and fetches the new state."""
    QUIET = ("filing.progress", "filing.started", "entry.saved")

    def __init__(self):
        self.version = 1
        self._event = asyncio.Event()

    def bump(self) -> None:
        self.version += 1
        event, self._event = self._event, asyncio.Event()
        event.set()

    async def wait(self, since: int, timeout: float) -> int:
        if since != self.version:
            return self.version
        event = self._event
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        return self.version


# What a LAN connection may reach (design §4.3: least privilege, even when paired).
def lan_path_allowed(path: str) -> bool:
    return path == "/tablet" or path.startswith("/tablet/") or path.startswith("/api/tablet/")


def _lan_needs_device(path: str) -> bool:
    return path.startswith("/api/tablet/") and path != "/api/tablet/pair"


def _deny(status: int, message: str, **extra) -> web.Response:
    return web.json_response({"error": message, **extra}, status=status)


@web.middleware
async def lan_guard(request: web.Request, handler):
    """Outermost: a request on the LAN site may reach only the tablet surface, at the LAN address,
    from its own origin, and (past the page and pairing) only with a paired device's cookie.
    Loopback requests go on to local_only untouched."""
    lan: LanSite | None = request.app.get("lan")
    if lan is None or not lan.is_lan(request):
        return await handler(request)
    request["lan"] = True
    if not lan_path_allowed(request.path) or "/.." in request.path or "//" in request.path:
        return _deny(403, "not available over Wi-Fi")
    if lan.bound is None or request.url.host != lan.bound:
        return _deny(403, "forbidden host")
    origin = request.headers.get("Origin")
    if origin is not None and origin != f"http://{request.host}":
        return _deny(403, "forbidden origin")
    if _lan_needs_device(request.path):
        device = await asyncio.to_thread(request.app["pairing"].device, request.cookies.get(COOKIE))
        if device is None:
            return _deny(401, "this tablet isn't paired", pair=True)
        request["device"] = device
    return await handler(request)
