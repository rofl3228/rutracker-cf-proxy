"""UpstreamClient with the real curl_cffi transport against a local HTTP server."""

from aiohttp import web
from aiohttp.test_utils import TestServer

from rutracker_proxy.byparr import Clearance
from rutracker_proxy.clearance import ClearanceManager
from rutracker_proxy.upstream import UpstreamClient


class OneShotSolver:
    async def solve(self, target_url):
        return Clearance(user_agent="Byparr-UA/1.0", cookies={"cf_clearance": "CLR"})


async def test_cookies_ua_body_reach_server_and_nothing_is_remembered():
    seen = []

    async def handler(request: web.Request):
        seen.append({
            "cookies": dict(request.cookies),
            "ua": request.headers.get("User-Agent"),
            "body": await request.read(),
        })
        resp = web.Response(status=302, headers={"Location": "/forum/index.php"})
        resp.set_cookie("bb_session", "NEW", path="/forum/")
        return resp

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    client = UpstreamClient(ClearanceManager(OneShotSolver(), "unused"), base_url=str(server.make_url("")))
    try:
        first = await client.fetch("POST", "/forum/login.php", headers={"Cookie": "bb_guid=G"}, body=b"\xe0\xe1=1")
        second = await client.fetch("GET", "/forum/index.php")
    finally:
        await client.close()
        await server.close()

    assert first.status == 302  # redirects are not followed
    assert any(k == "Set-Cookie" and v.startswith("bb_session=NEW") for k, v in first.headers)
    assert seen[0] == {"cookies": {"bb_guid": "G", "cf_clearance": "CLR"}, "ua": "Byparr-UA/1.0", "body": b"\xe0\xe1=1"}
    # bb_session from the first response must NOT be replayed: the proxy is stateless.
    assert seen[1]["cookies"] == {"cf_clearance": "CLR"}
