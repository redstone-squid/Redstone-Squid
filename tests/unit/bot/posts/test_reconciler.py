"""Reconcile-loop tests.

These pin the properties that used to be reimplemented per surface: publishing to
every configured channel, never duplicating on redelivery, filling only the channel a
partial failure missed, and removing posts a resource no longer wants.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from whenever import Instant

import squid_discord as sd
from squid.bot.posts.reconciler import PostReconciler
from squid.bot.posts.renderer import DesiredPost
from squid.posts.domain import DiscordPost, ResourceKind, Surface

GUILD = 500


@dataclass
class FakeMessage:
    id: int
    channel_id: int
    edits: list[object] = field(default_factory=list)
    deleted: bool = False

    @property
    def channel(self) -> Any:
        return _Identified(self.channel_id)

    @property
    def guild(self) -> Any:
        return _Identified(GUILD)

    @property
    def author(self) -> Any:
        return _Identified(999)

    content: str = ""
    flags: Any = field(default_factory=lambda: SimpleNamespace(components_v2=True))

    async def edit(self, **kwargs: object) -> FakeMessage:
        self.edits.append(kwargs.get("view"))
        return self

    async def delete(self) -> None:
        self.deleted = True

    @property
    def created_at(self) -> datetime:
        return datetime(2026, 8, 15, tzinfo=UTC)


@dataclass
class _Identified:
    id: int


class FakeChannel:
    """A channel that hands out message ids and remembers what was sent."""

    _next_id = 1000

    def __init__(self, channel_id: int, *, explode: bool = False) -> None:
        self.id = channel_id
        self.explode = explode
        self.sent: list[object] = []
        self.messages: dict[int, FakeMessage] = {}

    async def send(self, *, files: object, view: object, allowed_mentions: object) -> FakeMessage:
        del files, allowed_mentions
        if self.explode:
            msg = "discord is down"
            raise RuntimeError(msg)
        FakeChannel._next_id += 1
        message = FakeMessage(id=FakeChannel._next_id, channel_id=self.id)
        self.messages[message.id] = message
        self.sent.append(view)
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        try:
            return self.messages[message_id]
        except KeyError:
            # discord.py only reads `status` and `reason` off the response here.
            response = cast(Any, SimpleNamespace(status=404, reason="Not Found"))
            raise discord.NotFound(response, "Unknown Message") from None


class FakePosts:
    """An in-memory stand-in enforcing the one-live-post-per-channel rule."""

    def __init__(self) -> None:
        self.posts: dict[int, DiscordPost] = {}

    async def list_for_resource(self, resource_kind: ResourceKind, resource_key: str) -> Sequence[DiscordPost]:
        return [
            post
            for post in self.posts.values()
            if post.resource_kind == resource_kind and post.resource_key == resource_key
        ]

    async def record(
        self,
        *,
        message_id: int,
        channel_id: int,
        resource_kind: ResourceKind,
        resource_key: str,
        surface: Surface,
        applied_revision: int,
    ) -> None:
        clash = [
            post
            for post in self.posts.values()
            if post.resource_kind == resource_kind
            and post.resource_key == resource_key
            and post.channel_id == channel_id
            and post.is_live
        ]
        if clash:
            msg = "a live post already exists for this resource and channel"
            raise AssertionError(msg)
        self.posts[message_id] = DiscordPost(
            message_id=message_id,
            channel_id=channel_id,
            resource_kind=resource_kind,
            resource_key=resource_key,
            surface=surface,
            applied_revision=applied_revision,
        )

    async def mark_rendered(self, message_id: int, applied_revision: int) -> None:
        post = self.posts[message_id]
        if post.applied_revision < applied_revision:
            self.posts[message_id] = DiscordPost(
                message_id=post.message_id,
                channel_id=post.channel_id,
                resource_kind=post.resource_kind,
                resource_key=post.resource_key,
                surface=post.surface,
                applied_revision=applied_revision,
            )

    async def suppress(self, message_id: int) -> bool:
        post = self.posts.get(message_id)
        if post is None or not post.is_live:
            return False
        self.posts[message_id] = DiscordPost(
            message_id=post.message_id,
            channel_id=post.channel_id,
            resource_kind=post.resource_kind,
            resource_key=post.resource_key,
            surface=post.surface,
            applied_revision=post.applied_revision,
            suppressed_at=Instant.now(),
        )
        return True

    async def forget(self, message_id: int) -> None:
        self.posts.pop(message_id, None)


class FakeMessages:
    def __init__(self) -> None:
        self.observed: list[int] = []
        self.deleted: list[int] = []

    async def observe(self, fact: Any) -> None:
        self.observed.append(fact.id)

    async def mark_deleted(self, message_id: int) -> bool:
        self.deleted.append(message_id)
        return True


class FakeRenderer:
    resource_kind: ResourceKind = "build"

    def __init__(self, channels: Sequence[FakeChannel] | None, *, repost_if_deleted: bool = False) -> None:
        self.channels = channels
        self.repost_if_deleted = repost_if_deleted
        self.after_send_calls: list[int] = []

    async def desired(self, resource_key: str) -> Sequence[DesiredPost] | None:
        del resource_key
        if self.channels is None:
            return None
        return [
            DesiredPost(
                channel_id=channel.id,
                guild_id=GUILD,
                surface="build_card",
                presentation=sd.render_static([]),
            )
            for channel in self.channels
        ]

    async def after_send(self, resource_key: str, message: Any) -> None:
        del resource_key
        self.after_send_calls.append(message.id)


class FakeBot:
    def __init__(self, channels: Sequence[FakeChannel]) -> None:
        self.channels = {channel.id: channel for channel in channels}
        self.services = _Services()

    async def get_or_fetch_messageable_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channels.get(channel_id)


@dataclass
class _Services:
    posts: FakePosts = field(default_factory=FakePosts)
    messages: FakeMessages = field(default_factory=FakeMessages)


def _reconciler(channels: Sequence[FakeChannel], renderer: FakeRenderer) -> Any:
    return PostReconciler(FakeBot(channels), [renderer])  # type: ignore[arg-type]


async def test_publishes_to_every_configured_channel() -> None:
    channels = [FakeChannel(1), FakeChannel(2)]
    renderer = FakeRenderer(channels)
    reconciler = _reconciler(channels, renderer)

    await reconciler.reconcile("build", "42", 7)

    assert [len(channel.sent) for channel in channels] == [1, 1]
    assert len(reconciler.bot.services.posts.posts) == 2
    assert sorted(renderer.after_send_calls) == sorted(reconciler.bot.services.posts.posts)


async def test_reconciling_twice_does_not_publish_again() -> None:
    """The job queue is at-least-once, so a repeat run must not duplicate the card."""
    channels = [FakeChannel(1), FakeChannel(2)]
    reconciler = _reconciler(channels, FakeRenderer(channels))

    await reconciler.reconcile("build", "42", 7)
    await reconciler.reconcile("build", "42", 8)

    assert [len(channel.sent) for channel in channels] == [1, 1]


async def test_a_partial_failure_is_repaired_by_posting_only_the_missing_channel() -> None:
    channels = [FakeChannel(1), FakeChannel(2, explode=True)]
    reconciler = _reconciler(channels, FakeRenderer(channels))

    with pytest.raises(RuntimeError, match="discord is down"):
        await reconciler.reconcile("build", "42", 7)
    assert len(channels[0].sent) == 1

    channels[1].explode = False
    await reconciler.reconcile("build", "42", 7)

    assert [len(channel.sent) for channel in channels] == [1, 1]


async def test_a_newer_generation_edits_rather_than_reposts() -> None:
    channels = [FakeChannel(1)]
    reconciler = _reconciler(channels, FakeRenderer(channels))
    await reconciler.reconcile("build", "42", 7)
    posted = next(iter(channels[0].messages.values()))

    await reconciler.reconcile("build", "42", 9)

    assert len(channels[0].sent) == 1
    assert len(posted.edits) == 1


async def test_an_already_current_post_is_not_edited() -> None:
    """Re-running at the same generation costs no Discord calls."""
    channels = [FakeChannel(1)]
    reconciler = _reconciler(channels, FakeRenderer(channels))
    await reconciler.reconcile("build", "42", 7)
    posted = next(iter(channels[0].messages.values()))

    await reconciler.reconcile("build", "42", 7)

    assert posted.edits == []


async def test_a_vanished_resource_removes_every_post() -> None:
    channels = [FakeChannel(1), FakeChannel(2)]
    renderer = FakeRenderer(channels)
    reconciler = _reconciler(channels, renderer)
    await reconciler.reconcile("build", "42", 7)
    posted = [message for channel in channels for message in channel.messages.values()]

    renderer.channels = None
    await reconciler.reconcile("build", "42", 8)

    assert all(message.deleted for message in posted)
    assert reconciler.bot.services.posts.posts == {}


async def test_a_channel_that_is_no_longer_wanted_loses_its_post() -> None:
    channels = [FakeChannel(1), FakeChannel(2)]
    renderer = FakeRenderer(channels)
    reconciler = _reconciler(channels, renderer)
    await reconciler.reconcile("build", "42", 7)
    dropped = next(iter(channels[1].messages.values()))

    renderer.channels = [channels[0]]
    await reconciler.reconcile("build", "42", 8)

    assert dropped.deleted is True
    assert [post.channel_id for post in reconciler.bot.services.posts.posts.values()] == [1]


async def test_a_hand_deleted_post_stays_deleted_by_default() -> None:
    """Removing a build card is a moderator decision, not damage to repair."""
    channels = [FakeChannel(1)]
    reconciler = _reconciler(channels, FakeRenderer(channels))
    await reconciler.reconcile("build", "42", 7)
    posted = next(iter(channels[0].messages.values()))
    del channels[0].messages[posted.id]

    await reconciler.reconcile("build", "42", 8)

    assert len(channels[0].sent) == 1


async def test_a_hand_deleted_post_returns_when_the_renderer_asks() -> None:
    """A starboard entry is a mirror, so it is restored rather than left missing."""
    channels = [FakeChannel(1)]
    reconciler = _reconciler(channels, FakeRenderer(channels, repost_if_deleted=True))
    await reconciler.reconcile("build", "42", 7)
    posted = next(iter(channels[0].messages.values()))
    del channels[0].messages[posted.id]

    await reconciler.reconcile("build", "42", 8)

    assert len(channels[0].sent) == 2
