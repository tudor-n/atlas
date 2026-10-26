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
from .brainmap import load_map
from .chatter import Chatter, git_commit, git_count
from .chatter_sub import Sub
from .chattiness import level
from .config import VOICE_IDS, load_settings, save_settings, validate_setting
from .presence import Presence
from .lines import lines_path
from .quotes import quotes_path
from .routes import body_dict
from .voice import TICK_S, Announcer, Speaker

log = logging.getLogger(__name__)
t = asyncio.to_thread
SAMPLE_KEYS = ("voice_blend", "voice_speed", "voice_volume", "voice_boost", "voice_duck_to", "voice_model")


def setup_voice(app: web.Application, *, speaker_env: dict | None = None) -> None:
    """app["voice"] (the Announcer) and app["voice_download"]; app["voice"] is None when read-only."""
    paths = app["paths"]
    if app["root_status"].state == "readonly":
        app["voice"], app["voice_download"], app["chatter"], app["presence"] = None, None, None, None
        return
    state = osdirs.state_dir()

    def send(type_: str, data: dict) -> None:  # from a thread: hop onto the loop for the broadcast
        loop = app.get("loop")
        if loop is not None and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(app["broadcast"](type_, data), loop)

    app["voice_download"] = vf.Downloader(paths.data, on_change=lambda st: send("voice.changed", {"download": st}))
    app["presence"] = Presence(away_after=lambda: level(load_settings(paths)).away_after)
    # voice.speaking / voice.quiet: the ATLAS screen's dot follows the line as it sounds (VI8)
    app["voice_speaker"] = Speaker(state, env=speaker_env, on_event=send)
    app["voice"] = Announcer(
        settings=lambda: load_settings(paths), brain=paths.brain, state_path=state / "voice.json",
        files=lambda s: vf.files(paths.data, s["voice_model"]),
        items=lambda: app["store"].list_items(status=["open", "snoozed"]), speaker=app["voice_speaker"],
        # The store's rows, not the tracker's: the tracker calls the voice under its own lock.
        agents=lambda: app["store"].list_agents(), presence=app["presence"],
        on_line=lambda entry: send("voice.line", entry))  # the caption of every line, spoken or not
    store, code = app["store"], paths.app.parent
    app["chatter"] = Chatter(
        voice=app["voice"], settings=lambda: load_settings(paths), brain=paths.brain,
        items=lambda: store.list_items(status=["open", "snoozed"]), agents=store.list_agents,
        entries_today=lambda day: len(store.list_entries(day=day)), unconfirmed=lambda: len(store.list_proposals()),
        usage=lambda: app["usage"].view(load_settings(paths)["usage_warn_pct"], ""),
        presence=app["presence"], save_settings=lambda patch: _save_and_tell(app, patch),
        roots=(paths.root, code), load_map=lambda: load_map(paths.brain),
        commit=lambda: git_commit(code), count_commits=lambda a, b: git_count(code, a, b))
    app["chatter"].sub = Sub(
        runner=app["runner"], settings=lambda: load_settings(paths), paths=paths,
        usage=lambda: app["usage"].view(load_settings(paths)["usage_warn_pct"], ""), presence=app["presence"],
        chatter=app["chatter"], voice=app["voice"], commit=lambda message: _commit(app, message),
        reviewer=app["reviewer"], store=store)


def _commit(app: web.Application, message: str) -> None:
    """Pattern A's generated.md, committed like the hub's other brain writes."""
    sha = app["repo"].commit_all(message)
    app["on_commit"](sha)


def _save_and_tell(app: web.Application, patch: dict) -> dict:
    """Chatter changing a setting by itself (Very high dropping back): saved, and the pages told."""
    saved = save_settings(app["paths"], patch)
    loop = app.get("loop")
    if loop is not None and not loop.is_closed():
        asyncio.run_coroutine_threadsafe(app["broadcast"]("voice.changed", {}), loop)
    return saved


def agent_transition(app: web.Application):
    """The Agents tracker's listener: the voice's lines, then chatter's (both swallow their errors)."""
    def on(t: dict) -> None:
        app["voice"].agent_transition(t)
        app["chatter"].agent_transition(t)
    return on


async def voice_tick(app: web.Application) -> None:
    """The reminder tick (design §4.3): the first one at once, for the startup catch-up."""
    while True:
        await t(app["voice"].tick)
        await t(app["chatter"].tick)
        await app["chatter"].sub.tick()  # on the loop: its runs are async, never on a line's path
        await asyncio.sleep(TICK_S)


def close_voice(app: web.Application) -> None:
    if app.get("voice") is not None:
        app["chatter"].close()
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
    if body.get("resume") is not True:
        await t(request.app["chatter"].react, "pause")  # captioned: a pause can't speak
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


async def post_edit_lines(request: web.Request) -> web.Response:
    _voice(request)
    await body_dict(request)
    path = lines_path(request.app["paths"].brain)
    if not path.is_file():
        raise web.HTTPNotFound(reason="the info lines file isn't there yet: it's made when ATLAS next starts")
    await t(request.app.get("file_opener", _open_file), path)
    return web.Response(status=204)


async def post_presence(request: web.Request) -> web.Response:
    """The app's and the widget's input ping (throttled there): the owner is at the PC."""
    await body_dict(request)
    if request.app.get("presence") is not None:
        request.app["presence"].seen("app")
    return web.Response(status=204)


REACT_KINDS = ("flip_ask", "flip_note")


async def post_react(request: web.Request) -> web.Response:
    """What the owner did on a page that the hub can't see by itself: overruling NOTE | ASK."""
    _voice(request)
    kind = (await body_dict(request)).get("kind")
    if kind not in REACT_KINDS:
        raise ValueError("kind must be " + " or ".join(REACT_KINDS))
    said = await t(request.app["chatter"].react, kind)
    return web.json_response({"said": said})


async def get_chatter(request: web.Request) -> web.Response:
    """Settings → Voice's chatter panel: the level, presence, ATLAS's project, the last lines."""
    if request.app.get("voice") is None:
        return web.json_response({"available": False})
    app = request.app
    chatter, voice = app["chatter"], app["voice"]
    s = await t(load_settings, app["paths"])
    lvl = level(s)
    sub = chatter.sub.view() if chatter.sub is not None else None
    return web.json_response({
        "available": True, "level": lvl.name, "per_hour": lvl.per_hour,
        "presence": app["presence"].view(), "atlas_project": await t(chatter.atlas_project),
        "recent": list(reversed(voice.recent)), "debug": (voice.chatter_state().get("debug") or None),
        "restrained": [e for e in ("briefing", "self", "nudge", "tease", "musing") if voice.restrained(e)],
        "sub": sub})


async def post_try(request: web.Request) -> web.Response:
    """Very high's Try it now: one kind of line at once (or one of the subscription's patterns)."""
    _voice(request)
    app = request.app
    what = (await body_dict(request)).get("what")
    if load_settings(app["paths"])["voice_chattiness"] != "debug":
        raise web.HTTPConflict(reason="Try it now is for the Very high level")
    if isinstance(what, str) and what.startswith("sub_"):
        if app["chatter"].sub is None:
            raise web.HTTPConflict(reason="the subscription's patterns aren't available")
        return web.json_response({"started": await t(app["chatter"].sub.run_now, what[4:])})
    if what not in Chatter.TRY:
        raise ValueError("what must be one of " + ", ".join(Chatter.TRY))
    return web.json_response({"said": await t(app["chatter"].try_now, what)})


QUIET_PATHS = ("/api/hooks/", "/api/usage", "/api/tablet/", "/api/presence")


async def _owner_was_here(request: web.Request, response: web.StreamResponse) -> None:
    """A write from the PC app or the widget is the owner at the PC (agents' hooks and the status
    line aren't, and nor is the tablet at the bench)."""
    presence = request.app.get("presence")
    if presence is None or request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if not request.path.startswith("/api/") or request.path.startswith(QUIET_PATHS):
        return
    presence.seen("app")


async def get_week(request: web.Request) -> web.Response:
    """ATLAS's week (pattern C), for the ATLAS screen; null until the first one is written."""
    chatter = request.app.get("chatter")
    return web.json_response({"week": await t(chatter.sub.week) if chatter is not None else None})


async def post_week_decision(request: web.Request) -> web.Response:
    _voice(request)
    await body_dict(request)
    sub = request.app["chatter"].sub
    week = await t(sub.decide, request.match_info["id"], request.match_info["decision"] == "confirm")
    await request.app["broadcast"]("items.changed", {})
    return web.json_response({"week": week})


def add_voice_routes(app: web.Application) -> None:
    app.on_response_prepare.append(_owner_was_here)
    app.router.add_get("/api/chatter/week", get_week)
    app.router.add_post("/api/chatter/week/{id}/{decision:confirm|dismiss}", post_week_decision)
    app.router.add_get("/api/voice", get_voice)
    app.router.add_post("/api/voice/download", post_download)
    app.router.add_post("/api/voice/sample", post_sample)
    app.router.add_post("/api/voice/pause", post_pause)
    app.router.add_post("/api/voice/stop", post_stop)
    app.router.add_post("/api/voice/edit-quotes", post_edit_quotes)
    app.router.add_post("/api/voice/edit-lines", post_edit_lines)
    app.router.add_post("/api/presence", post_presence)
    app.router.add_post("/api/chatter/react", post_react)
    app.router.add_get("/api/chatter", get_chatter)
    app.router.add_post("/api/chatter/try", post_try)
