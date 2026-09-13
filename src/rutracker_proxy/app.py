"""aiohttp application: wiring, lifecycle, /health and the passthrough route."""

from __future__ import annotations

import time

from aiohttp import web

from . import __version__
from .byparr import ByparrClient
from .cache import ResponseCache
from .clearance import ClearanceManager
from .config import Settings
from .passthrough import Rewriter, make_handler
from .upstream import UpstreamClient

SETTINGS = web.AppKey("settings", Settings)
CLEARANCE = web.AppKey("clearance", ClearanceManager)
UPSTREAM = web.AppKey("upstream", UpstreamClient)
STARTED_AT = web.AppKey("started_at", float)
CACHE = web.AppKey("cache", ResponseCache)


def build_components(settings: Settings) -> tuple[ByparrClient, ClearanceManager, UpstreamClient]:
    byparr = ByparrClient(settings.byparr_url, settings.byparr_timeout)
    clearance = ClearanceManager(
        byparr,
        settings.clearance_url,
        max_age=settings.clearance_max_age,
        state_file=settings.clearance_file,
    )
    upstream = UpstreamClient(
        clearance,
        base_url=settings.upstream_url,
        impersonate=settings.impersonate,
        ip_family=settings.ip_family,
        timeout=settings.upstream_timeout,
        origin_retries=settings.origin_retries,
        concurrency=settings.upstream_concurrency,
    )
    return byparr, clearance, upstream


async def health(request: web.Request) -> web.Response:
    clearance: ClearanceManager = request.app[CLEARANCE]
    current = clearance.current
    stats = clearance.stats
    cache = request.app[CACHE]
    body = {
        "status": "ok" if current is not None else "degraded",
        "version": __version__,
        "uptime": round(time.time() - request.app[STARTED_AT]),
        "clearance": {
            "present": current is not None,
            "age": round(current.age) if current else None,
            "user_agent": current.user_agent if current else None,
            "refreshes": stats.refreshes,
            "failures": stats.failures,
            "last_error": stats.last_error,
        },
        "cache": {
            "enabled": cache.enabled,
            "entries": len(cache),
            "hits": cache.stats.hits,
            "misses": cache.stats.misses,
            "shared": cache.stats.shared,
        },
    }
    # Always 200: a missing clearance is recoverable and must not make Docker restart us.
    return web.json_response(body)


def create_app(settings: Settings) -> web.Application:
    app = web.Application()
    byparr, clearance, upstream = build_components(settings)
    app[SETTINGS] = settings
    app[CLEARANCE] = clearance
    app[UPSTREAM] = upstream
    app[STARTED_AT] = time.time()
    app[CACHE] = cache = ResponseCache(settings.cache_ttl, settings.cache_max_entries)

    async def lifecycle(app: web.Application):
        clearance.load()
        clearance.start_background()
        yield
        await clearance.stop_background()
        await upstream.close()
        await byparr.close()

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/health", health)
    app.router.add_route("*", "/{tail:.*}", make_handler(upstream, Rewriter(settings.upstream_url), cache))
    return app
