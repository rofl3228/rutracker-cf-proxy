from dataclasses import dataclass, field

import pytest
from curl_cffi.requests import Headers

from rutracker_proxy.byparr import ByparrError, Clearance
from rutracker_proxy.clearance import ClearanceManager
from rutracker_proxy.upstream import ChallengeNotPassed, UpstreamClient, UpstreamError, parse_cookie_header


@dataclass
class FakeCurlResponse:
    status_code: int
    headers: Headers
    content: bytes = b""


@dataclass
class SentRequest:
    method: str
    url: str
    headers: dict
    cookies: dict
    body: bytes | None


class SequenceSolver:
    def __init__(self, fail: bool = False):
        self.calls = 0
        self.fail = fail

    async def solve(self, target_url):
        self.calls += 1
        if self.fail:
            raise ByparrError("byparr down")
        return Clearance(user_agent=f"UA-{self.calls}", cookies={"cf_clearance": f"clr-{self.calls}"})


@dataclass
class Harness:
    client: UpstreamClient
    solver: SequenceSolver
    sent: list = field(default_factory=list)


@pytest.fixture
async def make_client():
    clients = []

    def factory(responses, solver=None):
        solver = solver or SequenceSolver()
        client = UpstreamClient(ClearanceManager(solver, "https://rutracker.org/forum/login.php"),
                                base_url="https://rutracker.org/", retry_delay=0)
        harness = Harness(client, solver)
        queue = list(responses)

        async def fake_send(method, url, headers, client_cookies, body, clearance):
            harness.sent.append(SentRequest(method, url, {**headers, "User-Agent": clearance.user_agent},
                                            {**client_cookies, **clearance.cookies}, body))
            return queue.pop(0)

        client._send = fake_send
        clients.append(client)
        return harness

    yield factory
    for c in clients:
        await c.close()


def ok_page(body=b"<html>ok</html>", extra=()):
    return FakeCurlResponse(200, Headers([("content-type", "text/html; charset=Windows-1251"), *extra]), body)


def challenge_page(html):
    return FakeCurlResponse(403, Headers([("cf-mitigated", "challenge"), ("content-type", "text/html")]), html)


def test_parse_cookie_header():
    assert parse_cookie_header("bb_session=0-1; cf_clearance=x;bad; empty=") == {
        "bb_session": "0-1", "cf_clearance": "x", "empty": "",
    }
    assert parse_cookie_header(None) == {}


async def test_request_uses_clearance_and_keeps_site_cookies(make_client):
    h = make_client([ok_page()])
    resp = await h.client.fetch(
        "POST", "/forum/login.php",
        headers={
            "Cookie": "bb_session=S; cf_clearance=from-prowlarr",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://rutracker.org/forum/login.php",
            "User-Agent": "Prowlarr/2.6",
            "Accept": "*/*",
            "Host": "proxy:8080",
        },
        body=b"login_username=%E2",
    )
    assert resp.status == 200 and not resp.refreshed
    sent = h.sent[0]
    assert sent.url == "https://rutracker.org/forum/login.php"
    assert sent.cookies == {"bb_session": "S", "cf_clearance": "clr-1"}
    assert sent.headers == {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://rutracker.org/forum/login.php",
        "User-Agent": "UA-1",  # byparr's UA, not Prowlarr's
    }
    assert sent.body == b"login_username=%E2"


async def test_challenge_triggers_one_refresh_and_retry(make_client, challenge_html):
    h = make_client([challenge_page(challenge_html), ok_page(b"real")])
    await h.client._clearance.get()  # clearance #1 exists, Cloudflare no longer accepts it
    resp = await h.client.fetch("GET", "forum/tracker.php?nm=test")
    assert resp.body == b"real"
    assert resp.refreshed
    assert h.solver.calls == 2
    assert [s.cookies["cf_clearance"] for s in h.sent] == ["clr-1", "clr-2"]
    assert h.sent[1].headers["User-Agent"] == "UA-2"


async def test_challenge_after_fresh_clearance_raises(make_client, challenge_html):
    h = make_client([challenge_page(challenge_html), challenge_page(challenge_html)])
    with pytest.raises(ChallengeNotPassed):
        await h.client.fetch("GET", "/forum/tracker.php")
    assert h.solver.calls == 2


async def test_byparr_down_raises_upstream_error(make_client):
    h = make_client([], solver=SequenceSolver(fail=True))
    with pytest.raises(UpstreamError, match="no clearance"):
        await h.client.fetch("GET", "/forum/index.php")
    assert h.sent == []


async def test_response_headers_filtered_and_multivalued(make_client):
    h = make_client([FakeCurlResponse(302, Headers([
        ("location", "https://rutracker.org/forum/index.php"),
        ("set-cookie", "bb_session=S; path=/forum/; domain=.rutracker.org"),
        ("set-cookie", "bb_ssl=1; path=/forum/"),
        ("content-encoding", "br"),
        ("content-length", "0"),
        ("transfer-encoding", "chunked"),
    ]))])
    resp = await h.client.fetch("POST", "/forum/login.php", body=b"x")
    assert resp.headers == [
        ("location", "https://rutracker.org/forum/index.php"),
        ("set-cookie", "bb_session=S; path=/forum/; domain=.rutracker.org"),
        ("set-cookie", "bb_ssl=1; path=/forum/"),
    ]
    assert resp.header("Location") == "https://rutracker.org/forum/index.php"


def origin_error(status=522):
    return FakeCurlResponse(status, Headers([("content-type", "text/html"), ("server", "cloudflare")]), b"error code: 522")


async def test_get_retried_once_on_cloudflare_origin_error(make_client):
    h = make_client([origin_error(), ok_page(b"real")])
    resp = await h.client.fetch("GET", "/forum/login.php")
    assert resp.status == 200 and resp.body == b"real"
    assert len(h.sent) == 2


async def test_origin_error_returned_when_retries_exhausted(make_client):
    h = make_client([origin_error(522), origin_error(524)])
    resp = await h.client.fetch("GET", "/forum/login.php")
    assert resp.status == 524
    assert len(h.sent) == 2


async def test_login_post_never_retried(make_client):
    h = make_client([origin_error()])
    resp = await h.client.fetch("POST", "/forum/login.php", body=b"login_username=x")
    assert resp.status == 522
    assert len(h.sent) == 1


async def test_challenge_then_origin_error_then_ok(make_client, challenge_html):
    h = make_client([challenge_page(challenge_html), origin_error(), ok_page(b"real")])
    resp = await h.client.fetch("GET", "/forum/tracker.php")
    assert resp.body == b"real" and resp.refreshed
    assert h.solver.calls == 2


async def test_concurrency_limit():
    import asyncio

    client = UpstreamClient(ClearanceManager(SequenceSolver(), "unused"), base_url="https://rutracker.org", concurrency=2)
    state = {"now": 0, "max": 0}

    async def slow_request(method, url, **kwargs):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.03)
        state["now"] -= 1
        return ok_page()

    client._session.request = slow_request
    try:
        await asyncio.gather(*(client.fetch("GET", f"/forum/tracker.php?nm={i}") for i in range(6)))
    finally:
        await client.close()
    assert state["max"] == 2


async def test_binary_body_untouched(make_client):
    torrent = b"d8:announce40:http://bt.t-ru.org/ann?pk=xxxxxxxxxxxx\x00\xff" * 10
    h = make_client([FakeCurlResponse(200, Headers([("content-type", "application/x-bittorrent")]), torrent)])
    resp = await h.client.fetch("GET", "/forum/dl.php?t=1")
    assert resp.body == torrent
