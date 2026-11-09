"""The /recount command's routing, permission check, and request limits."""

from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from squid.bot.recount import RecountCog
from squid.bot.starboard.cog import StarboardCog
from squid.bot.voting.reactions import VoteReactionHandler
from squid.bot.voting.vote import VoteCog
from squid.messages.models import Message
from squid.posts.application import PostService
from squid.posts.domain import PostReference
from squid.starboard.application import StarboardService
from squid.starboard.application.ports import EntryState
from squid.starboard.models import OriginMessage, StarboardConfig, StarboardEntry
from squid.voting.application import VoteService
from squid.voting.domain import VoteMessage, VoteSessionSnapshot, VoteStatus
from squid_ui.text import NEUTRAL, resolve_text
from squid_ui_discord.testing import AsyncCallRecorder, MessageHarness
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services
from tests.support.voting import poll_snapshot

GUILD_ID = 10
LINK = f"https://discord.com/channels/{GUILD_ID}/200/100"


@dataclass
class VoteReactions(VoteReactionHandler):
    running: set[int] = field(default_factory=set)
    recounted: list[int] = field(default_factory=list)

    def is_recounting(self, vote_session_id: int) -> bool:
        return vote_session_id in self.running

    async def recount(self, vote_session_id: int) -> bool:
        self.recounted.append(vote_session_id)
        return True


@dataclass
class Starboard(StarboardCog):
    recounted: list[int] = field(default_factory=list)

    async def recount(self, message: Any) -> tuple[tuple[int, int], ...]:
        self.recounted.append(message.id)
        return ((1, message.id),)


class Votes(VoteService):
    def __init__(self, snapshot: VoteSessionSnapshot | None) -> None:
        self.get_session_for_message = AsyncCallRecorder(result=snapshot)


class Posts(PostService):
    def __init__(self, mirror_of: int | None) -> None:
        self.mirror_of = mirror_of

    async def resolve(self, message_id: int) -> PostReference | None:
        if self.mirror_of is None:
            return None
        return PostReference(message_id, "starboard_entry", f"1:{self.mirror_of}", "starboard_entry")


class Starboards(StarboardService):
    def __init__(self) -> None:
        pass

    async def entry_state(self, starboard_id: int, origin_message_id: int) -> EntryState:
        return EntryState(
            StarboardConfig(id=starboard_id, guild_id=GUILD_ID, channel_id=200, name="main"),
            OriginMessage(
                id=origin_message_id,
                author_is_bot=False,
                message=Message(id=origin_message_id, channel_id=300, author_id=1),
            ),
            StarboardEntry(starboard_id=starboard_id, origin_message_id=origin_message_id),
        )


class VotingCog(VoteCog):
    def __init__(self, reactions: VoteReactions) -> None:
        self.reactions = reactions


class Harness:
    def __init__(self, *, snapshot: VoteSessionSnapshot | None = None, mirror_of: int | None = None) -> None:
        self.reactions = VoteReactions()
        self.starboard = Starboard()
        self.authorized = 0

        async def get_or_fetch_message(channel_id: int, message_id: int) -> Any:
            return MessageHarness(message_id=message_id, channel_id=channel_id, guild_id=GUILD_ID).source

        cogs = {"VoteCog": VotingCog(self.reactions), "StarboardCog": self.starboard}
        bot = make_layout_bot(
            services=bot_services(votes=Votes(snapshot), posts=Posts(mirror_of), starboards=Starboards()),
            get_cog=cogs.get,
            get_or_fetch_message=get_or_fetch_message,
        )
        # Skip sd.Cog.__init__, which needs a running UI; the command logic reads only these.
        self.cog = cast(Any, RecountCog).__new__(RecountCog)
        self.cog.bot = bot
        self.cog._last_started = {}
        self.cog._origins_in_progress = set()

    async def authorize(self) -> None:
        self.authorized += 1

    async def run(self, link: str = LINK, guild_id: int = GUILD_ID) -> str:
        message = await self.cog.recount_link(guild_id, link, authorize_starboard=self.authorize)
        return resolve_text(message, NEUTRAL).content


def _open_poll() -> VoteSessionSnapshot:
    return poll_snapshot(messages=(VoteMessage(100, 200, GUILD_ID),))


@pytest.mark.parametrize("link", ["not a link", f"https://discord.com/channels/{GUILD_ID + 1}/200/100"])
async def test_only_links_to_this_server_are_accepted(link: str) -> None:
    harness = Harness(snapshot=_open_poll())

    assert "link to a message in this server" in await harness.run(link)
    assert harness.reactions.recounted == []


async def test_a_vote_card_is_recounted_by_anyone_once_per_cooldown() -> None:
    harness = Harness(snapshot=_open_poll())

    assert "Votes recounted" in await harness.run()
    assert "Try again in 30 seconds" in await harness.run()

    assert harness.reactions.recounted == [1]
    assert harness.authorized == 0


async def test_a_vote_already_being_recounted_or_closed_is_refused_without_using_the_cooldown() -> None:
    harness = Harness(snapshot=_open_poll())
    harness.reactions.running.add(1)

    assert "already being recounted" in await harness.run()
    harness.reactions.running.clear()
    assert "Votes recounted" in await harness.run()

    closed = Harness(snapshot=poll_snapshot(status=VoteStatus.CLOSED))
    assert "already closed" in await closed.run()


@pytest.mark.parametrize(("mirror_of", "origin_id"), [(None, 100), (555, 555)])
async def test_a_starboard_recount_needs_permission_and_follows_a_mirror_to_its_source(
    mirror_of: int | None, origin_id: int
) -> None:
    harness = Harness(mirror_of=mirror_of)

    assert "Starboard reactions recounted" in await harness.run()

    assert harness.authorized == 1
    assert harness.starboard.recounted == [origin_id]
