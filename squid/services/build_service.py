"""Service layer for build-related commands."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

import discord

from squid.bot._types import GuildMessageable
from squid.db.builds import Build
from squid.db.schema import BuildCategory, Status

if TYPE_CHECKING:
    import squid.bot


class BuildCommandService[BotT: "squid.bot.RedstoneSquid"]:
    """Encapsulates build command workflows used by the bot."""

    def __init__(self, bot: BotT):
        self.bot = bot

    def apply_submission_metadata(
        self,
        build: Build,
        *,
        submitter_id: int | None,
        category: BuildCategory | None,
        ai_generated: bool | None,
        submission_status: Status | None,
    ) -> None:
        """Apply standard submission metadata before persistence."""
        if submitter_id is not None:
            build.submitter_id = submitter_id
        if category is not None:
            build.category = category
        if ai_generated is not None:
            build.ai_generated = ai_generated
        if submission_status is not None:
            build.submission_status = submission_status

    async def save_build(self, build: Build) -> None:
        """Persist a build."""
        await build.save()

    async def generate_embed(self, build: Build) -> discord.Embed:
        """Generate a build embed."""
        return await self.bot.for_build(build).generate_embed()

    async def post_for_voting(self, build: Build, *, vote_type: Literal["add", "update"] = "add") -> None:
        """Post a build for voting."""
        await self.bot.for_build(build).post_for_voting(type=vote_type)

    async def update_build_and_messages(self, build: Build) -> None:
        """Persist a build and refresh bot messages."""
        await build.save()
        await self.bot.for_build(build).update_messages()

    async def post_confirmed_build(self, build: Build) -> None:
        """Post a confirmed build to the appropriate discord channels."""
        assert build.id is not None
        if build.submission_status != Status.CONFIRMED:
            msg = "The build must be confirmed to post it."
            raise ValueError(msg)

        build_handler = self.bot.for_build(build)
        em = await build_handler.generate_embed()

        async def _send_msg(channel: GuildMessageable):
            message = await channel.send(content=build.original_link, embed=em)
            await self.bot.db.message.track_message(message, purpose="view_confirmed_build", build_id=build.id)

        await asyncio.gather(*(_send_msg(channel) for channel in await build_handler.get_channels_to_post_to()))
