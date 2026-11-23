"""Build cards and review cards follow their own publication settings."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast, override
from unittest.mock import Mock

import discord
import pytest

from squid.bot.posts.renderers import BuildCardRenderer
from squid.bot.posts.vote_renderer import VoteSessionRenderer
from squid.bot.utils.web import MediaPreviewClient
from squid.core.builds.application import BuildQueryService
from squid.core.builds.domain import BuildStatus, Credit
from squid.core.builds.models import Build, OtherBuild
from squid.core.settings.application import SettingsService
from squid.core.settings.domain import Setting
from squid.core.voting.domain import BuildEditVoteTarget, BuildVoteTarget, VoteKind, VoteStatus
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services
from tests.support.voting import build_snapshot
from tests.unit.bot.posts.test_vote_renderer import PostRecorder, VoteRecorder


@dataclass(frozen=True)
class Guild:
    id: int


class BuildQueries(BuildQueryService):
    def __init__(self, build: Build | None) -> None:
        self.build = build

    @override
    async def get(self, build_id: int) -> Build | None:
        assert build_id == 7
        return self.build


class Settings(SettingsService):
    def __init__(self) -> None:
        self.requested: list[Setting] = []

    @override
    async def get_many(self, server_ids: Iterable[int], setting: Setting) -> Mapping[int, int | None]:
        assert list(server_ids) == [10, 20, 30]
        self.requested.append(setting)
        # One configured channel is absent from the Discord cache; another is unset.
        return {10: 201 if setting == "Vote" else 300, 20: 999, 30: None}


@pytest.mark.parametrize("build_status", [BuildStatus.PENDING, BuildStatus.APPROVED, BuildStatus.REJECTED])
@pytest.mark.parametrize("vote_status", [VoteStatus.OPEN, VoteStatus.CLOSED])
@pytest.mark.parametrize("kind", [VoteKind.BUILD, VoteKind.BUILD_EDIT])
async def test_review_destinations_follow_the_session(
    build_status: BuildStatus, vote_status: VoteStatus, kind: VoteKind
) -> None:
    build = OtherBuild(id=7, submission_status=build_status)
    target = (
        BuildEditVoteTarget(7, 1, {"dimensions": [2, 3, 4]}, (("dimensions", [1, 2, 3], [2, 3, 4]),))
        if kind is VoteKind.BUILD_EDIT
        else BuildVoteTarget(7)
    )
    snapshot = build_snapshot(kind=kind, status=vote_status, target=target)
    settings = Settings()
    channels = {
        201: Mock(spec=discord.TextChannel, id=201, guild=Guild(10)),
        300: Mock(spec=discord.TextChannel, id=300, guild=Guild(10)),
    }
    bot = make_layout_bot(
        guilds=[Guild(10), Guild(20), Guild(30)],
        get_channel=channels.get,
        media_previews=MediaPreviewClient(),
        services=bot_services(
            build_queries=BuildQueries(build),
            settings=settings,
            votes=VoteRecorder(snapshot),
            posts=PostRecorder(snapshot),
        ),
    )

    posts = await VoteSessionRenderer(cast(Any, bot)).desired(str(snapshot.id))

    assert posts is not None
    assert [(post.channel_id, post.guild_id) for post in posts] == (
        [(200, 10), (201, 10)] if vote_status is VoteStatus.OPEN else [(200, 10)]
    )
    assert settings.requested == (["Vote"] if vote_status is VoteStatus.OPEN else [])
    if kind is VoteKind.BUILD_EDIT:
        assert [asset.name for asset in posts[0].payload.assets] == ["build-7-proposed-changes.json"]


@pytest.mark.parametrize("status", [BuildStatus.PENDING, BuildStatus.APPROVED, BuildStatus.REJECTED, None])
async def test_only_confirmed_builds_reach_build_channels(status: BuildStatus | None) -> None:
    build = OtherBuild(id=7, submission_status=status) if status is not None else None
    settings = Settings()
    channels = {
        201: Mock(spec=discord.TextChannel, id=201, guild=Guild(10)),
        300: Mock(spec=discord.TextChannel, id=300, guild=Guild(10)),
    }
    bot = make_layout_bot(
        guilds=[Guild(10), Guild(20), Guild(30)],
        get_channel=channels.get,
        media_previews=MediaPreviewClient(),
        services=bot_services(build_queries=BuildQueries(build), settings=settings),
    )

    posts = await BuildCardRenderer(cast(Any, bot)).desired("7")

    if status is None:
        assert posts is None
    elif status is BuildStatus.APPROVED:
        assert posts is not None
        assert [(post.channel_id, post.guild_id) for post in posts] == [(300, 10)]
    else:
        assert posts == ()
    assert settings.requested == (["Builds"] if status is BuildStatus.APPROVED else [])


@pytest.mark.parametrize("credited", [True, False])
async def test_a_credited_build_post_links_to_its_creators(credited: bool) -> None:
    build = OtherBuild(id=7, submission_status=BuildStatus.APPROVED)
    build.credits = [Credit(name="Notch")] if credited else []
    channels = {300: Mock(spec=discord.TextChannel, id=300, guild=Guild(10))}
    bot = make_layout_bot(
        guilds=[Guild(10), Guild(20), Guild(30)],
        get_channel=channels.get,
        media_previews=MediaPreviewClient(),
        services=bot_services(build_queries=BuildQueries(build), settings=Settings()),
    )

    posts = await BuildCardRenderer(cast(Any, bot)).desired("7")

    assert posts is not None
    view = posts[0].payload.view
    assert view is not None
    custom_ids = {getattr(item, "custom_id", None) for item in view.walk_children()}
    assert ("ui:/builds/7/creators" in custom_ids) is credited
