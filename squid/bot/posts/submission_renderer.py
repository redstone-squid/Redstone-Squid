"""Minimal source-message status rendered from durable inference and draft state."""

from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

import discord

import squid_ui as sl
from squid.bot.posts.renderer import DesiredPost
from squid.bot.submission.ui.controls import inference_reopen
from squid.bot.ui import render_payload
from squid.posts.domain import ResourceKind

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


class SubmissionStatusRenderer:
    """Keep one source-channel status post current until its inference run expires."""

    resource_kind: ResourceKind = "inference_run"
    repost_if_deleted = False

    def __init__(self, bot: RedstoneSquid) -> None:
        self.bot = bot

    async def desired(self, resource_key: str) -> Sequence[DesiredPost] | None:
        status = await self.bot.services.submission_inference.public_status(UUID(resource_key))
        if status is None:
            return None
        if status.guild_id is None:
            return ()
        lines = [f"Candidate {index + 1}: {state}" for index, state in enumerate(status.candidates)]
        summary = (
            "\n".join(lines)
            if lines
            else ("No build candidates inferred." if status.state == "completed" else f"Inference {status.state}.")
        )
        source = f"https://discord.com/channels/{status.guild_id}/{status.channel_id}/{status.source_message_id}"
        payload = render_payload(
            [
                sl.primitives.Section(
                    (sl.primitives.Text(f"[Submission source]({source})\n{summary}"),),
                    sl.primitives.RoutedButton("Review privately", inference_reopen.id(run_id=resource_key)),
                )
            ]
        )
        return (DesiredPost(status.channel_id, status.guild_id, "submission_status", payload),)

    async def after_send(self, resource_key: str, message: discord.Message) -> None:
        return None
