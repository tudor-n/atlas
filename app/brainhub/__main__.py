import argparse
import asyncio
import json
import os
import socket
import sqlite3
import sys
from pathlib import Path

from aiohttp import web

from . import rootguard
from .config import Paths, load_settings
from .server import DEFAULT_HOST, EXIT_RESTART, RootUnavailable, create_app, create_reduced_app


def run_server(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="brainhub")
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args(argv)
    paths = Paths(args.root)
    port = args.port or load_settings(paths)["port"]
    # A second hub on the same root must not run startup work (log scan, git sync, filing
    # recovery) before failing to bind: check the port first.
    if not port_free(DEFAULT_HOST, port):
        sys.exit(f"BrainHub: port {port} on {DEFAULT_HOST} is already in use (is another hub running?). "
                 "Not starting.")
    # One hub per root, whatever its port: the lock is checked before any startup work too.
    # A read-only root is only checked (nothing can be written there); missing, foreign and
    # newer roots are never written, so they take no lock.
    status = rootguard.check_root(paths)
    locked = status.state in ("ok", "fresh")
    if status.state in ("ok", "fresh", "readonly"):
        refusal = rootguard.acquire_lock(paths, write=locked)
        if refusal:
            sys.exit(f"BrainHub: not starting. {refusal}")
    code = 0
    try:
        app = _make_app(paths, status)
        app["port"] = port  # the port it really listens on, not the settings value (--port may differ)
        print(f"BrainHub on http://{DEFAULT_HOST}:{port}", flush=True)
        found = app["root_status"]
        if found.state not in ("ok", "fresh"):
            mode = "read-only" if "store" in app else "reduced"
            print(f"BrainHub: {mode} mode, captures are held on this OS. {found.message}", flush=True)
        code = serve(app, DEFAULT_HOST, port)
    finally:
        if locked:
            rootguard.release_lock(paths)
    if code == EXIT_RESTART:
        print("BrainHub: the data folder is usable again, so it stopped to start again in full "
              "(the desktop shell restarts it at once; if you started it by hand, start it again).",
              flush=True)
    if code:
        sys.exit(code)


def _make_app(paths: Paths, status: rootguard.RootStatus) -> web.Application:
    """The full app, or the reduced one when the full app can't run on this root (design §6.5)."""
    try:
        return create_app(paths, status=status)
    except RootUnavailable as e:
        return create_reduced_app(paths, e.status)
    except sqlite3.Error as e:  # locked, or can't be opened: not damage, so nothing was moved
        sys.exit(f"BrainHub: not starting. Its index {paths.db} can't be opened ({e}); nothing was "
                 "moved or changed.")


def serve(app: web.Application, host: str, port: int) -> int:
    """Serve until SIGINT or SIGTERM (POSIX), Ctrl+C, or until the app asks to stop (app["stop"],
    set by the root watch, P8); return app["exit_code"]. web.run_app can't be stopped from inside
    the app, so this is its loop with a stop event."""
    async def main() -> None:
        runner = web.AppRunner(app, handle_signals=True)  # SIGINT/SIGTERM: a graceful stop (POSIX)
        await runner.setup()
        try:
            await web.TCPSite(runner, host, port).start()
            await app["stop"].wait()
        finally:
            await runner.cleanup()

    try:
        asyncio.run(main())
    except (web.GracefulExit, KeyboardInterrupt):
        pass
    return app["exit_code"]


def port_free(host: str, port: int) -> bool:
    """Can we bind host:port right now? (Bound, then closed again at once.)

    On POSIX it binds as aiohttp does, with SO_REUSEADDR: connections the previous hub closed sit in
    TIME_WAIT for a minute, and without it a hub restarted at once (exit 75, P8) would be refused.
    A port someone listens on is still refused. Windows' SO_REUSEADDR would let us bind over a
    listener, so it isn't set there (asyncio doesn't set it there either)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "posix":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def run_integration(argv: list[str]) -> None:
    from . import claude_integration as ci
    from . import osdirs
    ap = argparse.ArgumentParser(prog="brainhub integration")
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--settings", type=Path, default=None)
    args = ap.parse_args(argv)
    paths = Paths(args.root)
    target = args.settings or ci.default_settings_path()
    port = load_settings(paths)["port"]
    state = osdirs.state_dir()
    try:
        if args.action == "install":
            ci.install(target, python=str(ci.console_python(Path(sys.executable))), script=str(ci.hook_script()),
                       port=port, state_dir=state, data_dir=paths.data)
        elif args.action == "uninstall":
            ci.uninstall(target, state_dir=state, data_dir=paths.data)
        print(json.dumps(ci.status(target, port=port), indent=2))
    except ci.IntegrationError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


def run_desktop_config(argv: list[str]) -> None:
    from . import claude_integration as ci
    from . import desktop_config as dc
    ap = argparse.ArgumentParser(prog="brainhub desktop-config")
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--target", type=Path, default=None)
    args = ap.parse_args(argv)
    paths = Paths(args.root.resolve())
    target = args.target or dc.default_target()
    written = dc.write_desktop_config(root=paths.root, port=load_settings(paths)["port"],
                                      python=ci.console_python(Path(sys.executable)), app_dir=paths.app,
                                      target=target)
    print(written)


COMMANDS = {"integration": run_integration, "desktop-config": run_desktop_config}


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in COMMANDS:
        COMMANDS[argv[0]](argv[1:])
    else:
        run_server(argv)


if __name__ == "__main__":
    main()
