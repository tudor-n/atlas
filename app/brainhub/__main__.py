import argparse
import json
import socket
import sys
from pathlib import Path

from aiohttp import web

from .config import Paths, load_settings
from .server import DEFAULT_HOST, create_app


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
    app = create_app(paths)
    app["port"] = port  # the port it really listens on, not the settings value (--port may differ)
    print(f"BrainHub on http://{DEFAULT_HOST}:{port}", flush=True)
    web.run_app(app, host=DEFAULT_HOST, port=port, print=None)


def port_free(host: str, port: int) -> bool:
    """Can we bind host:port right now? (Bound, then closed again at once.)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def run_integration(argv: list[str]) -> None:
    from . import claude_integration as ci
    ap = argparse.ArgumentParser(prog="brainhub integration")
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--settings", type=Path, default=None)
    args = ap.parse_args(argv)
    paths = Paths(args.root)
    target = args.settings or ci.default_settings_path()
    port = load_settings(paths)["port"]
    try:
        if args.action == "install":
            ci.install(target, python=str(ci.console_python(Path(sys.executable))), script=str(ci.hook_script()),
                       port=port, data_dir=paths.data)
        elif args.action == "uninstall":
            ci.uninstall(target, data_dir=paths.data)
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
