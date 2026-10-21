import argparse
import json
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
    print(f"BrainHub on http://{DEFAULT_HOST}:{port}", flush=True)
    web.run_app(create_app(paths), host=DEFAULT_HOST, port=port, print=None)


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


COMMANDS = {"integration": run_integration}  # later: "desktop-config"


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in COMMANDS:
        COMMANDS[argv[0]](argv[1:])
    else:
        run_server(argv)


if __name__ == "__main__":
    main()
