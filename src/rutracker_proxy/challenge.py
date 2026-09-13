"""Recognising a Cloudflare challenge page instead of a real RuTracker response."""

from __future__ import annotations

from collections.abc import Mapping

# Markers seen on the "Just a moment..." interstitial (tests/fixtures/cf_challenge.html).
_BODY_MARKERS = (b"<title>Just a moment...</title>", b"challenges.cloudflare.com/", b"/cdn-cgi/challenge-platform/")


def is_challenge(status: int, headers: Mapping[str, str], body: bytes) -> bool:
    """True if the response is a Cloudflare challenge, not content from the site."""
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if status not in (403, 429, 503):
        return False
    # Challenge pages are small; don't scan multi-megabyte bodies.
    head = body[:16_000]
    return any(marker in head for marker in _BODY_MARKERS)
