"""Small typed harnesses for Discord boundary tests."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import discord


@dataclass(frozen=True, slots=True)
class InteractionHarness:
    """An interaction together with its observable response methods."""

    interaction: discord.Interaction[discord.Client]
    send_initial: AsyncMock
    send_followup: AsyncMock


def make_interaction(
    *,
    response_done: bool = False,
    user_id: int = 1,
    guild_id: int | None = None,
    channel_id: int = 2,
) -> InteractionHarness:
    """Create the minimal interaction contract used by shared error handling."""
    send_initial = AsyncMock()
    send_followup = AsyncMock()
    interaction = cast(
        discord.Interaction[discord.Client],
        SimpleNamespace(
            response=SimpleNamespace(is_done=lambda: response_done, send_message=send_initial),
            followup=SimpleNamespace(send=send_followup),
            command=None,
            user=SimpleNamespace(id=user_id),
            guild_id=guild_id,
            channel_id=channel_id,
        ),
    )
    return InteractionHarness(interaction, send_initial, send_followup)


@dataclass(frozen=True, slots=True)
class MessageHarness:
    """A message together with its observable edit method."""

    message: discord.Message
    edit: AsyncMock


def make_message(*, channel_id: int = 2, message_id: int = 3) -> MessageHarness:
    """Create the minimal message contract used by shared error handling."""
    edit = AsyncMock()
    message = cast(
        discord.Message,
        SimpleNamespace(
            edit=edit,
            channel=SimpleNamespace(id=channel_id),
            id=message_id,
        ),
    )
    return MessageHarness(message, edit)
