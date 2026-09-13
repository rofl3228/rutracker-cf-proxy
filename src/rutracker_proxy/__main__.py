"""Entry point.

    python -m rutracker_proxy          run the proxy
    python -m rutracker_proxy check    one-off diagnostics: get clearance, fetch a challenged page
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from aiohttp import web

from .app import build_components, create_app
from .config import Settings
from .upstream import UpstreamError


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # aiohttp's access log is noisy at INFO; our own request log comes in stage 3.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


async def check(settings: Settings, path: str) -> int:
    """Fetch `path` once through the full clearance machinery and report what happened."""
    byparr, clearance, upstream = build_components(settings)
    clearance.load()
    try:
        resp = await upstream.fetch("GET", path)
    except UpstreamError as e:
        print(f"FAIL: {e}")
        return 1
    finally:
        await upstream.close()
        await byparr.close()
    print(
        f"OK: GET {path} -> {resp.status} in {resp.elapsed:.2f}s, "
        f"refreshed={resp.refreshed}, content-type={resp.header('content-type')}, "
        f"location={resp.header('location')}, bytes={len(resp.body)}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="rutracker-proxy")
    sub = parser.add_subparsers(dest="command")
    check_cmd = sub.add_parser("check", help="diagnostics: pass Cloudflare once and exit")
    check_cmd.add_argument("path", nargs="?", default="/forum/tracker.php?nm=test")
    args = parser.parse_args()

    settings = Settings.from_env()
    setup_logging(settings.log_level)

    if args.command == "check":
        sys.exit(asyncio.run(check(settings, args.path)))
    web.run_app(create_app(settings), host=settings.host, port=settings.port, print=None)


if __name__ == "__main__":
    main()
