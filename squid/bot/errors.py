"""Shared Discord error presentation and framework hooks."""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Self, cast, override

import discord
from discord import app_commands
from discord.ext import commands

from squid.bot.utils.components import StaticLayout, edit_layout, error_layout, no_mentions
from squid.bot.utils.permissions import PermissionNodeRequired
from squid.core.errors import DomainError, JSONValue, SquidError
from squid.core.i18n import _, translate
from squid.diagnostics.application import ErrorReportService
from squid.observability import (
    correlated_log_buffer,
    correlation_id,
    correlation_reference,
    correlation_scope,
    record_current_exception,
    trace_span,
)
from squid.permissions.domain import CATALOGUE

logger = logging.getLogger(__name__)

_PRESENTED_ATTRIBUTE = "_squid_error_presented"
type ErrorResponder = Callable[[StaticLayout], Awaitable[None]]

_reporter: ErrorReportService | None = None


def set_error_reporter(service: ErrorReportService | None) -> None:
    """Register the process's error report store for surfaces that carry no client reference.

    A command and an interaction both reach the bot through their own arguments, so those
    handlers resolve the service themselves. A progress message does not: `RunningMessage` is
    built from a `Messageable` or a `Webhook`, neither of which exposes the client. There is one
    bot per process, so registering it here is more honest than threading a service through every
    UI helper that might one day fail.
    """
    global _reporter
    _reporter = service


def _reports_from(client: object) -> ErrorReportService | None:
    """Read the error report store off a bot, tolerating a client that is not one."""
    return getattr(getattr(client, "services", None), "error_reports", None)


@dataclass(frozen=True, slots=True)
class ErrorPresentation:
    """Safe Discord-facing representation of an exception."""

    title: str
    detail: str
    error_id: str | None = None
    reference: str | None = None
    """The shortened form of `error_id` shown to the user, who has to retype it.

    Both are kept because they index the same report: logs and the stored record carry the full
    ID, while a moderator looking one up will be quoting whatever the card showed.
    """

    def to_layout(self) -> StaticLayout:
        """Build the Components V2 layout for this presentation."""
        return error_layout(self.title, self.detail)


def _safe_log_context(context: Mapping[str, object] | None) -> dict[str, object]:
    """Return diagnostic context without stable Discord account identifiers."""

    def sanitize(value: object) -> object:
        if isinstance(value, Mapping):
            return {
                key: sanitize(item)
                for key, item in value.items()
                if isinstance(key, str) and not _is_discord_user_id_key(key)
            }
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, tuple):
            return tuple(sanitize(item) for item in value)
        return value

    sanitized = sanitize(context or {})
    assert isinstance(sanitized, dict)
    return sanitized


def _is_discord_user_id_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized in {"discord_id", "discord_user_id", "user_id"} or normalized.endswith("_discord_id")


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


def _presentation_locale(interaction: discord.Interaction[Any] | None) -> str | None:
    """Best-effort Discord-native locale for error presentation.

    Deliberately skips the admin-configured guild override (which requires a
    database lookup): this runs on every error, including ones raised from
    generic views/modals that don't carry a concrete bot type, so it stays
    synchronous-cheap and safe to call with test doubles. Regular command
    responses use the fully accurate `squid.bot.i18n.resolve_locale` instead.
    """
    if interaction is None:
        return None
    guild_locale = getattr(interaction, "guild_locale", None)
    if guild_locale is not None:
        return str(guild_locale)
    locale = getattr(interaction, "locale", None)
    return str(locale) if locale is not None else None


def _present_missing_nodes(error: PermissionNodeRequired, locale: str | None) -> ErrorPresentation:
    """Name the nodes a caller is missing, with what each one is for.

    Node names are identifiers and stay untranslated; their catalogue
    descriptions are translated, so a refusal reads as "you need this capability"
    rather than as a tier the user has no way to look up.
    """
    described = "\n".join(
        f"`{name}` — {translate(locale, CATALOGUE[name].description)}" for name in error.nodes if name in CATALOGUE
    )
    if error.forbidden:
        return ErrorPresentation(
            translate(locale, _("Permission withheld")),
            translate(
                locale,
                _("An administrator has explicitly withheld this from you. Ask them if you think that is a mistake."),
            )
            + f"\n\n{described}",
        )
    lead = (
        _("You need any one of these permissions to use this command:")
        if error.mode == "any"
        else _("You need these permissions to use this command:")
    )
    return ErrorPresentation(
        translate(locale, _("Missing permission")),
        f"{translate(locale, lead)}\n{described}",
    )


def build_error_presentation(error: BaseException, locale: str | None = None) -> ErrorPresentation:
    """Classify an exception into safe Discord-facing text, translated into `locale`."""
    error = unwrap_error(error)
    if isinstance(error, DomainError):
        return ErrorPresentation(error.localized_title(locale), error.localized_public_detail(locale))
    if isinstance(error, commands.NoPrivateMessage):
        return ErrorPresentation(
            translate(locale, _("Server only")),
            translate(locale, _("This command cannot be used in a private message.")),
        )
    if isinstance(error, (commands.MissingRole, commands.MissingAnyRole, commands.MissingPermissions)):
        return ErrorPresentation(
            translate(locale, _("Missing permission")),
            translate(locale, _("You do not have permission to use this command.")),
        )
    if isinstance(error, commands.NotOwner):
        return ErrorPresentation(
            translate(locale, _("Owner only")),
            translate(locale, _("Only the bot owner can use this command.")),
        )
    if isinstance(error, PermissionNodeRequired):
        return _present_missing_nodes(error, locale)
    if isinstance(error, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
        return ErrorPresentation(
            translate(locale, _("Command on cooldown")),
            translate(locale, _("Try again in {seconds:.1f} seconds."), seconds=error.retry_after),
        )
    if isinstance(error, commands.MaxConcurrencyReached):
        return ErrorPresentation(
            translate(locale, _("Command already running")),
            translate(locale, _("Wait for the current operation to finish and try again.")),
        )
    if isinstance(error, commands.CheckFailure):
        return ErrorPresentation(
            translate(locale, _("Command unavailable")),
            str(error) or translate(locale, _("You cannot use this command here.")),
        )
    if isinstance(error, commands.UserInputError):
        return ErrorPresentation(
            translate(locale, _("Invalid command input")),
            str(error) or translate(locale, _("Check the command arguments and try again.")),
        )
    if isinstance(error, app_commands.TransformerError):
        return ErrorPresentation(
            translate(locale, _("Invalid command input")),
            translate(locale, _("One of the command options is invalid.")),
        )
    if isinstance(error, app_commands.CheckFailure):
        return ErrorPresentation(
            translate(locale, _("Command unavailable")),
            translate(locale, _("You cannot use this command here.")),
        )

    error_id = correlation_id()
    reference = correlation_reference(error_id)
    return ErrorPresentation(
        translate(locale, _("Something went wrong")),
        translate(locale, _("An unexpected error occurred. Reference: `{error_id}`"), error_id=reference),
        error_id,
        reference,
    )


async def _capture(
    error: BaseException,
    presentation: ErrorPresentation,
    *,
    surface: str,
    context: Mapping[str, object],
    reports: ErrorReportService | None,
) -> None:
    """Store the failure, if this process was wired with somewhere to store it.

    Guarded even though `ErrorReportService.record` already swallows: the buffer drain and the
    service lookup happen out here, and this runs inside a handler that owes the user a reply.
    Losing the diagnostic is survivable; losing the reply is a command that silently does nothing.
    """
    if reports is None or presentation.error_id is None or presentation.reference is None:
        return
    command = context.get("command")
    try:
        buffer = correlated_log_buffer()
        await reports.record(
            error,
            correlation_id=presentation.error_id,
            reference=presentation.reference,
            surface=surface,
            origin=command if isinstance(command, str) else None,
            context=cast(Mapping[str, JSONValue], context),
            log_tail=buffer.drain(presentation.error_id) if buffer is not None else (),
        )
    except Exception:
        logger.exception("Could not capture a Discord failure [error_id=%s]", presentation.error_id)


async def _handle_discord_error(
    error: BaseException,
    responder: ErrorResponder,
    *,
    surface: str,
    context: Mapping[str, object] | None = None,
    locale: str | None = None,
    reports: ErrorReportService | None = None,
) -> None:
    if is_error_presented(error):
        return

    original = unwrap_error(error)
    presentation = build_error_presentation(original, locale)
    if presentation.error_id is not None:
        application_context = _safe_log_context(original.context) if isinstance(original, SquidError) else None
        safe_context = _safe_log_context(context)
        # Capture before logging, so the stored tail is what the command was doing before it
        # failed rather than an echo of the traceback the report already carries. The context is
        # the redacted one: persisting is more exposing than logging, not less.
        await _capture(
            original,
            presentation,
            surface=surface,
            context={**safe_context, "application_context": application_context},
            reports=reports if reports is not None else _reporter,
        )
        # Both widths are logged: a backend that cannot do prefix queries still resolves whichever
        # one the reporter quoted by exact match.
        logger.error(
            "Discord failure [error_id=%s error_ref=%s surface=%s context=%r application_context=%r]",
            presentation.error_id,
            presentation.reference,
            surface,
            _safe_log_context(context),
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
            "guild_id": context.guild.id if context.guild is not None else None,
            "channel_id": context.channel.id,
        },
        locale=_presentation_locale(context.interaction),
        reports=_reports_from(context.bot),
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
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
        },
        locale=_presentation_locale(interaction),
        reports=_reports_from(interaction.client),
    )


async def handle_message_error(
    message: discord.Message,
    error: BaseException,
    *,
    locale: str | None = None,
    reports: ErrorReportService | None = None,
) -> None:
    """Render an exception into an existing progress message.

    `locale` should be the locale the message was originally sent in (e.g.
    `RunningMessage.locale`), since a bare message carries no locale info of
    its own.
    """

    async def respond(layout: StaticLayout) -> None:
        await edit_layout(message, layout, allowed_mentions=no_mentions())

    await _handle_discord_error(
        error,
        respond,
        surface="running_message",
        context={"channel_id": message.channel.id, "message_id": message.id},
        locale=locale,
        reports=reports,
    )


class SquidCommandTree[ClientT: discord.Client](app_commands.CommandTree[ClientT]):
    """Application command tree with centralized error handling."""

    @override
    async def _call(self, interaction: discord.Interaction[ClientT]) -> None:
        if interaction.type is discord.InteractionType.autocomplete:
            await super()._call(interaction)  # pyright: ignore[reportPrivateUsage]
            return

        command_name = _interaction_command_name(interaction.data)
        attributes: dict[str, str | int] = {
            "squid.command.name": command_name,
            "squid.surface": "application_command",
        }
        if interaction.guild_id is not None:
            attributes["squid.guild.id"] = interaction.guild_id
        if interaction.channel_id is not None:
            attributes["squid.channel.id"] = interaction.channel_id
        # The correlation scope opens inside the span so it adopts the trace id when one exists.
        # Binding here rather than at presentation time is what lets an error report carry the log
        # lines the command produced before it failed.
        with trace_span(f"discord.command {command_name}", attributes) as span, correlation_scope():
            await super()._call(interaction)  # pyright: ignore[reportPrivateUsage]
            if interaction.command_failed:
                span.set_error()

    @override
    async def on_error(
        self,
        interaction: discord.Interaction[ClientT],
        error: app_commands.AppCommandError,
        /,
    ) -> None:
        record_current_exception(error)
        await handle_interaction_error(interaction, error, surface="application_command")


def _interaction_command_name(data: Mapping[str, Any] | None) -> str:
    """Read a qualified command name from Discord's nested interaction payload."""
    names: list[str] = []
    current = data
    while current is not None:
        name = current.get("name")
        if isinstance(name, str):
            names.append(name)
        options = current.get("options")
        if not isinstance(options, list):
            break
        current = next(
            (option for option in options if isinstance(option, Mapping) and option.get("type") in {1, 2}),
            None,
        )
    return " ".join(names) or "unknown"


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


class ExpiringLayoutView(ErrorHandledLayoutView):
    """A finite session view that visibly disables controls when it expires."""

    def __init__(self, *, timeout: float) -> None:
        super().__init__(timeout=timeout)
        self._bound_message: discord.Message | None = None

    def bind_message(self, message: discord.Message) -> None:
        """Bind the response message so timeout state can be rendered visibly."""
        self._bound_message = message

    @override
    async def on_timeout(self) -> None:
        for child in self.walk_children():
            component: discord.ui.Item[Any] = child
            if isinstance(child, discord.ui.DynamicItem):
                component = child.item
            if isinstance(component, discord.ui.Button | discord.ui.Select):
                component.disabled = True
        self.stop()
        if self._bound_message is None:
            return
        try:
            await edit_layout(self._bound_message, self, allowed_mentions=no_mentions())
        except discord.HTTPException:
            logger.debug("Could not disable expired %s", type(self).__name__, exc_info=True)


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
