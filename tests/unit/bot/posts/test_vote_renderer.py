"""Where a vote session's cards are placed."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

from squid.bot.posts.vote_renderer import VoteSessionRenderer
from squid.voting.domain import PollScope, VoteMessage, VoteSessionSnapshot, VoteStatus
from tests.helpers.voting import poll_snapshot

OWNER_GUILD_ID = 10
OTHER_GUILD_ID = 999


class FakePost:
    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        self.is_live = True


def renderer_for(snapshot: VoteSessionSnapshot, vote_channels: dict[int, int]) -> VoteSessionRenderer[Any]:
    """A renderer whose bot sees `vote_channels` as channel id to guild id."""
    guilds = [SimpleNamespace(id=guild_id) for guild_id in dict.fromkeys(vote_channels.values())]
    channels = {
        channel_id: SimpleNamespace(id=channel_id, guild=SimpleNamespace(id=guild_id))
        for channel_id, guild_id in vote_channels.items()
    }
    settings = SimpleNamespace(
        get_many=_async({guild_id: channel_id for channel_id, guild_id in vote_channels.items()})
    )
    posts = SimpleNamespace(list_for_resource=_async([FakePost(message.channel_id) for message in snapshot.messages]))
    accounts = SimpleNamespace(get_accounts=_async({}))
    bot = SimpleNamespace(
        guilds=guilds,
        get_channel=channels.get,
        services=SimpleNamespace(
            votes=SimpleNamespace(get_session_by_id=_async(snapshot)),
            settings=settings,
            posts=posts,
            accounts=accounts,
        ),
    )
    return VoteSessionRenderer(cast(Any, bot))


def _async(value: object):
    async def call(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return value

    return call


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
