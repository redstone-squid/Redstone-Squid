from collections.abc import Callable

import httpx
import pytest

from squid.voting.domain import VoteKind
from squid.voting.errors import DiscordMemberServiceUnavailableError
from squid.voting.infrastructure.discord_rest import DiscordRestActorResolver

type Handler = Callable[[httpx.Request], httpx.Response]


def client_for(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FakeCapabilities:
    """Grants the delete-log node to holders of role 11, as a role grant would."""

    async def capabilities_for(self, *, discord_role_ids, nodes=(), **_kwargs) -> frozenset[str]:
        held = "vote.log_delete.cast" if 11 in set(discord_role_ids) else ""
        return frozenset({node for node in nodes if node == held})


async def test_member_resolves_capabilities_from_the_payload_role_ids() -> None:
    """The REST path used to hardcode both capability flags to False, so a
    `delete_log` vote cast over HTTP was always rejected as ineligible."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"roles": ["11", "22"]})

    async with client_for(handler) as client:
        resolver = DiscordRestActorResolver("secret-token", capabilities=FakeCapabilities(), client=client)
        actor = await resolver.member(1, 7, 10, VoteKind.BUILD)

    assert actor is not None
    assert actor.account_id == 1
    assert actor.discord_id == 7
    assert actor.guild_id == 10
    assert actor.role_ids == frozenset({11, 22})
    assert actor.capabilities == frozenset({"vote.log_delete.cast"})
    assert requests[0].url == httpx.URL("https://discord.com/api/v10/guilds/10/members/7")
    assert requests[0].headers["Authorization"] == "Bot secret-token"


async def test_member_uses_a_configured_loopback_discord_api() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    async with client_for(handler) as client:
        resolver = DiscordRestActorResolver("token", client=client, api_url="http://127.0.0.1:8102/discord/api/")
        assert await resolver.member(1, 7, 10, VoteKind.GENERIC) is None

    assert requests[0].url == httpx.URL("http://127.0.0.1:8102/discord/api/guilds/10/members/7")


@pytest.mark.parametrize("status", [403, 404])
async def test_member_returns_none_when_member_is_not_accessible(status: int) -> None:
    async with client_for(lambda _request: httpx.Response(status)) as client:
        resolver = DiscordRestActorResolver("token", client=client)

        assert await resolver.member(1, 7, 10, VoteKind.GENERIC) is None


async def test_member_caches_successful_lookup_for_five_minutes() -> None:
    calls = 0
    now = [100.0]

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"roles": [str(calls)]})

    async with client_for(handler) as client:
        resolver = DiscordRestActorResolver("token", client=client, clock=lambda: now[0])
        first = await resolver.member(1, 7, 10, VoteKind.BUILD)
        now[0] = 399.9
        cached = await resolver.member(1, 7, 10, VoteKind.GENERIC)
        now[0] = 400.0
        refreshed = await resolver.member(1, 7, 10, VoteKind.BUILD)

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
        actor = await resolver.member(1, 7, 10, VoteKind.BUILD)

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
            await resolver.member(1, 7, 10, VoteKind.BUILD)


async def test_member_maps_transport_failure_to_typed_unavailable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    async with client_for(handler) as client:
        resolver = DiscordRestActorResolver("token", client=client)

        with pytest.raises(DiscordMemberServiceUnavailableError, match="Discord member lookup failed"):
            await resolver.member(1, 7, 10, VoteKind.BUILD)


async def test_resolve_swallows_discord_failure_for_background_refresh() -> None:
    async with client_for(lambda _request: httpx.Response(500)) as client:
        resolver = DiscordRestActorResolver("token", client=client)

        assert await resolver.resolve(1, 7, 10, VoteKind.BUILD) is None


async def test_member_rejects_malformed_role_payload() -> None:
    async with client_for(lambda _request: httpx.Response(200, json={"roles": "11"})) as client:
        resolver = DiscordRestActorResolver("token", client=client)

        with pytest.raises(DiscordMemberServiceUnavailableError, match="malformed"):
            await resolver.member(1, 7, 10, VoteKind.BUILD)
