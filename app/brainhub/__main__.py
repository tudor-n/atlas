import argparse
from pathlib import Path

from aiohttp import web

from .config import Paths, load_settings
from .server import DEFAULT_HOST, create_app


def main() -> None:
    ap = argparse.ArgumentParser(prog="brainhub")
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    paths = Paths(args.root)
    port = args.port or load_settings(paths)["port"]
    print(f"BrainHub on http://{DEFAULT_HOST}:{port}", flush=True)
    web.run_app(create_app(paths), host=DEFAULT_HOST, port=port, print=None)


if __name__ == "__main__":
    main()
