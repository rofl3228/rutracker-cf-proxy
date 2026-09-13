"""Settings, read once from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from e


def _str(name: str, default: str) -> str:
    return os.environ.get(name) or default


@dataclass(frozen=True)
class Settings:
    # Where the proxy listens.
    host: str = "0.0.0.0"
    port: int = 8080

    # Real site. Only one mirror is needed: protection is identical on all of them.
    upstream_url: str = "https://rutracker.org"

    # byparr (FlareSolverr-compatible) endpoint.
    byparr_url: str = "http://127.0.0.1:30230/v1"
    # URL byparr opens to obtain cf_clearance. Must be a path Cloudflare actually challenges.
    clearance_url: str = "https://rutracker.org/forum/login.php"
    byparr_timeout: int = 120  # seconds byparr may spend solving

    # curl_cffi browser fingerprint. Must match the browser byparr runs (currently Firefox).
    impersonate: str = "firefox"
    # cf_clearance is bound to the IP it was solved from; byparr exits via IPv4.
    ip_family: str = "4"  # "4", "6" or "any"
    upstream_timeout: int = 90  # rutracker itself is sometimes slow
    # Extra attempts for GET/HEAD when Cloudflare reports 520-524 (rutracker servers not answering).
    origin_retries: int = 1
    # Simultaneous requests to rutracker (byparr solves are not counted).
    upstream_concurrency: int = 2

    # Search results cache (seconds, 0 = off) and its size.
    cache_ttl: int = 300
    cache_max_entries: int = 100

    # Refresh clearance proactively once it is this old (seconds). 0 = only on demand.
    clearance_max_age: int = 0
    # Where to keep clearance across restarts. Empty = memory only.
    clearance_file: Path | None = None

    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        clearance_file = os.environ.get("CLEARANCE_FILE", "")
        ip_family = _str("IP_FAMILY", cls.ip_family)
        if ip_family not in ("4", "6", "any"):
            raise ValueError(f"IP_FAMILY must be 4, 6 or any, got {ip_family!r}")
        return cls(
            host=_str("HOST", cls.host),
            port=_int("PORT", cls.port),
            upstream_url=_str("UPSTREAM_URL", cls.upstream_url).rstrip("/"),
            byparr_url=_str("BYPARR_URL", cls.byparr_url),
            clearance_url=_str("CLEARANCE_URL", cls.clearance_url),
            byparr_timeout=_int("BYPARR_TIMEOUT", cls.byparr_timeout),
            impersonate=_str("IMPERSONATE", cls.impersonate),
            ip_family=ip_family,
            upstream_timeout=_int("UPSTREAM_TIMEOUT", cls.upstream_timeout),
            origin_retries=_int("ORIGIN_RETRIES", cls.origin_retries),
            upstream_concurrency=_int("UPSTREAM_CONCURRENCY", cls.upstream_concurrency),
            cache_ttl=_int("CACHE_TTL", cls.cache_ttl),
            cache_max_entries=_int("CACHE_MAX_ENTRIES", cls.cache_max_entries),
            clearance_max_age=_int("CLEARANCE_MAX_AGE", cls.clearance_max_age),
            clearance_file=Path(clearance_file) if clearance_file else None,
            log_level=_str("LOG_LEVEL", cls.log_level).upper(),
        )
