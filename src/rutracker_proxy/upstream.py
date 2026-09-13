"""Requests to RuTracker that look like the browser byparr used.

Cloudflare accepts a byparr cf_clearance only when the request comes with
- the same User-Agent,
- a browser TLS/HTTP2 fingerprint (curl_cffi impersonation),
- from the same IP (we pin IPv4, which byparr uses).
Missing any of the three makes Cloudflare issue a new challenge (verified against the live site).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from curl_cffi import requests as curl_requests
from curl_cffi.const import CurlOpt

from .byparr import ByparrError, Clearance
from .challenge import is_challenge
from .clearance import ClearanceManager

log = logging.getLogger(__name__)

_IPRESOLVE = {"any": 0, "4": 1, "6": 2}  # CURL_IPRESOLVE_WHATEVER / V4 / V6
_CLOUDFLARE_ORIGIN_ERRORS = frozenset({520, 521, 522, 523, 524})

# Only these client headers are forwarded. Everything else (Accept, Accept-Language, ...)
# comes from the impersonated browser profile, so the header set stays browser-like.
_FORWARD_REQUEST_HEADERS = frozenset({
    "content-type", "referer", "origin", "range", "if-modified-since", "if-none-match",
})
# Never forwarded back: curl_cffi already decompressed and de-chunked the body.
_DROP_RESPONSE_HEADERS = frozenset({
    "connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length",
})


class UpstreamError(RuntimeError):
    """RuTracker could not be reached (network error, timeout, or challenge we cannot pass)."""


class ChallengeNotPassed(UpstreamError):
    """Still challenged right after getting a fresh clearance."""


@dataclass
class UpstreamResponse:
    status: int
    headers: list[tuple[str, str]]  # multi-valued (several Set-Cookie), already filtered
    body: bytes
    elapsed: float
    refreshed: bool  # True if a clearance refresh was needed for this request

    def header(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers if k.lower() == name), None)


def parse_cookie_header(value: str | None) -> dict[str, str]:
    """'a=1; b=2' -> {'a': '1', 'b': '2'}"""
    cookies: dict[str, str] = {}
    for part in (value or "").split(";"):
        name, sep, val = part.strip().partition("=")
        if sep and name:
            cookies[name] = val
    return cookies


class UpstreamClient:
    def __init__(
        self,
        clearance: ClearanceManager,
        *,
        base_url: str,
        impersonate: str = "firefox",
        ip_family: str = "4",
        timeout: int = 90,
        origin_retries: int = 1,
        retry_delay: float = 2.0,
        concurrency: int = 2,
    ):
        # Be gentle with rutracker: at most `concurrency` requests at the same time.
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._clearance = clearance
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._origin_retries = origin_retries
        self._retry_delay = retry_delay
        # discard_cookies: the session must not remember anything between requests,
        # otherwise one Prowlarr session's cookies could leak into another request.
        self._session = curl_requests.AsyncSession(
            impersonate=impersonate,
            discard_cookies=True,
            allow_redirects=False,
            curl_options={CurlOpt.IPRESOLVE: _IPRESOLVE[ip_family]},
        )

    async def close(self) -> None:
        await self._session.close()

    async def fetch(
        self,
        method: str,
        path_and_query: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> UpstreamResponse:
        """Send one request to RuTracker; on a challenge refresh clearance and retry once.

        `path_and_query` is e.g. "/forum/tracker.php?nm=test".
        `headers` are the client's headers; its Cookie header (RuTracker session) is kept,
        any cf_* cookies in it are replaced with ours.
        """
        if not path_and_query.startswith("/"):
            path_and_query = "/" + path_and_query
        url = self._base_url + path_and_query
        headers = headers or {}
        client_cookies = {
            k: v for k, v in parse_cookie_header(_get_ci(headers, "cookie")).items() if not k.startswith(("cf_", "__cf"))
        }
        forward_headers = {k: v for k, v in headers.items() if k.lower() in _FORWARD_REQUEST_HEADERS}

        try:
            clearance = await self._clearance.get()
        except ByparrError as e:
            raise UpstreamError(f"no clearance available: {e}") from e

        started = time.monotonic()
        refreshed = False
        # Only idempotent requests are repeated: a login POST must never be sent twice.
        attempts_left = self._origin_retries if method in ("GET", "HEAD") else 0
        while True:
            resp = await self._send(method, url, forward_headers, client_cookies, body, clearance)
            if is_challenge(resp.status_code, resp.headers, resp.content):
                if refreshed:
                    raise ChallengeNotPassed(
                        f"still challenged on {method} {path_and_query} with a fresh clearance "
                        "(byparr UA/IP family changed? check IMPERSONATE and IP_FAMILY)"
                    )
                log.info("challenge on %s %s (clearance age %.0fs), refreshing", method, path_and_query, clearance.age)
                try:
                    clearance = await self._clearance.refresh(stale=clearance)
                except ByparrError as e:
                    raise UpstreamError(f"challenge and clearance refresh failed: {e}") from e
                refreshed = True
                continue
            # 520-524: Cloudflare could not get an answer from rutracker's own servers (happens regularly).
            if resp.status_code in _CLOUDFLARE_ORIGIN_ERRORS and attempts_left > 0:
                attempts_left -= 1
                log.warning("rutracker origin error %s on %s %s, retrying", resp.status_code, method, path_and_query)
                await asyncio.sleep(self._retry_delay)
                continue
            break

        elapsed = time.monotonic() - started
        log.debug("%s %s -> %s in %.2fs", method, path_and_query, resp.status_code, elapsed)
        return UpstreamResponse(
            status=resp.status_code,
            headers=[(k, v) for k, v in resp.headers.multi_items() if v is not None and k.lower() not in _DROP_RESPONSE_HEADERS],
            body=resp.content,
            elapsed=elapsed,
            refreshed=refreshed,
        )

    async def _send(self, method, url, headers, client_cookies, body, clearance: Clearance):
        try:
            async with self._semaphore:
                return await self._session.request(
                    method,
                    url,
                    headers={**headers, "User-Agent": clearance.user_agent},
                    cookies={**client_cookies, **clearance.cookies},
                    data=body if body else None,
                    timeout=self._timeout,
                )
        except curl_requests.RequestsError as e:
            raise UpstreamError(f"{method} {url} failed: {e}") from e


def _get_ci(headers: dict[str, str], name: str) -> str | None:
    name = name.lower()
    return next((v for k, v in headers.items() if k.lower() == name), None)
