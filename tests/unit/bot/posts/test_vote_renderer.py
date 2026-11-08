"""Where a vote session's cards are placed."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, cast, override

import pytest

from squid.accounts.application import AccountService
from squid.accounts.models import Account
from squid.bot.posts import vote_renderer
from squid.bot.posts.vote_renderer import VoteSessionRenderer
from squid.messages.models import MessageRecord
from squid.posts.application import PostService
from squid.posts.domain import ResourceKind
from squid.posts.models import DiscordPost
from squid.runtime import BotServices
from squid.settings.application import SettingsService
from squid.settings.domain import Setting
from squid.voting.application import VoteService
from squid.voting.domain import PollScope, VoteKind, VoteMessage, VoteSessionSnapshot, VoteStatus
from tests.support.services import bot_services
from tests.support.voting import DEFAULT_DELETE_LOG_TARGET, build_snapshot, poll_snapshot

OWNER_GUILD_ID = 10
OTHER_GUILD_ID = 999


@dataclass(frozen=True)
class Guild:
    id: int


@dataclass(frozen=True)
class Channel:
    id: int
    guild: Guild


class VoteRecorder(VoteService):
    def __init__(self, snapshot: VoteSessionSnapshot) -> None:
        self.snapshot = snapshot

    @override
    async def get_session(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        assert vote_session_id == self.snapshot.id
        return self.snapshot


class SettingsRecorder(SettingsService):
    def __init__(self, vote_channels: dict[int, int]) -> None:
        self.vote_channels = vote_channels

    @override
    async def get_many(self, server_ids: Iterable[int], setting: Setting) -> Mapping[int, int | None]:
        assert setting == "Vote"
        return {guild_id: channel_id for channel_id, guild_id in self.vote_channels.items() if guild_id in server_ids}


class PostRecorder(PostService):
    def __init__(self, snapshot: VoteSessionSnapshot) -> None:
        self.snapshot = snapshot

    @override
    async def list_for_resource(self, resource_kind: ResourceKind, resource_key: str) -> Sequence[DiscordPost]:
        assert (resource_kind, resource_key) == ("vote_session", str(self.snapshot.id))
        return [
            DiscordPost(
                message_id=message.id,
                channel_id=message.channel_id,
                resource_kind="vote_session",
                resource_key=resource_key,
                surface="vote_card",
                applied_revision=0,
            )
            for message in self.snapshot.messages
        ]


class AccountRecorder(AccountService):
    def __init__(self) -> None:
        pass

    @override
    async def get_accounts(self, account_ids: Sequence[int]) -> dict[int, Account]:
        return {}


@dataclass(frozen=True)
class Bot:
    guilds: list[Guild]
    channels: dict[int, Channel]
    services: BotServices

    def get_channel(self, channel_id: int) -> Channel | None:
        return self.channels.get(channel_id)


def renderer_for(snapshot: VoteSessionSnapshot, vote_channels: dict[int, int]) -> VoteSessionRenderer[Any]:
    """A renderer whose bot sees `vote_channels` as channel id to guild id."""
    guilds = [Guild(id=guild_id) for guild_id in dict.fromkeys(vote_channels.values())]
    channels = {
        channel_id: Channel(id=channel_id, guild=Guild(id=guild_id)) for channel_id, guild_id in vote_channels.items()
    }
    bot = Bot(
        guilds=guilds,
        channels=channels,
        services=bot_services(
            votes=VoteRecorder(snapshot),
            settings=SettingsRecorder(vote_channels),
            posts=PostRecorder(snapshot),
            accounts=AccountRecorder(),
        ),
    )
    return VoteSessionRenderer(cast(Any, bot))


async def test_a_guild_poll_is_carded_only_where_it_already_is() -> None:
    poll = poll_snapshot(
        guild_id=OWNER_GUILD_ID, scope=PollScope.GUILD, messages=(VoteMessage(100, 200, OWNER_GUILD_ID),)
    )
    renderer = renderer_for(poll, {200: OWNER_GUILD_ID, 201: OTHER_GUILD_ID})

    posts = await renderer.desired(str(poll.id))

    assert posts is not None
    assert [post.channel_id for post in posts] == [200]


async def test_an_open_network_poll_reaches_every_configured_vote_channel() -> None:
    poll = poll_snapshot(
        guild_id=OWNER_GUILD_ID, scope=PollScope.NETWORK, messages=(VoteMessage(100, 200, OWNER_GUILD_ID),)
    )
    renderer = renderer_for(poll, {200: OWNER_GUILD_ID, 201: OTHER_GUILD_ID})

    posts = await renderer.desired(str(poll.id))

    assert posts is not None
    assert sorted(post.channel_id for post in posts) == [200, 201]
    assert {post.channel_id: post.guild_id for post in posts}[201] == OTHER_GUILD_ID


async def test_a_closed_network_poll_gains_no_new_cards() -> None:
    poll = replace(
        poll_snapshot(
            guild_id=OWNER_GUILD_ID, scope=PollScope.NETWORK, messages=(VoteMessage(100, 200, OWNER_GUILD_ID),)
        ),
        status=VoteStatus.CLOSED,
    )
    renderer = renderer_for(poll, {200: OWNER_GUILD_ID, 201: OTHER_GUILD_ID})

    posts = await renderer.desired(str(poll.id))

    assert posts is not None
    assert [post.channel_id for post in posts] == [200]


async def test_a_deleted_vote_target_is_shown_from_its_stored_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Voters still see the message they are judging after it disappears from Discord."""
    snapshot = build_snapshot(kind=VoteKind.DELETE_LOG)
    rendered: list[str] = []
    fetched: list[tuple[int, int]] = []

    async def fetch_stored_message(channel_id: int, message_id: int) -> None:
        fetched.append((channel_id, message_id))

    async def get(message_id: int) -> MessageRecord:
        return MessageRecord(id=message_id, channel_id=1, author_id=2, content="the stored text")

    def render(snapshot: VoteSessionSnapshot, content: str) -> object:
        rendered.append(content)
        return object()

    bot = SimpleNamespace(
        fetch_stored_message=fetch_stored_message, services=SimpleNamespace(messages=SimpleNamespace(get=get))
    )
    renderer = VoteSessionRenderer(cast(Any, bot))

    async def no_channels(snapshot: VoteSessionSnapshot) -> dict[int, int]:
        return {}

    monkeypatch.setattr(renderer, "_published_channels", no_channels)
    monkeypatch.setattr(vote_renderer, "render_delete_log", render)

    await renderer._delete_log(snapshot)

    assert fetched == [(DEFAULT_DELETE_LOG_TARGET.channel_id, DEFAULT_DELETE_LOG_TARGET.message_id)]
    assert rendered == ["the stored text"]
