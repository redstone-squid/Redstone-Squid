"""Exercise the live preview through its browser-facing HTTP endpoints."""

from aiohttp.test_utils import TestClient, TestServer
from bs4 import BeautifulSoup

from scripts.ui_preview import Counter, preview_app


async def test_counter_sessions_and_stale_clicks() -> None:
    async with preview_app(Counter) as app, TestClient(TestServer(app)) as client:
        page = BeautifulSoup(await (await client.get("/")).text(), "html.parser")
        root = page.select_one("main")
        button = page.select_one("button")
        assert root
        assert button
        assert root.find(string="0")
        path = f"/preview/{root['data-session']}/actions"
        generation = int(str(root["data-generation"]))
        click = {"generation": generation, "action": button["data-squid-action"]}

        invalid = await client.post(path, json={**click, "action": "missing"})
        assert invalid.status == 400

        first = await client.post(path, json=click)
        assert first.status == 200
        assert BeautifulSoup(await first.text(), "html.parser").find(string="1")

        replay = await client.post(path, json=click)
        assert replay.headers["X-Generation"] == first.headers["X-Generation"]
        assert await replay.text() == await first.text()

        click["generation"] = int(first.headers["X-Generation"])
        second = await client.post(path, json=click)
        assert second.status == 200
        assert BeautifulSoup(await second.text(), "html.parser").find(string="2")

        other = BeautifulSoup(await (await client.get("/")).text(), "html.parser")
        other_root = other.select_one("main")
        assert other_root
        assert other_root["data-session"] != root["data-session"]
        assert other_root.find(string="0")
