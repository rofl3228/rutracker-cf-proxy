import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from rutracker_proxy.byparr import ByparrClient, ByparrError

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0"


async def start_fake_byparr(handler) -> TestServer:
    app = web.Application()
    app.router.add_post("/v1", handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_solve_keeps_only_cloudflare_cookies():
    received = {}

    async def handler(request):
        received.update(await request.json())
        return web.json_response({
            "status": "ok",
            "message": "Challenge solved!",
            "solution": {
                "url": "https://rutracker.org/forum/login.php",
                "status": 200,
                "userAgent": UA,
                "cookies": [
                    {"name": "cf_clearance", "value": "abc"},
                    {"name": "bb_guid", "value": "site-cookie"},
                    {"name": "__cf_bm", "value": "bm"},
                ],
            },
        })

    server = await start_fake_byparr(handler)
    client = ByparrClient(str(server.make_url("/v1")), timeout=5)
    try:
        clearance = await client.solve("https://rutracker.org/forum/login.php")
    finally:
        await client.close()
        await server.close()

    assert received == {"cmd": "request.get", "url": "https://rutracker.org/forum/login.php", "maxTimeout": 5000}
    assert clearance.user_agent == UA
    assert clearance.cookies == {"cf_clearance": "abc", "__cf_bm": "bm"}


@pytest.mark.parametrize("status,payload,match", [
    (200, {"status": "error", "message": "Timeout"}, "status='error'"),
    (500, {"status": "error"}, "HTTP 500"),
    (200, {"status": "ok", "solution": {"cookies": []}}, "no userAgent"),
])
async def test_solve_errors(status, payload, match):
    async def handler(request):
        return web.json_response(payload, status=status)

    server = await start_fake_byparr(handler)
    client = ByparrClient(str(server.make_url("/v1")), timeout=5)
    try:
        with pytest.raises(ByparrError, match=match):
            await client.solve("https://rutracker.org/")
    finally:
        await client.close()
        await server.close()


async def test_unreachable_byparr():
    client = ByparrClient("http://127.0.0.1:1/v1", timeout=5)
    try:
        with pytest.raises(ByparrError, match="request failed"):
            await client.solve("https://rutracker.org/")
    finally:
        await client.close()
