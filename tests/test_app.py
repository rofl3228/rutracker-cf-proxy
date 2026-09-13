from aiohttp.test_utils import TestClient, TestServer

from rutracker_proxy.app import create_app
from rutracker_proxy.config import Settings


async def test_health_without_byparr_is_degraded_but_200():
    settings = Settings(byparr_url="http://127.0.0.1:1/v1", byparr_timeout=2)
    async with TestClient(TestServer(create_app(settings))) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "degraded"
        assert data["clearance"]["present"] is False


async def test_passthrough_without_byparr_is_502():
    settings = Settings(byparr_url="http://127.0.0.1:1/v1", byparr_timeout=2)
    async with TestClient(TestServer(create_app(settings))) as client:
        resp = await client.get("/forum/index.php")
        assert resp.status == 502
        assert "no clearance" in await resp.text()
