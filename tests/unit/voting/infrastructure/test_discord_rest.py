"""Discord member lookups, vote card reactions, caching, and client ownership.

Script discord.py HTTP responses to check how interactive lookups and background
weight refreshes distinguish absent members from unavailable membership information.
"""

from typing import Any, cast

import aiohttp
import pytest
from aiohttp import ClientResponse
from discord.errors import DiscordServerError, Forbidden, HTTPException, NotFound, RateLimited
from discord.http import HTTPClient, Route

from squid.voting.domain import VoteKind, VoteMessage
from squid.voting.errors import DiscordMemberServiceUnavailableError
from squid.voting.infrastructure.discord_rest import (
    DiscordRestActorResolver,
    DiscordRestSession,
    DiscordRestVoteCards,
    RestOnlyClient,
    rebased_url,
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


class FakeCapabilities:
    """Grants the delete-log node to holders of role 11, as a role grant would."""

    async def capabilities_for(self, *, discord_role_ids, nodes=(), **_kwargs) -> frozenset[str]:
        held = "vote.log_delete.cast" if 11 in set(discord_role_ids) else ""
        return frozenset({node for node in nodes if node == held})


class FakeDiscordIds:
    """Maps every voting account to `account_id * 7`, so the two ids stay distinguishable."""

    def __init__(self, *, known: bool = True) -> None:
        self._known = known

    async def discord_id_for(self, account_id: int) -> int | None:
        return account_id * 7 if self._known else None


def resolver_for(http: StubHTTPClient, **kwargs: Any) -> DiscordRestActorResolver:
    kwargs.setdefault("discord_ids", FakeDiscordIds())
    return DiscordRestActorResolver(session_for(http), **kwargs)


def session_for(http: object) -> DiscordRestSession:
    """A session whose client sends every request to the stub `http`."""
    return DiscordRestSession.wrapping(RestOnlyClient(cast(HTTPClient, http)))


async def test_member_resolves_capabilities_from_the_payload_role_ids() -> None:
    """Resolve capabilities from the returned roles as well as retaining the raw role IDs."""
    http = StubHTTPClient({"roles": ["11", "22"]})
    resolver = resolver_for(http, capabilities=FakeCapabilities())

    actor = await resolver.member(1, 10, VoteKind.BUILD)

    assert actor is not None
    assert actor.account_id == 1
    assert actor.discord_id == 7
    assert actor.guild_id == 10
    assert actor.role_ids == frozenset({11, 22})
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


async def test_shutdown_closes_an_owned_client_exactly_once() -> None:
    http = StubHTTPClient({"roles": []})
    session = DiscordRestSession("token")
    session._client = RestOnlyClient(cast(HTTPClient, http))  # Stands in for the client a first request logs in.
    resolver = DiscordRestActorResolver(session, discord_ids=FakeDiscordIds())

    await session.aclose()
    await session.aclose()

    assert http.closes == 1
    with pytest.raises(DiscordMemberServiceUnavailableError, match="shut down"):
        await resolver.member(1, 10, VoteKind.BUILD)


async def test_a_wrapped_client_is_not_closed_by_the_session() -> None:
    http = StubHTTPClient({"roles": []})

    await session_for(http).aclose()

    assert http.closes == 0


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
    assert actor.role_ids == frozenset()
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
    assert actor.role_ids == frozenset()


def user_payload(user_id: int, *, bot: bool = False) -> dict[str, Any]:
    return {"id": str(user_id), "username": f"user{user_id}", "discriminator": "0", "avatar": None, "bot": bot}


def message_payload(card: VoteMessage, reactions: list[dict[str, Any]]) -> dict[str, Any]:
    """A message as Discord's REST API returns it, with the fields discord.py's `Message` requires."""
    return {
        "id": str(card.id),
        "channel_id": str(card.channel_id),
        "author": user_payload(99, bot=True),
        "content": "",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "edited_timestamp": None,
        "tts": False,
        "mention_everyone": False,
        "mentions": [],
        "mention_roles": [],
        "attachments": [],
        "embeds": [],
        "pinned": False,
        "type": 0,
        "reactions": reactions,
    }


class StubVoteCardHTTPClient:
    """Serve one card's message and reaction users the way Discord's REST API pages them."""

    def __init__(self, message: Any, users: dict[str, list[dict[str, Any]]]) -> None:
        self.message = message
        self.users = users
        self.pages: list[tuple[str, int | None]] = []
        self.removed: list[tuple[str, int]] = []
        self.added: list[str] = []

    async def get_message(self, channel_id: int, message_id: int) -> Any:
        if isinstance(self.message, BaseException):
            raise self.message
        return self.message

    async def get_reaction_users(
        self, channel_id: int, message_id: int, emoji: str, limit: int, after: int | None = None, type: Any = None
    ) -> Any:
        self.pages.append((emoji, after))
        users = self.users[emoji]
        start = 0 if after is None else next(i for i, user in enumerate(users) if int(user["id"]) == after) + 1
        return users[start : start + limit]

    async def remove_reaction(self, channel_id: int, message_id: int, emoji: str, member_id: int) -> None:
        self.removed.append((emoji, member_id))

    async def add_reaction(self, channel_id: int, message_id: int, emoji: str) -> None:
        self.added.append(emoji)

    async def close(self) -> None:
        pass


async def test_vote_cards_page_through_human_voters_and_report_the_bots_own_reactions() -> None:
    card = VoteMessage(100, 200, 10)
    message = message_payload(
        card,
        [
            {"emoji": {"id": None, "name": "1️⃣"}, "me": True, "count": 151},
            {"emoji": {"id": "123456789012345678", "name": "yes"}, "me": False, "count": 1},
        ],
    )
    one = [user_payload(1, bot=True), *(user_payload(user_id) for user_id in range(2, 152))]
    http = StubVoteCardHTTPClient(message, {"1️⃣": one, "yes:123456789012345678": [user_payload(7)]})
    cards = DiscordRestVoteCards(session_for(http))

    reactions = await cards.read(card, ("1️⃣", "<:yes:123456789012345678>", "2️⃣"))

    assert reactions is not None
    assert sorted(reactions.voters["1️⃣"]) == list(range(2, 152))
    assert reactions.voters["<:yes:123456789012345678>"] == [7]
    assert "2️⃣" not in reactions.voters
    assert reactions.own == {"1️⃣"}
    assert http.pages == [("1️⃣", None), ("1️⃣", 100), ("yes:123456789012345678", None)]
    await cards.remove(card, "<:yes:123456789012345678>", 7)
    await cards.add_own(card, "2️⃣")
    assert (http.removed, http.added) == ([(":yes:123456789012345678", 7)], ["2️⃣"])


@pytest.mark.parametrize(("kind", "status"), [(Forbidden, 403), (NotFound, 404)])
async def test_an_unreadable_vote_card_reads_as_none(kind: type[HTTPException], status: int) -> None:
    cards = DiscordRestVoteCards(session_for(StubVoteCardHTTPClient(discord_error(kind, status), {})))

    assert await cards.read(VoteMessage(100, 200, 10), ("1️⃣",)) is None
