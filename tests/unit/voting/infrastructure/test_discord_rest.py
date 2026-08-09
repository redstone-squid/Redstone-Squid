from collections.abc import Callable

import httpx
import pytest

from squid.voting.errors import DiscordMemberServiceUnavailableError
from squid.voting.infrastructure.discord_rest import DiscordRestActorResolver

type Handler = Callable[[httpx.Request], httpx.Response]


def client_for(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_member_returns_role_facts_with_transport_privileges_disabled() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"roles": ["11", "22"]})

    async with client_for(handler) as client:
        resolver = DiscordRestActorResolver("secret-token", client=client)
        actor = await resolver.member(7, 10, "build")

    assert actor is not None
    assert actor.user_id == 7
    assert actor.guild_id == 10
    assert actor.role_ids == frozenset({11, 22})
    assert not actor.is_staff
    assert not actor.is_trusted
    assert requests[0].url == httpx.URL("https://discord.com/api/v10/guilds/10/members/7")
    assert requests[0].headers["Authorization"] == "Bot secret-token"


@pytest.mark.parametrize("status", [403, 404])
async def test_member_returns_none_when_member_is_not_accessible(status: int) -> None:
    async with client_for(lambda _request: httpx.Response(status)) as client:
        resolver = DiscordRestActorResolver("token", client=client)

        assert await resolver.member(7, 10, "generic") is None


async def test_member_caches_successful_lookup_for_five_minutes() -> None:
    calls = 0
    now = [100.0]

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"roles": [str(calls)]})

    async with client_for(handler) as client:
        resolver = DiscordRestActorResolver("token", client=client, clock=lambda: now[0])
        first = await resolver.member(7, 10, "build")
        now[0] = 399.9
        cached = await resolver.member(7, 10, "generic")
        now[0] = 400.0
        refreshed = await resolver.member(7, 10, "build")

    assert first is not None
    assert first.role_ids == frozenset({1})
    assert cached == first
    assert refreshed is not None
    assert refreshed.role_ids == frozenset({2})
    assert calls == 2


async def test_member_honors_one_rate_limit_retry() -> None:
    calls = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"retry_after": 0.25})
        return httpx.Response(200, json={"roles": []})

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with client_for(handler) as client:
        resolver = DiscordRestActorResolver("token", client=client, sleep=sleep)
        actor = await resolver.member(7, 10, "build")

    assert actor is not None
    assert delays == [0.25]
    assert calls == 2


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_member_raises_typed_unavailable_error(status: int) -> None:
    async def sleep(_delay: float) -> None:
        pass

    async with client_for(
        lambda _request: httpx.Response(status, json={"retry_after": 0} if status == 429 else None)
    ) as client:
        resolver = DiscordRestActorResolver("token", client=client, sleep=sleep)

        with pytest.raises(DiscordMemberServiceUnavailableError):
            await resolver.member(7, 10, "build")


async def test_member_maps_transport_failure_to_typed_unavailable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with client_for(handler) as client:
        resolver = DiscordRestActorResolver("token", client=client)

        with pytest.raises(DiscordMemberServiceUnavailableError, match="Discord member lookup failed"):
            await resolver.member(7, 10, "build")


async def test_resolve_swallows_discord_failure_for_background_refresh() -> None:
    async with client_for(lambda _request: httpx.Response(500)) as client:
        resolver = DiscordRestActorResolver("token", client=client)

        assert await resolver.resolve(7, 10, "build") is None


async def test_member_rejects_malformed_role_payload() -> None:
    async with client_for(lambda _request: httpx.Response(200, json={"roles": "11"})) as client:
        resolver = DiscordRestActorResolver("token", client=client)

        with pytest.raises(DiscordMemberServiceUnavailableError, match="malformed"):
            await resolver.member(7, 10, "build")
