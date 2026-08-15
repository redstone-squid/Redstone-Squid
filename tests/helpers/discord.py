"""Small typed harnesses for Discord boundary tests."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, cast
from unittest.mock import AsyncMock

import discord

if TYPE_CHECKING:
    import squid.bot.app


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


def make_message(*, channel_id: int = 2, message_id: int = 3, components_v2: bool = False) -> MessageHarness:
    """Create the minimal message contract used by shared error handling."""
    edit = AsyncMock()
    message = cast(
        discord.Message,
        SimpleNamespace(
            edit=edit,
            channel=SimpleNamespace(id=channel_id),
            id=message_id,
            flags=SimpleNamespace(components_v2=components_v2),
        ),
    )
    return MessageHarness(message, edit)


def make_reaction_payload(
    *,
    message_id: int = 10,
    channel_id: int = 20,
    guild_id: int | None = 30,
    user_id: int = 40,
    emoji: str = "⭐",
    event_type: Literal["REACTION_ADD", "REACTION_REMOVE"] = "REACTION_ADD",
) -> discord.RawReactionActionEvent:
    """Build the real gateway payload rather than a look-alike namespace.

    The router shards on `message_id` and stringifies `emoji`; a duck-typed stand-in
    would keep passing if discord.py renamed or retyped either one.
    """
    data: dict[str, Any] = {
        "message_id": message_id,
        "channel_id": channel_id,
        "user_id": user_id,
        # ReactionType.normal. Burst reactions arrive as the same event and the router
        # does not branch on the distinction.
        "type": 0,
    }
    if guild_id is not None:
        data["guild_id"] = guild_id
    return discord.RawReactionActionEvent(cast(Any, data), discord.PartialEmoji(name=emoji), event_type)


@dataclass(frozen=True, slots=True)
class ReactionBotHarness:
    """A bot stand-in exposing only what reaction dispatch calls, plus its observable fetch."""

    bot: "squid.bot.app.RedstoneSquid"
    get_or_fetch_message: AsyncMock


def make_reaction_bot(
    *, guild: discord.Guild | None = None, message: discord.Message | None = None
) -> ReactionBotHarness:
    """Create the two-method bot surface `ReactionRouter` and `ReactionEvent` depend on.

    Keeping the cast here rather than at each call site means the tests state what they
    need from the bot once, instead of repeating an `arg-type` suppression per test.
    """
    get_or_fetch_message = AsyncMock(return_value=message)
    bot = cast(
        "squid.bot.app.RedstoneSquid",
        SimpleNamespace(get_guild=lambda guild_id: guild, get_or_fetch_message=get_or_fetch_message),
    )
    return ReactionBotHarness(bot, get_or_fetch_message)
