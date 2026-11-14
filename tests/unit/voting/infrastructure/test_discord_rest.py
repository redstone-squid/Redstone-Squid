"""Discord member lookups, caching, and the gateway-less client's login and shutdown.

Script discord.py HTTP responses to check how interactive lookups and background
weight refreshes distinguish absent members from unavailable membership information.
"""

from typing import Any, cast

import aiohttp
import pytest
from aiohttp import ClientResponse
from discord.errors import DiscordServerError, Forbidden, HTTPException, NotFound, RateLimited
from discord.http import HTTPClient, Route

from squid.core.actors import Actor
from squid.permissions.domain import Decision, PermissionCheck, Reason
from squid.voting.domain import VoteKind
from squid.voting.errors import DiscordMemberServiceUnavailableError
from squid.voting.infrastructure.discord_rest import (
    DiscordRestActorResolver,
    RestOnlyClient,
    rebased_url,
    rest_client,
)


class StubResponse:
    """The subset of `aiohttp.ClientResponse` discord.py's errors read."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "stubbed"


def discord_error(kind: type[HTTPException], status: int) -> HTTPException:
    return kind(cast(ClientResponse, StubResponse(status)), {"code": 0, "message": "stubbed"})


class StubHTTPClient:
    """Replay scripted lookup results, repeating the last result after earlier ones are consumed."""

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


class FakePermissions:
    """Grants the delete-log node to holders of role 11, as a role grant would."""

    async def check(self, actor: Actor, *nodes: str) -> PermissionCheck:
        held = "vote.log_delete.cast" if 11 in actor.discord_role_ids else ""
        return PermissionCheck(tuple(Decision(node, allowed=node == held, reason=Reason.DEFAULT) for node in nodes))


class FakeDiscordIds:
    """Maps every voting account to `account_id * 7`, so the two ids stay distinguishable."""

    def __init__(self, *, known: bool = True) -> None:
        self._known = known

    async def discord_id_for(self, account_id: int) -> int | None:
        return account_id * 7 if self._known else None


def resolver_for(http: StubHTTPClient, **kwargs: Any) -> DiscordRestActorResolver:
    kwargs.setdefault("discord_ids", FakeDiscordIds())
    return DiscordRestActorResolver(RestOnlyClient(cast(HTTPClient, http)), **kwargs)


async def test_member_resolves_capabilities_from_the_payload_role_ids() -> None:
    """Resolve capabilities from the returned roles as well as retaining the raw role IDs."""
    http = StubHTTPClient({"roles": ["11", "22"]})
    resolver = resolver_for(http, permissions=FakePermissions())

    actor = await resolver.member(1, 10, VoteKind.BUILD)

    assert actor is not None
    assert actor.account_id == 1
    assert actor.discord_user_id == 7
    assert actor.guild_id == 10
    assert actor.discord_role_ids == frozenset({11, 22})
    assert actor.capabilities == frozenset({"vote.log_delete.cast"})
    assert http.calls == [(10, 7)]


async def test_an_account_without_a_discord_identity_is_not_a_member() -> None:
    """Not an error: a non-Discord account has no guild membership and so no role weight."""
    http = StubHTTPClient({"roles": ["11"]})
    resolver = resolver_for(http, discord_ids=FakeDiscordIds(known=False))

    assert await resolver.member(1, 10, VoteKind.BUILD) is None
    assert http.calls == []


@pytest.mark.parametrize(("kind", "status"), [(Forbidden, 403), (NotFound, 404)])
async def test_member_returns_none_when_member_is_not_accessible(kind: type[HTTPException], status: int) -> None:
    """Interactive callers receive no member for either absence or an unreadable guild."""
    http = StubHTTPClient(discord_error(kind, status))
    resolver = resolver_for(http)

    assert await resolver.member(1, 10, VoteKind.GENERIC) is None


async def test_a_negative_result_is_cached_like_a_positive_one() -> None:
    http = StubHTTPClient(discord_error(NotFound, 404))
    resolver = resolver_for(http, clock=lambda: 100.0)

    assert await resolver.member(1, 10, VoteKind.GENERIC) is None
    assert await resolver.member(1, 10, VoteKind.GENERIC) is None
    assert len(http.calls) == 1


async def test_member_caches_successful_lookup_for_five_minutes() -> None:
    now = [100.0]
    http = StubHTTPClient({"roles": ["1"]}, {"roles": ["2"]})
    resolver = resolver_for(http, clock=lambda: now[0])

    first = await resolver.member(1, 10, VoteKind.BUILD)
    now[0] = 399.9
    cached = await resolver.member(1, 10, VoteKind.GENERIC)
    now[0] = 400.0
    refreshed = await resolver.member(1, 10, VoteKind.BUILD)

    assert first is not None
    assert first.discord_role_ids == frozenset({1})
    assert cached == first
    assert refreshed is not None
    assert refreshed.discord_role_ids == frozenset({2})
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
    """Rate-limit exhaustion, server errors, and transport failures use the same unavailable error."""
    resolver = resolver_for(StubHTTPClient(error))

    with pytest.raises(DiscordMemberServiceUnavailableError, match="Discord member lookup failed"):
        await resolver.member(1, 10, VoteKind.BUILD)


async def test_resolve_swallows_discord_failure_for_background_refresh() -> None:
    """An unresolved refresh returns None so the service can retain the recorded vote weight."""
    resolver = resolver_for(StubHTTPClient(discord_error(HTTPException, 500)))

    assert await resolver.resolve(1, 10, VoteKind.BUILD) is None


@pytest.mark.parametrize("payload", [{"roles": "11"}, {"roles": ["not-a-snowflake"]}, {}, "not-a-member"])
async def test_member_rejects_malformed_payload(payload: Any) -> None:
    resolver = resolver_for(StubHTTPClient(payload))

    with pytest.raises(DiscordMemberServiceUnavailableError, match="malformed"):
        await resolver.member(1, 10, VoteKind.BUILD)


class RecordedRequests:
    """Replaces `HTTPClient.request` beneath the lazy-login client, answering the login route first."""

    def __init__(self, *login_results: object) -> None:
        self.routes: list[str] = []
        self._login_results = list(login_results)

    async def request(self, route: Route, **kwargs: Any) -> Any:
        self.routes.append(route.url)
        if route.path == "/users/@me":
            result = self._login_results.pop(0) if self._login_results else {"id": "99"}
            if isinstance(result, BaseException):
                raise result
            return result
        return {"roles": []}


async def test_the_client_logs_in_once_on_its_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = RecordedRequests()
    monkeypatch.setattr(HTTPClient, "request", requests.request)
    client = rest_client("token", api_url="http://127.0.0.1:8102/discord/api")
    resolver = DiscordRestActorResolver(client, discord_ids=FakeDiscordIds(), cache_ttl_seconds=0)

    await resolver.member(1, 10, VoteKind.BUILD)
    await resolver.member(1, 10, VoteKind.BUILD)
    await client.close()

    assert requests.routes == [
        "http://127.0.0.1:8102/discord/api/users/@me",
        "http://127.0.0.1:8102/discord/api/guilds/10/members/7",
        "http://127.0.0.1:8102/discord/api/guilds/10/members/7",
    ]


async def test_a_failed_login_is_retried_by_the_next_request(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = RecordedRequests(discord_error(DiscordServerError, 503))
    monkeypatch.setattr(HTTPClient, "request", requests.request)
    client = rest_client("token")
    resolver = DiscordRestActorResolver(client, discord_ids=FakeDiscordIds())

    with pytest.raises(DiscordMemberServiceUnavailableError, match="lookup failed"):
        await resolver.member(1, 10, VoteKind.BUILD)
    actor = await resolver.member(2, 10, VoteKind.BUILD)
    await client.close()

    assert actor is not None
    assert [url.removeprefix(Route.BASE) for url in requests.routes] == [
        "/users/@me",
        "/users/@me",
        "/guilds/10/members/14",
    ]


async def test_a_closed_client_refuses_requests_instead_of_logging_in_again() -> None:
    client = rest_client("token")
    resolver = DiscordRestActorResolver(client, discord_ids=FakeDiscordIds())

    await client.close()

    with pytest.raises(DiscordMemberServiceUnavailableError, match="shut down"):
        await resolver.member(1, 10, VoteKind.BUILD)


class TestConfiguredBase:
    """URL rewriting for member and login routes."""

    def test_the_default_base_leaves_routes_untouched(self) -> None:
        route = Route("GET", "/guilds/{guild_id}/members/{member_id}", guild_id=10, member_id=7)

        assert rebased_url(Route.BASE, route.url) == f"{Route.BASE}/guilds/10/members/7"

    def test_a_configured_base_replaces_the_prefix(self) -> None:
        route = Route("GET", "/guilds/{guild_id}/members/{member_id}", guild_id=10, member_id=7)

        rebased = rebased_url("http://127.0.0.1:8102/discord/api", route.url)

        assert rebased == "http://127.0.0.1:8102/discord/api/guilds/10/members/7"

    def test_login_is_rebased_too(self) -> None:
        """The login route needs the same base override as member requests."""
        assert rebased_url("http://127.0.0.1:8102/discord/api", Route("GET", "/users/@me").url) == (
            "http://127.0.0.1:8102/discord/api/users/@me"
        )

    def test_rate_limit_buckets_do_not_mention_the_host(self) -> None:
        """The route key excludes the host, so rewriting the URL preserves that bucket identity."""
        route = Route("GET", "/guilds/{guild_id}/members/{member_id}", guild_id=10, member_id=7)

        assert route.key == "GET /guilds/{guild_id}/members/{member_id}"


async def test_resolve_reports_a_proven_absence_as_a_weightless_member() -> None:
    """A 404 supplies an actor with no roles or capabilities for recalculating the vote weight."""
    http = StubHTTPClient(discord_error(NotFound, 404))
    resolver = resolver_for(http)

    actor = await resolver.resolve(1, 10, VoteKind.BUILD)

    assert actor is not None
    assert actor.discord_role_ids == frozenset()
    assert actor.capabilities == frozenset()
    assert actor.guild_id == 10


async def test_resolve_keeps_the_cached_weight_when_the_guild_is_unreadable() -> None:
    """A 403 leaves membership unresolved, so refresh must not infer an empty role set."""
    http = StubHTTPClient(discord_error(Forbidden, 403))
    resolver = resolver_for(http)

    assert await resolver.resolve(1, 10, VoteKind.BUILD) is None
    # The gate still denies, because it cannot prove membership either.
    assert await resolver.member(1, 10, VoteKind.BUILD) is None


async def test_resolve_reports_an_account_without_discord_as_weightless() -> None:
    http = StubHTTPClient({"roles": ["11"]})
    resolver = resolver_for(http, discord_ids=FakeDiscordIds(known=False))

    actor = await resolver.resolve(1, 10, VoteKind.BUILD)

    assert actor is not None
    assert actor.discord_role_ids == frozenset()
