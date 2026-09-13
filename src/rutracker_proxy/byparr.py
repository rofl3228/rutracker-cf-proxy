"""Client for byparr's FlareSolverr-compatible /v1 API.

byparr is only used to get a cf_clearance cookie and the User-Agent it was issued for.
It cannot POST, accept cookies or keep sessions, so all real traffic goes through upstream.py.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import aiohttp

log = logging.getLogger(__name__)


class ByparrError(RuntimeError):
    """byparr is unreachable or failed to solve the challenge."""


@dataclass(frozen=True)
class Clearance:
    """What Cloudflare needs to let us through: cookies + the exact UA they were issued for."""

    user_agent: str
    cookies: dict[str, str]
    obtained_at: float = field(default_factory=time.time)

    @property
    def age(self) -> float:
        return time.time() - self.obtained_at

    def to_json(self) -> dict:
        return {"user_agent": self.user_agent, "cookies": self.cookies, "obtained_at": self.obtained_at}

    @classmethod
    def from_json(cls, data: dict) -> Clearance:
        return cls(user_agent=data["user_agent"], cookies=dict(data["cookies"]), obtained_at=float(data["obtained_at"]))


def _is_cloudflare_cookie(name: str) -> bool:
    # Site cookies (bb_guid, bb_session...) belong to Prowlarr's own session, not ours.
    return name.startswith("cf_") or name.startswith("__cf")


class ByparrClient:
    def __init__(self, url: str, timeout: int, session: aiohttp.ClientSession | None = None):
        self._url = url
        self._timeout = timeout
        self._session = session
        self._owns_session = session is None

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()

    async def solve(self, target_url: str) -> Clearance:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        payload = {"cmd": "request.get", "url": target_url, "maxTimeout": self._timeout * 1000}
        started = time.monotonic()
        try:
            async with self._session.post(
                self._url, json=payload, timeout=aiohttp.ClientTimeout(total=self._timeout + 30)
            ) as resp:
                if resp.status != 200:
                    text = (await resp.text())[:300]
                    raise ByparrError(f"byparr HTTP {resp.status}: {text}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as e:
            raise ByparrError(f"byparr request failed: {e!r}") from e

        if data.get("status") != "ok":
            raise ByparrError(f"byparr returned status={data.get('status')!r}: {data.get('message')!r}")
        solution = data.get("solution") or {}
        user_agent = solution.get("userAgent") or ""
        cookies = {
            c["name"]: c["value"]
            for c in solution.get("cookies") or []
            if _is_cloudflare_cookie(c.get("name", ""))
        }
        if not user_agent:
            raise ByparrError("byparr response has no userAgent")
        if "cf_clearance" not in cookies:
            # Cloudflare did not challenge this time; the UA alone may be enough, but warn.
            log.warning("byparr returned no cf_clearance (status=%s, url=%s)", solution.get("status"), solution.get("url"))

        log.info(
            "byparr solved in %.1fs: status=%s cookies=%s ua=%r",
            time.monotonic() - started, solution.get("status"), sorted(cookies), user_agent,
        )
        return Clearance(user_agent=user_agent, cookies=cookies)
