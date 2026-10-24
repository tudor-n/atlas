"""Phase 3: pairing, the LAN guard, the LAN site that follows the home address, the change feed.

The LAN is stood in for by a second listener on 127.0.0.2 (the whole 127/8 is loopback on Linux
and Windows), which the hub is told is its tablet address."""
import asyncio
import socket
import sys

import aiohttp
import pytest
from aiohttp import web

from atlas import tablet
from atlas.server import create_app
from tests.fakes import Clock
from tests.test_server import MAP, make_runner

LAN = "127.0.0.2"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- pairing ------------------------------------------------------------------------------------
def test_pairing_code_is_six_digits_and_single_use(store):
    p = tablet.Pairing(store, now=Clock(1000))
    code = p.new_code()["code"]
    assert len(code) == 6 and code.isdigit()
    token = p.redeem(code, "Workbench tablet")
    dev = p.device(token)
    assert dev["name"] == "Workbench tablet" and store.device_by_hash(tablet.token_hash(token))
    assert token not in str(store.list_devices())
    with pytest.raises(tablet.PairingError, match="isn't right"):
        p.redeem(code, "again")  # used up


def test_pairing_code_expires(store):
    clock = Clock(1000)
    p = tablet.Pairing(store, now=clock)
    code = p.new_code()["code"]
    clock.t += tablet.CODE_TTL_S + 1
    with pytest.raises(tablet.PairingError, match="isn't right"):
        p.redeem(code, "x")


def test_five_wrong_codes_cancel_it(store):
    p = tablet.Pairing(store, now=Clock(1000))
    code = p.new_code()["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(tablet.CODE_TRIES):
        with pytest.raises(tablet.PairingError, match="isn't right"):
            p.redeem(wrong, "x")
    with pytest.raises(tablet.PairingError, match="isn't right"):
        p.redeem(code, "x")  # even the right one is gone now


def test_device_lookup(store):
    p = tablet.Pairing(store, now=Clock(1000))
    assert p.device(None) is None and p.device("") is None and p.device("nope") is None
    token = p.redeem(p.new_code()["code"], "  ")
    assert p.device(token)["name"] == "Workbench tablet"  # a blank name gets the default


# -- the address and the feed -------------------------------------------------------------------
def test_holds_address():
    assert tablet.holds_address("127.0.0.1") is True
    assert tablet.holds_address("192.0.2.1") is False  # TEST-NET-1: never one of ours


async def test_feed_wakes_waiters_and_times_out():
    feed = tablet.TabletFeed()
    v = feed.version
    assert await feed.wait(v - 1, 5) == v  # behind: answers at once
    waiter = asyncio.create_task(feed.wait(v, 5))
    await asyncio.sleep(0.01)
    feed.bump()
    assert await waiter == v + 1
    assert await feed.wait(v + 1, 0.05) == v + 1  # nothing new: the timeout answers


# -- the LAN site and the guard, over real sockets ----------------------------------------------
class Settings:
    def __init__(self, **kw):
        self.d = {"tablet_lan": True, "tablet_address": LAN, **kw}

    def __call__(self):
        return self.d


@pytest.fixture
async def hub(paths):
    """A full app served on 127.0.0.1:<port>, with its LAN site allowed on 127.0.0.2:<port>."""
    app = create_app(paths, runner=make_runner(), start_workers=False)
    port = free_port()
    app["port"] = port
    settings = Settings()
    app["lan"].configure(settings=settings, holds=lambda a: a == LAN)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    app["lan"].runner = runner
    await app["lan"].sync()
    jar = aiohttp.CookieJar(unsafe=True)  # cookies for IP hosts
    async with aiohttp.ClientSession(cookie_jar=jar) as http:
        pc, lan = f"http://127.0.0.1:{port}", f"http://{LAN}:{port}"
        async with http.post(pc + "/api/setup", json={"map": MAP, "backup_remote": None}) as r:
            assert r.status == 200
        yield app, http, pc, lan, settings
    await runner.cleanup()


async def _pair(http, pc, lan, name="Workbench tablet"):
    async with http.post(pc + "/api/devices/pairing-code", json={}) as r:
        code = (await r.json())["code"]
    async with http.post(lan + "/api/tablet/pair", json={"code": code, "name": name}) as r:
        assert r.status == 200, await r.text()
        return r


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has only 127.0.0.1 on lo0")
async def test_lan_site_follows_the_switch(hub):
    app, http, pc, lan, settings = hub
    assert app["lan"].bound == LAN
    async with http.get(lan + "/api/tablet/me") as r:
        assert r.status == 401  # listening, and the guard answers
    settings.d["tablet_lan"] = False
    await app["lan"].sync()
    assert app["lan"].bound is None
    async with http.get(lan + "/api/tablet/me") as r:
        assert r.status == 403  # a kept-alive connection outlives the listener: refused
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as fresh:
        with pytest.raises(aiohttp.ClientConnectionError):
            async with fresh.get(lan + "/api/tablet/me"):
                pass
    async with http.get(pc + "/api/state") as r:
        assert r.status == 200  # the PC side never changes
    settings.d["tablet_lan"] = True
    app["lan"].configure(holds=lambda a: False)  # the laptop left home
    await app["lan"].sync()
    assert app["lan"].bound is None


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has only 127.0.0.1 on lo0")
async def test_pairing_over_the_lan_sets_a_strict_cookie(hub):
    app, http, pc, lan, _ = hub
    r = await _pair(http, pc, lan)
    morsel = r.cookies[tablet.COOKIE]
    assert morsel["httponly"] and morsel["samesite"] == "Strict" and int(morsel["max-age"]) > 3 * 10**8
    async with http.get(lan + "/api/tablet/me") as r:
        assert r.status == 200 and (await r.json())["device"]["name"] == "Workbench tablet"
    async with http.get(pc + "/api/devices") as r:
        devices = await r.json()
    assert [d["name"] for d in devices] == ["Workbench tablet"] and devices[0]["last_seen"]
    async with http.delete(pc + f"/api/devices/{devices[0]['id']}") as r:
        assert r.status == 204
    async with http.get(lan + "/api/tablet/me") as r:
        assert r.status == 401 and (await r.json())["pair"] is True  # revoked: back to pairing


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has only 127.0.0.1 on lo0")
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/today"), ("GET", "/api/state"), ("GET", "/api/devices"), ("GET", "/"),
    ("POST", "/api/devices/pairing-code"), ("POST", "/api/requests"), ("GET", "/static/js/app.js"),
    ("GET", "/widget"), ("POST", "/api/entries"), ("GET", "/api/ask?q=x"), ("GET", "/ws"),
    ("GET", "/api/tablet-notes"), ("GET", "/api/tablet-lan"), ("POST", "/api/tablet-notes/e-1/file"),
    ("GET", "/api/requests"), ("GET", "/api/tablet/../today"), ("GET", "/api/tablet%2F..%2Ftoday"),
    ("GET", "/api/voice"), ("POST", "/api/voice/pause"), ("POST", "/api/voice/sample"),
])
async def test_the_lan_reaches_only_the_tablet_even_when_paired(hub, method, path):
    app, http, pc, lan, _ = hub
    await _pair(http, pc, lan)
    async with http.request(method, lan + path, json={} if method == "POST" else None) as r:
        assert r.status == 403, (method, path, r.status)


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has only 127.0.0.1 on lo0")
async def test_lan_host_and_origin_checks(hub):
    app, http, pc, lan, _ = hub
    await _pair(http, pc, lan)
    async with http.get(lan + "/api/tablet/me", headers={"Host": "evil.example"}) as r:
        assert r.status == 403  # DNS rebinding
    async with http.get(lan + "/api/tablet/me", headers={"Origin": "http://evil.example"}) as r:
        assert r.status == 403
    async with http.get(lan + "/api/tablet/me", headers={"Origin": lan}) as r:
        assert r.status == 200


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has only 127.0.0.1 on lo0")
async def test_wrong_codes_over_the_lan(hub):
    app, http, pc, lan, _ = hub
    async with http.post(lan + "/api/tablet/pair", json={"code": "123456"}) as r:
        assert r.status == 400 and "isn't right" in (await r.json())["error"]
    async with http.post(lan + "/api/tablet/pair", data="{}", headers={"Content-Type": "text/plain"}) as r:
        assert r.status == 415


async def test_pc_can_open_the_tablet_api_without_pairing(aiohttp_client, paths):
    app = create_app(paths, runner=make_runner(), start_workers=False)
    c = await aiohttp_client(app)
    r = await c.get("/api/tablet/me")
    assert r.status == 200 and (await r.json())["device"] is None  # localhost: the owner's preview
    code = await (await c.post("/api/devices/pairing-code", json={})).json()
    assert len(code["code"]) == 6 and code["expires_in"] == tablet.CODE_TTL_S


# -- review -----------------------------------------------------------------------------------------
def test_odd_codes_are_wrong_codes_and_the_message_gives_nothing_away(store):
    p = tablet.Pairing(store, now=Clock(1000))
    with pytest.raises(tablet.PairingError) as none:
        p.redeem("123456", "x")
    p.new_code()
    with pytest.raises(tablet.PairingError) as wrong:
        p.redeem("１２３４５６", "x")  # full-width digits: no TypeError, just wrong
    assert str(none.value) == str(wrong.value)  # no oracle for whether a code is live


def test_is_lan_fails_closed_without_a_socket():
    class Req:
        transport = None
    assert tablet.LanSite(lambda: {}, lambda: 1).is_lan(Req()) is True


async def test_a_failed_bind_leaks_no_site(paths):
    app = create_app(paths, runner=make_runner(), start_workers=False)
    app["port"] = free_port()
    app["lan"].configure(settings=Settings(tablet_address="192.0.2.1"), holds=lambda a: True)  # never ours: bind fails
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        app["lan"].runner = runner
        for _ in range(4):
            await app["lan"].sync()
        assert app["lan"].bound is None and app["lan"].error and len(runner.sites) == 0
    finally:
        await runner.cleanup()


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has only 127.0.0.1 on lo0")
async def test_the_cookie_also_has_expires(hub):
    app, http, pc, lan, _ = hub
    r = await _pair(http, pc, lan)
    assert r.cookies[tablet.COOKIE]["expires"]
