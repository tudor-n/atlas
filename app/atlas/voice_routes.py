"""The voice in the hub (phase 4 design §7, §9): its parts wired into the app, and the PC-only API.

Only the full, writable app has a voice. A read-only root speaks nothing (it can't record what was
said), and the reduced app has no store to read reminders from. Every endpoint lives under
/api/voice, outside /api/tablet/: the LAN guard refuses it (rule 5)."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from aiohttp import web

from . import osdirs
from . import voice_files as vf
from .config import VOICE_IDS, load_settings, validate_setting
from .quotes import quotes_path
from .routes import body_dict
from .voice import TICK_S, Announcer, Speaker

log = logging.getLogger(__name__)
t = asyncio.to_thread
SAMPLE_KEYS = ("voice_blend", "voice_speed", "voice_volume", "voice_model")


def setup_voice(app: web.Application, *, speaker_env: dict | None = None) -> None:
    """app["voice"] (the Announcer) and app["voice_download"]; app["voice"] is None when read-only."""
    paths = app["paths"]
    if app["root_status"].state == "readonly":
        app["voice"], app["voice_download"] = None, None
        return
    state = osdirs.state_dir()

    def changed(data: dict) -> None:  # from a thread: hop onto the loop for the broadcast
        loop = app.get("loop")
        if loop is not None and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(app["broadcast"]("voice.changed", data), loop)

    app["voice_download"] = vf.Downloader(paths.data, on_change=lambda st: changed({"download": st}))
    app["voice_speaker"] = Speaker(state, env=speaker_env)
    app["voice"] = Announcer(
        settings=lambda: load_settings(paths), brain=paths.brain, state_path=state / "voice.json",
        files=lambda s: vf.files(paths.data, s["voice_model"]),
        items=lambda: app["store"].list_items(status=["open", "snoozed"]), speaker=app["voice_speaker"])


async def voice_tick(app: web.Application) -> None:
    """The reminder tick (design §4.3): the first one at once, for the startup catch-up."""
    while True:
        await t(app["voice"].tick)
        await asyncio.sleep(TICK_S)


def close_voice(app: web.Application) -> None:
    if app.get("voice") is not None:
        app["voice"].close()
        app["voice_speaker"].close()


def _voice(request: web.Request) -> Announcer:
    voice = request.app.get("voice")
    if voice is None:
        raise web.HTTPServiceUnavailable(reason="the voice is off while the root is read-only")
    return voice


def _view(app: web.Application) -> dict:
    voice = app["voice"]
    s = load_settings(app["paths"])
    return {"available": True, **voice.view(), "download": dict(app["voice_download"].state),
            "voices": list(VOICE_IDS), "missing_bytes": vf.missing_bytes(app["paths"].data, s["voice_model"]),
            "sizes": {m: f.size + vf.VOICES.size for m, f in vf.MODELS.items()}}


async def get_voice(request: web.Request) -> web.Response:
    if request.app.get("voice") is None:
        return web.json_response({"available": False, "reason": "The voice is off while the root is read-only."})
    return web.json_response(await t(_view, request.app))


async def _changed(app: web.Application) -> None:
    await app["broadcast"]("voice.changed", {})


async def post_download(request: web.Request) -> web.Response:
    _voice(request)
    app = request.app
    body = await body_dict(request)
    model = body.get("model") or load_settings(app["paths"])["voice_model"]
    validate_setting("voice_model", model)
    started = await t(app["voice_download"].start, model)
    return web.json_response({"started": started, "download": dict(app["voice_download"].state)})


async def post_sample(request: web.Request) -> web.Response:
    voice = _voice(request)
    body = await body_dict(request)
    overrides = {}
    for key in SAMPLE_KEYS:
        if key in body:
            validate_setting(key, body[key])
            overrides[key] = body[key]
    unknown = set(body) - set(SAMPLE_KEYS)
    if unknown:
        raise ValueError(f"unknown field: {sorted(unknown)[0]}")
    if not await t(voice.sample, overrides):
        raise web.HTTPConflict(reason="download the voice first")
    return web.Response(status=204)


async def post_pause(request: web.Request) -> web.Response:
    voice = _voice(request)
    body = await body_dict(request)
    if body.get("resume") is True:
        await t(voice.resume)
    elif body.get("until") == "morning":
        await t(voice.pause, until_morning=True)
    else:
        minutes = body.get("minutes")
        if type(minutes) is not int or not 1 <= minutes <= 24 * 60:
            raise ValueError('send {"minutes": 1-1440}, {"until": "morning"} or {"resume": true}')
        await t(voice.pause, minutes)
    await _changed(request.app)
    until = voice.paused_until()
    return web.json_response({"paused_until": until.isoformat(timespec="minutes") if until else None})


async def post_stop(request: web.Request) -> web.Response:
    voice = _voice(request)
    await body_dict(request)  # JSON only, like every other POST (rule 5)
    await t(voice.stop)
    return web.Response(status=204)


def _open_file(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # the owner's default app for .md files
    else:
        subprocess.Popen(["xdg-open", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)


async def post_edit_quotes(request: web.Request) -> web.Response:
    _voice(request)
    await body_dict(request)
    path = quotes_path(request.app["paths"].brain)
    if not path.is_file():
        raise web.HTTPNotFound(reason="the quote file isn't there yet: it's made when ATLAS next starts")
    await t(request.app.get("file_opener", _open_file), path)
    return web.Response(status=204)


def add_voice_routes(app: web.Application) -> None:
    app.router.add_get("/api/voice", get_voice)
    app.router.add_post("/api/voice/download", post_download)
    app.router.add_post("/api/voice/sample", post_sample)
    app.router.add_post("/api/voice/pause", post_pause)
    app.router.add_post("/api/voice/stop", post_stop)
    app.router.add_post("/api/voice/edit-quotes", post_edit_quotes)
