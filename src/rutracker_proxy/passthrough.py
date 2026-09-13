"""The proxy route: Prowlarr -> this handler -> UpstreamClient -> RuTracker -> back.

To Prowlarr the proxy must look like a plain HTTP site, so responses are adjusted:
- Location pointing at rutracker becomes a path on the proxy ("/forum/index.php").
- Set-Cookie loses Domain (.NET rejects a .rutracker.org cookie for 127.0.0.1) and
  Secure (.NET never sends Secure cookies over plain http). Cloudflare cookies are dropped.
- Absolute links to rutracker inside HTML are made relative (the page itself mostly uses
  relative links already).
Bodies are passed as raw bytes: windows-1251 and .torrent files stay intact.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from urllib.parse import urlsplit

from aiohttp import web
from multidict import CIMultiDict

from .cache import ResponseCache
from .upstream import ChallengeNotPassed, UpstreamClient, UpstreamError, UpstreamResponse, parse_cookie_header

log = logging.getLogger("rutracker_proxy.access")

ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})

# Upstream headers that make no sense coming from a local http proxy, or that aiohttp sets itself.
_DROP_RESPONSE_HEADERS = frozenset({
    "server", "date", "alt-svc", "strict-transport-security", "nel", "report-to",
})
_DROP_COOKIE_ATTRIBUTES = frozenset({"domain", "secure", "samesite", "partitioned"})
_SENSITIVE_QUERY = re.compile(r"((?:pass|password|passkey|pk|sid)[^=&]*=)[^&]*", re.I)


class Rewriter:
    """Pure URL/header/body rewriting between the upstream origin and the proxy."""

    def __init__(self, upstream_url: str):
        parts = urlsplit(upstream_url)
        host = re.escape(parts.hostname or "")
        # https://rutracker.org, http://rutracker.org, //rutracker.org, optional www. and :443
        self._origin_re = re.compile(rf"^(?:https?:)?//(?:www\.)?{host}(?::\d+)?(?=/|$)", re.I)
        self._body_re = re.compile(rf"(?:https?:)?//(?:www\.)?{host}(?::\d+)?/".encode(), re.I)
        self._upstream_origin = f"{parts.scheme}://{parts.netloc}"

    def location(self, value: str) -> str:
        """'https://rutracker.org/forum/index.php' -> '/forum/index.php'; other hosts untouched."""
        rewritten, count = self._origin_re.subn("", value, count=1)
        if count == 0:
            return value
        return rewritten or "/"

    def to_upstream(self, value: str) -> str:
        """Referer/Origin from Prowlarr ('http://127.0.0.1:30240/forum/login.php') -> rutracker URL."""
        parts = urlsplit(value)
        if not parts.scheme or not parts.netloc:
            return value
        rest = parts.path + (f"?{parts.query}" if parts.query else "")
        return self._upstream_origin + rest

    @staticmethod
    def set_cookie(value: str) -> str | None:
        """Make an upstream Set-Cookie usable on the proxy host. None = drop the cookie."""
        name_value, *attributes = [p.strip() for p in value.split(";")]
        name = name_value.split("=", 1)[0].strip()
        if name.startswith(("cf_", "__cf")):
            return None
        kept = [a for a in attributes if a and a.split("=", 1)[0].strip().lower() not in _DROP_COOKIE_ATTRIBUTES]
        return "; ".join([name_value, *kept])

    def body(self, content_type: str | None, body: bytes) -> bytes:
        if not content_type or "html" not in content_type.lower():
            return body
        # ASCII-only substitution, safe for windows-1251 bytes.
        return self._body_re.sub(b"/", body)


def build_request_headers(request: web.Request, rewriter: Rewriter) -> dict[str, str]:
    headers = dict(request.headers)
    for name in ("Referer", "Origin"):
        if name in request.headers:
            headers[name] = rewriter.to_upstream(request.headers[name])
    return headers


def build_response_headers(upstream_headers: list[tuple[str, str]], rewriter: Rewriter) -> CIMultiDict[str]:
    headers: CIMultiDict[str] = CIMultiDict()
    for name, value in upstream_headers:
        lower = name.lower()
        if lower in _DROP_RESPONSE_HEADERS:
            continue
        if lower == "location":
            value = rewriter.location(value)
        elif lower == "set-cookie":
            rewritten = rewriter.set_cookie(value)
            if rewritten is None:
                continue
            value = rewritten
        headers.add(name, value)
    return headers


_CATEGORY_PARAM = re.compile(r"(?:f(?:\[\]|%5B%5D)=\d+&?)+", re.I)


def safe_path(raw_path: str) -> str:
    """Path for logs: password-like query values masked, long category lists collapsed."""
    def collapse(m: re.Match) -> str:
        count = m.group(0).count("=")
        tail = "&" if m.group(0).endswith("&") else ""
        return m.group(0) if count <= 3 else f"f[]=<{count} categories>{tail}"

    return _CATEGORY_PARAM.sub(collapse, _SENSITIVE_QUERY.sub(r"\1***", raw_path))


_CACHEABLE_PATHS = ("/forum/tracker.php",)


def cache_key(method: str, raw_path: str, cookie_header: str | None) -> str | None:
    """Key for cacheable requests, None for everything else.

    Only search pages are cached. Login, the login test page (index.php) and .torrent
    downloads always go to the site. The RuTracker session is part of the key, so one
    session never gets a page rendered for another.
    """
    if method != "GET" or not raw_path.startswith(_CACHEABLE_PATHS):
        return None
    session = parse_cookie_header(cookie_header).get("bb_session", "")
    session_id = hashlib.sha256(session.encode()).hexdigest()[:16] if session else "anon"
    return f"{session_id} {raw_path}"


def worth_caching(resp: UpstreamResponse) -> bool:
    # 200 with at least one release. Guests get 302 to login; empty results are not kept,
    # so a retry really asks the site again.
    return resp.status == 200 and b"dl.php?t=" in resp.body


def make_handler(upstream: UpstreamClient, rewriter: Rewriter, cache: ResponseCache[UpstreamResponse] | None = None):
    async def passthrough(request: web.Request) -> web.StreamResponse:
        started = time.monotonic()
        method = request.method
        path = request.raw_path  # exactly as sent: keeps %XX of windows-1251 queries untouched
        if method not in ALLOWED_METHODS:
            return _finish(request, started, web.Response(status=405, headers={"Allow": "GET, HEAD, POST"}))

        body = await request.read() if method == "POST" else None
        upstream_headers = build_request_headers(request, rewriter)
        key = cache_key(method, path, request.headers.get("Cookie")) if cache is not None and cache.enabled else None
        source = None
        try:
            if key is None:
                up = await upstream.fetch(method, path, headers=upstream_headers, body=body)
            else:
                up, source = await cache.get_or_fetch(
                    key, lambda: upstream.fetch(method, path, headers=upstream_headers), worth_caching
                )
        except ChallengeNotPassed as e:
            log.error("%s %s: %s", method, safe_path(path), e)
            return _finish(request, started, _error(503, f"Cloudflare challenge not passed: {e}"))
        except UpstreamError as e:
            log.error("%s %s: %s", method, safe_path(path), e)
            status = 504 if "timed out" in str(e).lower() else 502
            return _finish(request, started, _error(status, str(e)))

        headers = build_response_headers(up.headers, rewriter)
        if source is not None:
            headers["X-Cache"] = source.upper()
        content = rewriter.body(headers.get("Content-Type"), up.body)
        response = web.Response(status=up.status, headers=headers, body=content if method != "HEAD" else None)
        return _finish(request, started, response, refreshed=up.refreshed and source in (None, "miss"), source=source)

    return passthrough


def _error(status: int, message: str) -> web.Response:
    # Retry-After lets well-behaved clients back off while byparr/rutracker recover.
    headers = {"Retry-After": "60"} if status in (502, 503, 504) else {}
    return web.Response(status=status, text=f"rutracker-proxy: {message}\n", headers=headers)


def _finish(
    request: web.Request, started: float, response: web.Response, *, refreshed: bool = False, source: str | None = None
) -> web.Response:
    size = len(response.body) if isinstance(response.body, (bytes, bytearray)) else 0
    tags = "".join(f" [{t}]" for t in (f"cache {source}" if source else None, "clearance refreshed" if refreshed else None) if t)
    log.info(
        "%s %s -> %s %dB %.2fs%s",
        request.method, safe_path(request.raw_path), response.status, size, time.monotonic() - started, tags,
    )
    return response
