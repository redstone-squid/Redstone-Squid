"""Renderers deciding what each kind of resource shows in Discord."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast, final, override

import discord

import squid_ui as sl
from squid.bot._types import GuildMessageable
from squid.bot.posts.renderer import DesiredPost, PostRenderer
from squid.bot.submission.build_card import render_build_card
from squid.bot.ui import render_payload, tr
from squid.core.builds.domain import BuildStatus
from squid.core.posts.domain import ResourceKind

if TYPE_CHECKING:
    import squid.bot.app


@final
class BuildCardRenderer[BotT: squid.bot.app.RedstoneSquid](PostRenderer):
    """Publish a approved build's card to every guild's builds channel.

    Only approved builds are published here. A pending build is shown by its review
    session instead, and a denied one is shown nowhere, so both answer with an empty
    set and the reconciler removes whatever is left over.
    """

    resource_kind: ResourceKind = "build"
    repost_if_deleted: bool = False
    """A moderator deleting a build card meant to remove it, not to trigger a repost."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot

    @override
    async def desired(self, resource_key: str) -> Sequence[DesiredPost] | None:
        build = await self.bot.services.build_queries.get(int(resource_key))
        if build is None:
            return None
        if build.submission_status != BuildStatus.APPROVED:
            return ()

        card = await render_build_card(
            build, self.bot.media_previews, schematic_summaries=self.bot.services.schematic_summaries
        )
        assert build.id is not None
        # Imported here because the catalogue imports squid.bot.app, which imports this module.
        from squid.bot.catalogue.browse import BuildCreatorsScreen

        creators = sl.action_controls(
            sl.routed_action_control(
                tr(t"build-post-creators = Creators"),
                BuildCreatorsScreen.open.custom_id(build_id=build.id),
                key="creators",
            ),
            key="build-post-actions",
        )
        payload = render_payload([card, creators] if build.credits else [card])
        configured = await self.bot.services.settings.get_many((guild.id for guild in self.bot.guilds), "Builds")
        channels = (self.bot.get_channel(channel_id) for channel_id in configured.values() if channel_id is not None)
        return [
            DesiredPost(
                channel_id=channel.id,
                guild_id=cast(GuildMessageable, channel).guild.id,
                surface="build_card",
                payload=payload,
            )
            for channel in channels
            if channel is not None
        ]

    @override
    async def after_send(self, resource_key: str, message: discord.Message) -> None:
        return None
