"""Exercise the live preview through its browser-facing HTTP endpoints."""

import pytest
from aiohttp.test_utils import TestClient, TestServer
from bs4 import BeautifulSoup

from scripts.ui_preview import preview_app


async def test_counter_sessions_and_stale_clicks() -> None:
    async with preview_app() as app, TestClient(TestServer(app)) as client:
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


@pytest.mark.parametrize(
    ("screen", "button_label", "expected"),
    [
        ("catalogue", "Build card", "Submission ID: 42"),
        ("review", "Approve", "1 approved"),
    ],
)
async def test_showcase_actions(screen: str, button_label: str, expected: str) -> None:
    async with preview_app() as app, TestClient(TestServer(app)) as client:
        page = BeautifulSoup(await (await client.get(f"/?screen={screen}")).text(), "html.parser")
        root = page.select_one("main")
        assert root
        button = next(button for button in root.select("button:enabled") if button.get_text(strip=True) == button_label)
        response = await client.post(
            f"/preview/{root['data-session']}/actions",
            json={"generation": int(str(root["data-generation"])), "action": button["data-squid-action"]},
        )
        assert response.status == 200
        assert expected in BeautifulSoup(await response.text(), "html.parser").get_text()
