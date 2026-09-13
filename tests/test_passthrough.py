import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from rutracker_proxy.cache import ResponseCache
from rutracker_proxy.passthrough import Rewriter, cache_key, make_handler, safe_path
from rutracker_proxy.upstream import ChallengeNotPassed, UpstreamError, UpstreamResponse

R = Rewriter("https://rutracker.org")


# ---------- pure rewriting ----------

@pytest.mark.parametrize("value,expected", [
    ("https://rutracker.org/forum/index.php", "/forum/index.php"),
    ("http://rutracker.org/forum/login.php?redirect=tracker.php?nm=test", "/forum/login.php?redirect=tracker.php?nm=test"),
    ("//www.rutracker.org:443/forum/", "/forum/"),
    ("https://rutracker.org", "/"),
    ("index.php", "index.php"),
    ("/forum/index.php", "/forum/index.php"),
    ("https://rutracker.org.evil.com/x", "https://rutracker.org.evil.com/x"),
    ("https://t-ru.org/x", "https://t-ru.org/x"),
])
def test_location(value, expected):
    assert R.location(value) == expected


def test_to_upstream():
    assert R.to_upstream("http://127.0.0.1:30240/forum/login.php") == "https://rutracker.org/forum/login.php"
    assert R.to_upstream("http://proxy:30240/forum/tracker.php?nm=a") == "https://rutracker.org/forum/tracker.php?nm=a"
    assert R.to_upstream("garbage") == "garbage"


@pytest.mark.parametrize("value,expected", [
    ("bb_session=0-123-abc; expires=Tue, 13-Oct-2026 17:00:00 GMT; path=/forum/; domain=.rutracker.org; secure; HttpOnly",
     "bb_session=0-123-abc; expires=Tue, 13-Oct-2026 17:00:00 GMT; path=/forum/; HttpOnly"),
    ("bb_ssl=1; path=/forum/; domain=.rutracker.org; SameSite=None; Secure", "bb_ssl=1; path=/forum/"),
    ("cf_clearance=x; path=/; domain=.rutracker.org", None),
    ("__cf_bm=x; path=/", None),
    ("plain=1", "plain=1"),
])
def test_set_cookie(value, expected):
    assert R.set_cookie(value) == expected


def test_body_rewrites_only_html():
    html = '<a href="https://rutracker.org/forum/profile.php?u=1">юзер</a> <a href="//rutracker.org/forum/">'.encode("cp1251")
    assert R.body("text/html; charset=Windows-1251", html) == '<a href="/forum/profile.php?u=1">юзер</a> <a href="/forum/">'.encode("cp1251")
    torrent = b"d8:announce30:https://rutracker.org/announce"
    assert R.body("application/x-bittorrent", torrent) == torrent


def test_safe_path_masks_secrets():
    assert safe_path("/forum/dl.php?t=1&passkey=abc&x=2") == "/forum/dl.php?t=1&passkey=***&x=2"
    assert safe_path("/forum/tracker.php?nm=test") == "/forum/tracker.php?nm=test"


def test_safe_path_collapses_category_lists():
    many = "&".join(f"f[]={i}" for i in range(87))
    assert safe_path(f"/forum/tracker.php?{many}&nm=Dune&o=1") == "/forum/tracker.php?f[]=<87 categories>&nm=Dune&o=1"
    assert safe_path("/forum/tracker.php?f%5B%5D=1&f%5B%5D=2&f%5B%5D=3&f%5B%5D=4") == "/forum/tracker.php?f[]=<4 categories>"
    assert safe_path("/forum/tracker.php?f[]=33&nm=x") == "/forum/tracker.php?f[]=33&nm=x"


# ---------- handler ----------

class FakeUpstream:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def fetch(self, method, path_and_query, *, headers=None, body=None):
        self.calls.append({"method": method, "path": path_and_query, "headers": headers, "body": body})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def up_response(status=200, headers=(), body=b"", refreshed=False):
    return UpstreamResponse(status=status, headers=list(headers), body=body, elapsed=0.1, refreshed=refreshed)


@pytest.fixture
async def proxy():
    clients = []

    async def factory(result):
        upstream = FakeUpstream(result)
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", make_handler(upstream, R))
        client = TestClient(TestServer(app))
        await client.start_server()
        clients.append(client)
        return client, upstream

    yield factory
    for c in clients:
        await c.close()


async def test_login_post_roundtrip(proxy):
    client, upstream = await proxy(up_response(302, [
        ("location", "https://rutracker.org/forum/index.php"),
        ("set-cookie", "bb_session=S; path=/forum/; domain=.rutracker.org; secure; HttpOnly"),
        ("set-cookie", "bb_ssl=1; path=/forum/; domain=.rutracker.org"),
        ("set-cookie", "cf_clearance=x; domain=.rutracker.org"),
        ("content-type", "text/html; charset=cp1251"),
        ("server", "cloudflare"),
    ]))
    form = b"login_username=%E8%E2%E0%ED&login_password=secret&login=%C2%F5%EE%E4"
    resp = await client.post(
        "/forum/login.php", data=form, allow_redirects=False,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Referer": str(client.make_url("/forum/login.php")), "Cookie": "bb_guid=G"},
    )

    call = upstream.calls[0]
    assert call["method"] == "POST" and call["path"] == "/forum/login.php"
    assert call["body"] == form
    assert call["headers"]["Referer"] == "https://rutracker.org/forum/login.php"
    assert call["headers"]["Cookie"] == "bb_guid=G"

    assert resp.status == 302
    assert resp.headers["Location"] == "/forum/index.php"
    assert resp.headers.getall("Set-Cookie") == ["bb_session=S; path=/forum/; HttpOnly", "bb_ssl=1; path=/forum/"]
    assert "cloudflare" not in resp.headers.get("Server", "")


async def test_cp1251_query_passed_raw(proxy):
    client, upstream = await proxy(up_response(200, [("content-type", "text/html; charset=Windows-1251")], "<b>Матрица</b>".encode("cp1251")))
    url = client.make_url("").join(URL("/forum/tracker.php?nm=%CC%E0%F2%F0%E8%F6%E0&f=1", encoded=True))
    resp = await client.session.get(URL(str(url), encoded=True))
    assert upstream.calls[0]["path"] == "/forum/tracker.php?nm=%CC%E0%F2%F0%E8%F6%E0&f=1"
    assert resp.headers["Content-Type"] == "text/html; charset=Windows-1251"
    assert await resp.read() == "<b>Матрица</b>".encode("cp1251")


async def test_torrent_download_untouched(proxy):
    torrent = b"d8:announce44:https://rutracker.org/ann?pk=0123456789abcdef\x00\xff"
    client, _ = await proxy(up_response(200, [
        ("content-type", "application/x-bittorrent; charset=Windows-1251"),
        ("content-disposition", 'attachment; filename="[rutracker.org].t1.torrent"'),
    ], torrent))
    resp = await client.get("/forum/dl.php?t=1")
    assert resp.status == 200
    assert await resp.read() == torrent
    assert resp.headers["Content-Disposition"] == 'attachment; filename="[rutracker.org].t1.torrent"'


@pytest.mark.parametrize("error,status", [
    (ChallengeNotPassed("still challenged"), 503),
    (UpstreamError("GET x failed: Operation timed out after 90000 milliseconds"), 504),
    (UpstreamError("no clearance available: byparr down"), 502),
])
async def test_errors(proxy, error, status):
    client, _ = await proxy(error)
    resp = await client.get("/forum/tracker.php?nm=x")
    assert resp.status == status
    assert resp.headers["Retry-After"] == "60"
    assert "rutracker-proxy" in await resp.text()


# ---------- caching ----------

def test_cache_key_rules():
    assert cache_key("GET", "/forum/tracker.php?nm=a", "bb_session=S1") == cache_key("GET", "/forum/tracker.php?nm=a", "x=1; bb_session=S1")
    assert cache_key("GET", "/forum/tracker.php?nm=a", "bb_session=S1") != cache_key("GET", "/forum/tracker.php?nm=a", "bb_session=S2")
    assert cache_key("GET", "/forum/tracker.php?nm=a", None).startswith("anon ")
    assert "S1" not in cache_key("GET", "/forum/tracker.php?nm=a", "bb_session=S1")  # session value is hashed
    for method, path in [("POST", "/forum/tracker.php"), ("GET", "/forum/login.php"), ("GET", "/forum/index.php"),
                         ("GET", "/forum/dl.php?t=1"), ("HEAD", "/forum/tracker.php")]:
        assert cache_key(method, path, "bb_session=S1") is None


@pytest.fixture
async def cached_proxy():
    clients = []

    async def factory(result):
        upstream = FakeUpstream(result)
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", make_handler(upstream, R, ResponseCache(ttl=300, max_entries=10)))
        client = TestClient(TestServer(app))
        await client.start_server()
        clients.append(client)
        return client, upstream

    yield factory
    for c in clients:
        await c.close()


RESULTS_PAGE = up_response(200, [("content-type", "text/html; charset=Windows-1251")], b'<a class="tr-dl" href="dl.php?t=1">x</a>')


async def test_search_cached_per_session(cached_proxy):
    client, upstream = await cached_proxy(RESULTS_PAGE)
    s1 = {"Cookie": "bb_session=S1"}
    first = await client.get("/forum/tracker.php?nm=Dune", headers=s1)
    second = await client.get("/forum/tracker.php?nm=Dune", headers=s1)
    other_session = await client.get("/forum/tracker.php?nm=Dune", headers={"Cookie": "bb_session=S2"})
    assert (first.headers["X-Cache"], second.headers["X-Cache"], other_session.headers["X-Cache"]) == ("MISS", "HIT", "MISS")
    assert await second.read() == await first.read()
    assert len(upstream.calls) == 2


async def test_empty_results_not_cached(cached_proxy):
    client, upstream = await cached_proxy(up_response(200, [("content-type", "text/html")], "<p>Не найдено</p>".encode("cp1251")))
    await client.get("/forum/tracker.php?nm=nothing")
    resp = await client.get("/forum/tracker.php?nm=nothing")
    assert resp.headers["X-Cache"] == "MISS"
    assert len(upstream.calls) == 2


async def test_login_and_downloads_bypass_cache(cached_proxy):
    client, upstream = await cached_proxy(RESULTS_PAGE)
    for _ in range(2):
        r1 = await client.get("/forum/dl.php?t=1")
        r2 = await client.post("/forum/login.php", data=b"x")
    assert "X-Cache" not in r1.headers and "X-Cache" not in r2.headers
    assert len(upstream.calls) == 4


async def test_method_not_allowed(proxy):
    client, upstream = await proxy(up_response())
    resp = await client.put("/forum/index.php", data=b"x")
    assert resp.status == 405
    assert upstream.calls == []


async def test_head(proxy):
    client, upstream = await proxy(up_response(200, [("content-type", "text/html")], b""))
    resp = await client.head("/forum/index.php")
    assert resp.status == 200
    assert upstream.calls[0]["method"] == "HEAD" and upstream.calls[0]["body"] is None
