"""Renderers deciding what each kind of resource shows in Discord."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, final

import discord

from squid.bot.posts.renderer import DesiredPost
from squid.builds.domain import Status
from squid.posts.domain import ResourceKind

if TYPE_CHECKING:
    import squid.bot.app


@final
class BuildCardRenderer[BotT: "squid.bot.app.RedstoneSquid"]:
    """Publish a confirmed build's card to every guild's builds channel.

    Only confirmed builds are published here. A pending build is shown by its review
    session instead, and a denied one is shown nowhere, so both answer with an empty
    set and the reconciler removes whatever is left over.
    """

    resource_kind: ResourceKind = "build"
    repost_if_deleted: bool = False
    """A moderator deleting a build card meant to remove it, not to trigger a repost."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot

    async def desired(self, resource_key: str) -> Sequence[DesiredPost] | None:
        build = await self.bot.services.build_queries.get(int(resource_key))
        if build is None:
            return None
        if build.submission_status != Status.CONFIRMED:
            return ()

        handler = self.bot.for_build(build)
        payload = await handler.render_payload()
        return [
            DesiredPost(
                channel_id=channel.id,
                guild_id=channel.guild.id,
                surface="build_card",
                payload=payload,
            )
            for channel in await handler.get_channels_to_post_to()
        ]

    async def after_send(self, resource_key: str, message: discord.Message) -> None:
        return None
