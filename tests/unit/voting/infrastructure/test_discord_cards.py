"""Vote card reactions read and changed through discord.py, as both the bot and the worker do.

The client here is gateway-less and its HTTP client is scripted, so discord.py's own models parse
the payloads and page through reaction users exactly as they would against Discord.
"""

from typing import Any, cast

import pytest
from aiohttp import ClientResponse
from discord.errors import Forbidden, HTTPException, NotFound
from discord.http import HTTPClient

from squid.voting.domain import VoteMessage
from squid.voting.infrastructure.discord_cards import DiscordVoteCards
from squid.voting.infrastructure.discord_rest import RestOnlyClient


class StubResponse:
    """The subset of `aiohttp.ClientResponse` discord.py's errors read."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "stubbed"


def discord_error(kind: type[HTTPException], status: int) -> HTTPException:
    return kind(cast(ClientResponse, StubResponse(status)), {"code": 0, "message": "stubbed"})


def cards_for(http: object) -> DiscordVoteCards:
    return DiscordVoteCards(RestOnlyClient(cast(HTTPClient, http)))


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
    cards = cards_for(http)

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
    cards = cards_for(StubVoteCardHTTPClient(discord_error(kind, status), {}))

    assert await cards.read(VoteMessage(100, 200, 10), ("1️⃣",)) is None
