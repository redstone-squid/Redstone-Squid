"""Shared Discord error presentation and framework hooks."""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Self, override
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands

from squid.bot.utils.components import StaticLayout, edit_layout, error_layout, no_mentions
from squid.core.errors import DomainError, SquidError

logger = logging.getLogger(__name__)

_PRESENTED_ATTRIBUTE = "_squid_error_presented"
type ErrorResponder = Callable[[StaticLayout], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ErrorPresentation:
    """Safe Discord-facing representation of an exception."""

    title: str
    detail: str
    error_id: str | None = None

    def to_layout(self) -> StaticLayout:
        """Build the Components V2 layout for this presentation."""
        return error_layout(self.title, self.detail)


def unwrap_error(error: BaseException) -> BaseException:
    """Unwrap discord.py command invocation wrappers."""
    wrapper_types = (
        commands.CommandInvokeError,
        commands.HybridCommandError,
        app_commands.CommandInvokeError,
    )
    current = error
    while isinstance(current, wrapper_types):
        original = current.original
        if original is current:
            break
        current = original
    return current


def is_error_presented(error: BaseException) -> bool:
    """Return whether this error has already received a Discord response."""
    return bool(getattr(unwrap_error(error), _PRESENTED_ATTRIBUTE, False))


def mark_error_presented(error: BaseException) -> None:
    """Mark an error so another Discord hook does not render it again."""
    setattr(unwrap_error(error), _PRESENTED_ATTRIBUTE, True)


def build_error_presentation(error: BaseException) -> ErrorPresentation:
    """Classify an exception into safe Discord-facing text."""
    error = unwrap_error(error)
    if isinstance(error, DomainError):
        return ErrorPresentation(error.title, error.public_detail())
    if isinstance(error, commands.NoPrivateMessage):
        return ErrorPresentation("Server only", "This command cannot be used in a private message.")
    if isinstance(error, (commands.MissingRole, commands.MissingAnyRole, commands.MissingPermissions)):
        return ErrorPresentation("Missing permission", "You do not have permission to use this command.")
    if isinstance(error, commands.NotOwner):
        return ErrorPresentation("Owner only", "Only the bot owner can use this command.")
    if isinstance(error, commands.CommandOnCooldown):
        return ErrorPresentation("Command on cooldown", f"Try again in {error.retry_after:.1f} seconds.")
    if isinstance(error, app_commands.CommandOnCooldown):
        return ErrorPresentation("Command on cooldown", f"Try again in {error.retry_after:.1f} seconds.")
    if isinstance(error, commands.MaxConcurrencyReached):
        return ErrorPresentation("Command already running", "Wait for the current operation to finish and try again.")
    if isinstance(error, commands.CheckFailure):
        return ErrorPresentation("Command unavailable", str(error) or "You cannot use this command here.")
    if isinstance(error, commands.UserInputError):
        return ErrorPresentation("Invalid command input", str(error) or "Check the command arguments and try again.")
    if isinstance(error, app_commands.TransformerError):
        return ErrorPresentation("Invalid command input", "One of the command options is invalid.")
    if isinstance(error, app_commands.CheckFailure):
        return ErrorPresentation("Command unavailable", "You cannot use this command here.")

    error_id = uuid4().hex[:12]
    return ErrorPresentation(
        "Something went wrong",
        f"An unexpected error occurred. Reference: `{error_id}`",
        error_id,
    )


async def _handle_discord_error(
    error: BaseException,
    responder: ErrorResponder,
    *,
    surface: str,
    context: Mapping[str, object] | None = None,
) -> None:
    if is_error_presented(error):
        return

    original = unwrap_error(error)
    presentation = build_error_presentation(original)
    if presentation.error_id is not None:
        application_context = original.context if isinstance(original, SquidError) else None
        logger.error(
            "Discord failure [error_id=%s surface=%s context=%r application_context=%r]",
            presentation.error_id,
            surface,
            dict(context or {}),
            application_context,
            exc_info=original,
        )

    try:
        await responder(presentation.to_layout())
    except discord.HTTPException:
        logger.exception(
            "Failed to send Discord error response [error_id=%s surface=%s]",
            presentation.error_id,
            surface,
        )
    finally:
        mark_error_presented(original)


async def handle_context_error[BotT: commands.Bot](
    context: commands.Context[BotT],
    error: BaseException,
) -> None:
    """Handle an exception raised by a prefix or hybrid command."""

    async def respond(layout: StaticLayout) -> None:
        await context.send(
            view=layout,
            ephemeral=context.interaction is not None,
            allowed_mentions=no_mentions(),
        )

    command_name = context.command.qualified_name if context.command is not None else None
    await _handle_discord_error(
        error,
        respond,
        surface="command",
        context={
            "command": command_name,
            "user_id": context.author.id,
            "guild_id": context.guild.id if context.guild is not None else None,
            "channel_id": context.channel.id,
        },
    )


async def handle_interaction_error(
    interaction: discord.Interaction[discord.Client],
    error: BaseException,
    *,
    surface: str,
) -> None:
    """Handle an exception raised by an application command or UI interaction."""

    async def respond(layout: StaticLayout) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(view=layout, ephemeral=True, allowed_mentions=no_mentions())
        else:
            await interaction.response.send_message(view=layout, ephemeral=True, allowed_mentions=no_mentions())

    command = interaction.command
    await _handle_discord_error(
        error,
        respond,
        surface=surface,
        context={
            "command": command.name if command is not None else None,
            "user_id": interaction.user.id,
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
        },
    )


async def handle_message_error(
    message: discord.Message,
    error: BaseException,
) -> None:
    """Render an exception into an existing progress message."""

    async def respond(layout: StaticLayout) -> None:
        await edit_layout(message, layout, allowed_mentions=no_mentions())

    await _handle_discord_error(
        error,
        respond,
        surface="running_message",
        context={"channel_id": message.channel.id, "message_id": message.id},
    )


class SquidCommandTree[ClientT: discord.Client](app_commands.CommandTree[ClientT]):
    """Application command tree with centralized error handling."""

    @override
    async def on_error(
        self,
        interaction: discord.Interaction[ClientT],
        error: app_commands.AppCommandError,
        /,
    ) -> None:
        await handle_interaction_error(interaction, error, surface="application_command")


class ErrorHandledView(discord.ui.View):
    """Discord view that delegates callback failures to the shared handler."""

    @override
    async def on_error[ClientT: discord.Client](
        self,
        interaction: discord.Interaction[ClientT],
        error: Exception,
        item: discord.ui.Item[Self],
        /,
    ) -> None:
        await handle_interaction_error(interaction, error, surface=f"view:{type(item).__name__}")


class ErrorHandledLayoutView(discord.ui.LayoutView):
    """Components V2 view that delegates callback failures to the shared handler."""

    @override
    async def on_error[ClientT: discord.Client](
        self,
        interaction: discord.Interaction[ClientT],
        error: Exception,
        item: discord.ui.Item[Self],
        /,
    ) -> None:
        await handle_interaction_error(interaction, error, surface=f"view:{type(item).__name__}")


class ErrorHandledModal(discord.ui.Modal):
    """Discord modal that delegates submission failures to the shared handler."""

    @override
    async def on_error[ClientT: discord.Client](
        self,
        interaction: discord.Interaction[ClientT],
        error: Exception,
        /,
    ) -> None:
        await handle_interaction_error(interaction, error, surface="modal")
