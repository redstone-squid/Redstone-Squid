"""Process-local health server tests."""

import aiohttp

from squid.health import ProcessHealthServer


async def test_process_health_server_distinguishes_live_from_ready(unused_tcp_port: int) -> None:
    ready = False

    async def readiness() -> bool:
        return ready

    async with ProcessHealthServer(readiness, port=unused_tcp_port), aiohttp.ClientSession() as client:
        live_response = await client.get(f"http://127.0.0.1:{unused_tcp_port}/livez")
        unavailable_response = await client.get(f"http://127.0.0.1:{unused_tcp_port}/readyz")
        ready = True
        ready_response = await client.get(f"http://127.0.0.1:{unused_tcp_port}/readyz")

    assert live_response.status == 200
    assert await live_response.json() == {"status": "ok"}
    assert unavailable_response.status == 503
    assert await unavailable_response.json() == {"status": "not_ready"}
    assert ready_response.status == 200
    assert await ready_response.json() == {"status": "ready"}
