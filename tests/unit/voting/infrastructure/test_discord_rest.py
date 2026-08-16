"""Discord member resolution over discord.py's rate-limited HTTP client.

The adapter no longer owns a rate limiter, so there is nothing here about
`retry_after`, retry counts, or backoff: that contract is discord.py's, and
discord.py tests it. What is still ours is the mapping from its typed errors onto
the promises `member()` and `resolve()` make to the voting service.
"""

from typing import Any, cast

import aiohttp
import pytest
from aiohttp import ClientResponse
from discord.errors import DiscordServerError, Forbidden, HTTPException, NotFound, RateLimited
from discord.http import Route

from squid.voting.domain import VoteKind
from squid.voting.errors import DiscordMemberServiceUnavailableError
from squid.voting.infrastructure.discord_rest import DiscordRestActorResolver, rebased_url


class StubResponse:
    """The subset of `aiohttp.ClientResponse` discord.py's errors read."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "stubbed"


def discord_error(kind: type[HTTPException], status: int) -> HTTPException:
    return kind(cast(ClientResponse, StubResponse(status)), {"code": 0, "message": "stubbed"})


class StubHTTPClient:
    """Records member lookups and replays a scripted result for each."""

    def __init__(self, *results: Any) -> None:
        self._results = list(results)
        self.calls: list[tuple[int, int]] = []
        self.closes = 0

    async def get_member(self, guild_id: int, member_id: int) -> Any:
        self.calls.append((guild_id, member_id))
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, BaseException):
            raise result
        return result

    async def close(self) -> None:
        self.closes += 1


class FakeCapabilities:
    """Grants the delete-log node to holders of role 11, as a role grant would."""

    async def capabilities_for(self, *, discord_role_ids, nodes=(), **_kwargs) -> frozenset[str]:
        held = "vote.log_delete.cast" if 11 in set(discord_role_ids) else ""
        return frozenset({node for node in nodes if node == held})


def resolver_for(http: StubHTTPClient, **kwargs: Any) -> DiscordRestActorResolver:
    return DiscordRestActorResolver("token", http=http, **kwargs)


async def test_member_resolves_capabilities_from_the_payload_role_ids() -> None:
    """The REST path used to hardcode both capability flags to False, so a
    `delete_log` vote cast over HTTP was always rejected as ineligible."""
    http = StubHTTPClient({"roles": ["11", "22"]})
    resolver = resolver_for(http, capabilities=FakeCapabilities())

    actor = await resolver.member(1, 7, 10, VoteKind.BUILD)

    assert actor is not None
    assert actor.account_id == 1
    assert actor.discord_id == 7
    assert actor.guild_id == 10
    assert actor.role_ids == frozenset({11, 22})
    assert actor.capabilities == frozenset({"vote.log_delete.cast"})
    assert http.calls == [(10, 7)]


@pytest.mark.parametrize(("kind", "status"), [(Forbidden, 403), (NotFound, 404)])
async def test_member_returns_none_when_member_is_not_accessible(kind: type[HTTPException], status: int) -> None:
    """Not a member, or not visible to this token: a fact, not a failure."""
    http = StubHTTPClient(discord_error(kind, status))
    resolver = resolver_for(http)

    assert await resolver.member(1, 7, 10, VoteKind.GENERIC) is None


async def test_a_negative_result_is_cached_like_a_positive_one() -> None:
    http = StubHTTPClient(discord_error(NotFound, 404))
    resolver = resolver_for(http, clock=lambda: 100.0)

    assert await resolver.member(1, 7, 10, VoteKind.GENERIC) is None
    assert await resolver.member(1, 7, 10, VoteKind.GENERIC) is None
    assert len(http.calls) == 1


async def test_member_caches_successful_lookup_for_five_minutes() -> None:
    now = [100.0]
    http = StubHTTPClient({"roles": ["1"]}, {"roles": ["2"]})
    resolver = resolver_for(http, clock=lambda: now[0])

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
    assert len(http.calls) == 2


@pytest.mark.parametrize(
    "error",
    [
        RateLimited(120.0),
        discord_error(DiscordServerError, 503),
        discord_error(HTTPException, 500),
        aiohttp.ClientConnectionError("refused"),
        TimeoutError(),
    ],
)
async def test_member_raises_typed_unavailable_error(error: Exception) -> None:
    """A rate limit past `max_ratelimit_timeout`, a 5xx, and a transport failure
    all mean the same thing to a vote: Discord cannot answer truthfully now."""
    resolver = resolver_for(StubHTTPClient(error))

    with pytest.raises(DiscordMemberServiceUnavailableError, match="Discord member lookup failed"):
        await resolver.member(1, 7, 10, VoteKind.BUILD)


async def test_resolve_swallows_discord_failure_for_background_refresh() -> None:
    """This is why a vote does not become un-castable when Discord hiccups."""
    resolver = resolver_for(StubHTTPClient(discord_error(HTTPException, 500)))

    assert await resolver.resolve(1, 7, 10, VoteKind.BUILD) is None


@pytest.mark.parametrize("payload", [{"roles": "11"}, {"roles": ["not-a-snowflake"]}, {}, "not-a-member"])
async def test_member_rejects_malformed_payload(payload: Any) -> None:
    resolver = resolver_for(StubHTTPClient(payload))

    with pytest.raises(DiscordMemberServiceUnavailableError, match="malformed"):
        await resolver.member(1, 7, 10, VoteKind.BUILD)


async def test_shutdown_closes_an_owned_client_exactly_once() -> None:
    http = StubHTTPClient({"roles": []})
    resolver = DiscordRestActorResolver("token")
    resolver._http = http
    resolver._owns_http = True

    await resolver.aclose()
    await resolver.aclose()

    assert http.closes == 1
    with pytest.raises(DiscordMemberServiceUnavailableError, match="shut down"):
        await resolver.member(1, 7, 10, VoteKind.BUILD)


async def test_an_injected_client_is_not_closed_by_the_resolver() -> None:
    http = StubHTTPClient({"roles": []})

    await resolver_for(http).aclose()

    assert http.closes == 0


class TestConfiguredBase:
    """A loopback upstream override has to reach every route, login included."""

    def test_the_default_base_leaves_routes_untouched(self) -> None:
        route = Route("GET", "/guilds/{guild_id}/members/{member_id}", guild_id=10, member_id=7)

        assert rebased_url(Route.BASE, route.url) == f"{Route.BASE}/guilds/10/members/7"

    def test_a_configured_base_replaces_the_prefix(self) -> None:
        route = Route("GET", "/guilds/{guild_id}/members/{member_id}", guild_id=10, member_id=7)

        rebased = rebased_url("http://127.0.0.1:8102/discord/api", route.url)

        assert rebased == "http://127.0.0.1:8102/discord/api/guilds/10/members/7"

    def test_login_is_rebased_too(self) -> None:
        """`static_login` builds its own route, so a configured deployment would
        otherwise validate its token against the real Discord."""
        assert rebased_url("http://127.0.0.1:8102/discord/api", Route("GET", "/users/@me").url) == (
            "http://127.0.0.1:8102/discord/api/users/@me"
        )

    def test_rate_limit_buckets_do_not_mention_the_host(self) -> None:
        """Which is why rewriting the URL leaves discord.py's accounting intact."""
        route = Route("GET", "/guilds/{guild_id}/members/{member_id}", guild_id=10, member_id=7)

        assert route.key == "GET /guilds/{guild_id}/members/{member_id}"
