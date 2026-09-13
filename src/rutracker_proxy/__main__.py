"""Entry point.

    python -m rutracker_proxy                       run the proxy
    python -m rutracker_proxy check                 one-off diagnostics: get clearance, fetch a challenged page
    python -m rutracker_proxy install-definition    put the Prowlarr definition into Definitions/Custom and exit
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from aiohttp import web

from . import definition
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


def install_definition() -> int:
    """Settings come from the environment:
    DEFINITION_DIR (default /definitions), PUBLIC_URL (default http://127.0.0.1:30240/),
    PROWLARR_URL and PROWLARR_API_KEY (optional, to reload Prowlarr without a restart).
    """
    try:
        definition.run(
            target_dir=Path(os.environ.get("DEFINITION_DIR") or "/definitions"),
            public_url=os.environ.get("PUBLIC_URL") or definition.DEFAULT_LINK,
            prowlarr_url=os.environ.get("PROWLARR_URL") or None,
            api_key=os.environ.get("PROWLARR_API_KEY") or None,
        )
    except (OSError, ValueError) as e:
        logging.getLogger("rutracker_proxy.definition").error("definition not installed: %s", e)
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="rutracker-proxy")
    sub = parser.add_subparsers(dest="command")
    check_cmd = sub.add_parser("check", help="diagnostics: pass Cloudflare once and exit")
    check_cmd.add_argument("path", nargs="?", default="/forum/tracker.php?nm=test")
    sub.add_parser("install-definition", help="install the Prowlarr definition into DEFINITION_DIR and exit")
    args = parser.parse_args()

    if args.command == "install-definition":
        setup_logging(os.environ.get("LOG_LEVEL", "INFO").upper())
        sys.exit(install_definition())

    settings = Settings.from_env()
    setup_logging(settings.log_level)

    if args.command == "check":
        sys.exit(asyncio.run(check(settings, args.path)))
    web.run_app(create_app(settings), host=settings.host, port=settings.port, print=None)


if __name__ == "__main__":
    main()
