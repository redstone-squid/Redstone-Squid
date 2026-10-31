"""Welcome relay Discord-boundary tests."""

from dataclasses import dataclass
from random import Random
from typing import Any, cast, override

import anyio
import discord
import pytest
from pytest_mock import MockerFixture

from squid.bot import welcome_relay
from squid.community.application import WelcomeRelayService
from squid.community.domain import WelcomeRelayPolicy
from squid_ui_discord.testing import AsyncCallRecorder


class CountingRandom(Random):
    def __init__(self) -> None:
        super().__init__(0)
        self.calls = 0

    @override
    def random(self) -> float:
        self.calls += 1
        return 0.0


@dataclass(frozen=True, slots=True)
class Services:
    welcome_relay: WelcomeRelayService


@dataclass(frozen=True, slots=True)
class CommunityConfig:
    welcome_relay_channel_id: int


@dataclass(frozen=True, slots=True)
class Bot:
    services: Services
    community_config: CommunityConfig
    get_or_fetch_messageable_channel: AsyncCallRecorder


@dataclass(frozen=True, slots=True)
class Channel:
    id: int


@dataclass(frozen=True, slots=True)
class Message:
    channel: Channel
    type: discord.MessageType
    system_content: str


@dataclass(frozen=True, slots=True)
class Member:
    id: int
    name: str


@pytest.mark.parametrize("message_first", [False, True])
async def test_welcome_relay_forwards_once_in_either_event_order_without_cache_or_wait(
    message_first: bool,
    mocker: MockerFixture,
) -> None:
    random_source = CountingRandom()
    service = WelcomeRelayService(
        WelcomeRelayPolicy(welcome_channel_id=10, forward_chance=1),
        random_source=random_source,
    )
    general_channel = mocker.Mock(spec=discord.TextChannel)
    fetch_channel = AsyncCallRecorder(result=general_channel)
    bot = Bot(Services(service), CommunityConfig(welcome_relay_channel_id=20), fetch_channel)
    cog = welcome_relay.WelcomeRelay(cast(Any, bot))
    message = cast(
        discord.Message,
        Message(Channel(10), discord.MessageType.new_member, "Everyone welcome Alice!"),
    )
    member = cast(discord.Member, Member(42, "Alice"))
    sent: list[object] = []
    mentions: list[discord.AllowedMentions] = []

    def send_to(channel: object, *, allowed_mentions: discord.AllowedMentions) -> Any:
        assert channel is general_channel
        mentions.append(allowed_mentions)

        async def send(payload: object) -> None:
            sent.append(payload)

        return send

    mocker.patch.object(welcome_relay, "send_to", side_effect=send_to)
    mocker.patch.object(welcome_relay, "text_node", side_effect=lambda content: content)
    mocker.patch.object(welcome_relay, "render_payload", side_effect=lambda nodes: nodes)

    with anyio.fail_after(0.2):
        if message_first:
            await cog.maybe_forward_welcome_message(message)
            fetch_channel.assert_not_awaited()
            await cog.track_new_member(member)
        else:
            await cog.track_new_member(member)
            fetch_channel.assert_not_awaited()
            await cog.maybe_forward_welcome_message(message)

    fetch_channel.assert_awaited_once_with(20)
    assert sent == [["Everyone welcome <@42>!"]]
    assert random_source.calls == 1
    assert len(mentions) == 1
    assert mentions[0].users is False
    assert mentions[0].roles is False
    assert mentions[0].everyone is False
    assert mentions[0].replied_user is False
