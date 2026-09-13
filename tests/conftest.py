from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def challenge_html() -> bytes:
    """Real Cloudflare 'Just a moment...' page captured from a Prowlarr trace log."""
    return (FIXTURES / "cf_challenge.html").read_bytes()


@pytest.fixture
def rutracker_html() -> bytes:
    """Minimal stand-in for a logged-in RuTracker page, windows-1251 like the real site."""
    return (
        '<html lang="ru"><head><meta charset="Windows-1251"><title>RuTracker.org</title></head>'
        '<body><a id="logged-in-username" href="#">юзер</a></body></html>'
    ).encode("cp1251")
