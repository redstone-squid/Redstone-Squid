"""Retire public inference status posts while keeping submission review private."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord

from squid.bot.posts.renderer import DesiredPost, PostRenderer
from squid.posts.domain import ResourceKind

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


class SubmissionStatusRenderer(PostRenderer):
    """Request no source-channel posts, removing legacy ones on reconciliation.

    Keep this renderer registered so queued updates can remove the bot's old
    status messages. Staff review remains available through `/build drafts`.
    """

    resource_kind: ResourceKind = "inference_run"
    repost_if_deleted = False

    def __init__(self, bot: RedstoneSquid) -> None:
        self.bot = bot

    async def desired(self, resource_key: str) -> Sequence[DesiredPost]:
        return ()

    async def after_send(self, resource_key: str, message: discord.Message) -> None:
        return None
