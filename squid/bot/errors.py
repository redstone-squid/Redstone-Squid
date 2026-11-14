"""Present Discord errors and retain unexpected failures for diagnosis.

Known domain and command errors become actionable notices. Unexpected exceptions
receive a short reference tied to a stored report and correlated logs, keeping
tracebacks out of the public reply. UI_ERRORS connects this policy to managed UI
requests; the command tree and context hooks cover discord.py error dispatch.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast, override

import discord
from discord import app_commands
from discord.ext import commands

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.errors import ConsentRequiredError
from squid.bot.ui import error_node
from squid.core.errors import DomainError, JSONValue, ServiceUnavailableError, SquidError
from squid.core.i18n import localization_for, tr
from squid.diagnostics.application import ErrorReportService
from squid.diagnostics.log_capture import captured
from squid.observability import (
    TraceSurface,
    correlated_log_buffer,
    correlation_id,
    correlation_reference,
    correlation_scope,
    record_current_exception,
    trace_span,
)
from squid_i18n import NUMBER, Markup, Text, current_localization, md, md_tr, resolve_text
from squid_ui.errors import LayoutSearchExhaustedError, UnsolvableLayoutError
from squid_ui.text import NEUTRAL, TextLike, localization_scope

logger = logging.getLogger(__name__)

_PRESENTED_ATTRIBUTE = "_squid_error_presented"
_CORRELATION_ATTRIBUTE = "_squid_error_correlation_id"
type ErrorResponder = Callable[[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]], Awaitable[None]]


def _reports_from(client: object) -> ErrorReportService | None:
    """Read the error report store off a bot, tolerating a client that is not one."""
    return getattr(getattr(client, "services", None), "error_reports", None)


@dataclass(frozen=True, slots=True)
class ErrorNotice:
    """Safe Discord-facing representation of an exception."""

    title: Text
    detail: Text
    error_id: str | None = None
    reference: str | None = None
    """The shortened form of `error_id` shown to the user, who has to retype it.

    Both are kept because they index the same report: logs and the stored record carry the full
    ID, while a moderator looking one up will be quoting whatever the card showed.
    """

    def to_node(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        """Build the composable Components V2 node for this error."""
        return error_node(self.title, self.detail)


@dataclass(frozen=True, slots=True)
class _NoticeDraft:
    """An ErrorNotice whose text is not yet translated; `build_error_notice` resolves it."""

    title: TextLike
    detail: TextLike
    error_id: str | None = None
    reference: str | None = None


def _sanitize_log_value(value: object) -> object:
    """Recursively remove mapping entries with recognized Discord user-ID keys.

    Values under other keys and free text are retained; this is not general
    identifier detection or text redaction.
    """
    if isinstance(value, Mapping):
        return {
            key: _sanitize_log_value(item)
            for key, item in value.items()
            if isinstance(key, str) and not _is_discord_user_id_key(key)
        }
    if isinstance(value, list):
        return [_sanitize_log_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_log_value(item) for item in value)
    return value


def _safe_log_context(context: Mapping[str, object] | None) -> dict[str, object]:
    """Copy diagnostic context while removing recognized Discord user-ID fields."""
    sanitized = _sanitize_log_value(context or {})
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
    """Return whether a Discord error hook has marked the underlying exception as handled."""
    return bool(getattr(unwrap_error(error), _PRESENTED_ATTRIBUTE, False))


def mark_error_presented(error: BaseException) -> None:
    """Mark the underlying exception to prevent another Discord hook from handling it again."""
    setattr(unwrap_error(error), _PRESENTED_ATTRIBUTE, True)


def _presentation_locale(interaction: discord.Interaction[Any] | None) -> str | None:
    """Use Discord's guild or user locale without a database lookup.

    Error handling may run when settings access has failed, so this fallback
    does not consult the configured guild override.
    """
    if interaction is None:
        return None
    guild_locale = getattr(interaction, "guild_locale", None)
    if guild_locale is not None:
        return str(guild_locale)
    locale = getattr(interaction, "locale", None)
    return str(locale) if locale is not None else None


def build_error_notice(error: BaseException, locale: str | None = None) -> ErrorNotice:
    """Classify an exception into safe Discord-facing text, translated into `locale`.

    The locale comes from the interaction rather than the guild's settings, because reading
    settings may be what failed. The text is therefore translated here, not by the render.
    """
    draft = _build_error_notice(error)
    with localization_scope(localization_for(locale)):
        return ErrorNotice(_resolved(draft.title), _resolved(draft.detail), draft.error_id, draft.reference)


def _build_error_notice(error: BaseException) -> _NoticeDraft:
    error = unwrap_error(error)
    if isinstance(error, ConsentRequiredError):
        # Give Discord users an in-bot recovery path when an operation requires consent.
        return _NoticeDraft(
            tr(t"errors-consent-required = Consent required"),
            md_tr(
                t"errors-consent-required-hint = Run `/account consent` to read the privacy notice and accept it.",
            ),
        )
    if isinstance(error, (DomainError, ServiceUnavailableError)):
        return _NoticeDraft(error.title, error.public_text())
    if isinstance(error, commands.NoPrivateMessage):
        return _NoticeDraft(
            tr(t"errors-server-only = Server only"),
            tr(t"errors-context-unavailable = This command cannot be used in a private message."),
        )
    if isinstance(error, (commands.MissingRole, commands.MissingAnyRole, commands.MissingPermissions)):
        return _NoticeDraft(
            tr(t"errors-missing-permission = Missing permission"),
            tr(t"errors-permission-required = You do not have permission to use this command."),
        )
    if isinstance(error, commands.NotOwner):
        return _NoticeDraft(
            tr(t"errors-owner-only = Owner only"),
            tr(t"errors-owner-only-hint = Only the bot owner can use this command."),
        )
    if isinstance(error, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
        seconds = error.retry_after
        return _NoticeDraft(
            tr(t"errors-command-on-cooldown = Command on cooldown"),
            md_tr(
                t"errors-try-again-in-seconds = Try again in {NUMBER(seconds, minimum_fraction_digits=1, maximum_fraction_digits=1)} seconds.",
            ),
        )
    if isinstance(error, commands.MaxConcurrencyReached):
        return _NoticeDraft(
            tr(t"errors-command-already-running = Command already running"),
            tr(t"errors-operation-in-progress = Wait for the current operation to finish and try again."),
        )
    if isinstance(error, commands.CheckFailure):
        return _NoticeDraft(
            tr(t"errors-command-unavailable = Command unavailable"),
            str(error) or tr(t"errors-context-forbidden = You cannot use this command here."),
        )
    if isinstance(error, commands.UserInputError):
        return _NoticeDraft(
            tr(t"errors-invalid-command-input = Invalid command input"),
            str(error) or tr(t"errors-invalid-arguments-hint = Check the command arguments and try again."),
        )
    if isinstance(error, app_commands.TransformerError):
        return _NoticeDraft(
            tr(t"errors-invalid-command-input = Invalid command input"),
            tr(t"errors-one-of-the-command-options-is-invalid = One of the command options is invalid."),
        )
    if isinstance(error, app_commands.CheckFailure):
        return _NoticeDraft(
            tr(t"errors-command-unavailable = Command unavailable"),
            tr(t"errors-context-forbidden = You cannot use this command here."),
        )

    # Rendering and recording are separate hooks and may run outside a shared
    # trace. Keep the displayed reference attached to the underlying exception.
    error_id = getattr(error, _CORRELATION_ATTRIBUTE, None)
    if not isinstance(error_id, str):
        error_id = correlation_id()
        setattr(error, _CORRELATION_ATTRIBUTE, error_id)
    reference = correlation_reference(error_id)
    return _NoticeDraft(
        tr(t"errors-something-went-wrong = Something went wrong"),
        _unexpected_error_detail(reference),
        error_id,
        reference,
    )


def _resolved(text: TextLike) -> Text:
    """Translate `text` now and keep its markup, so the card renders it the way it was authored.

    The result stays `Text` because a Markdown string passed on as plain text would be escaped again.
    """
    document = resolve_text(text, current_localization())
    return md(document.content) if document.markup is Markup.MARKDOWN else Text(document.content)


def _unexpected_error_detail(error_id: str) -> Text:
    return md_tr(
        t"errors-unexpected-reference = An unexpected error occurred. Reference: `{error_id!s}`",
    )


async def _capture(
    error: BaseException,
    notice: ErrorNotice,
    *,
    surface: str,
    context: Mapping[str, object],
    reports: ErrorReportService | None,
) -> None:
    """Record an unexpected failure without letting diagnostic errors prevent the reply."""
    if reports is None or notice.error_id is None or notice.reference is None:
        return
    command = context.get("command")
    try:
        buffer = correlated_log_buffer()
        await reports.record(
            error,
            correlation_id=notice.error_id,
            reference=notice.reference,
            surface=surface,
            origin=command if isinstance(command, str) else None,
            context=cast(Mapping[str, JSONValue], context),
            log_tail=buffer.drain(notice.error_id) if buffer is not None else (),
        )
    except Exception:
        logger.exception("Could not capture a Discord failure [error_id=%s]", notice.error_id)


async def _handle_discord_error(
    error: BaseException,
    responder: ErrorResponder,
    *,
    surface: str,
    context: Mapping[str, object] | None = None,
    locale: str | None = None,
    reports: ErrorReportService | None = None,
) -> None:
    """Present an error once, recording unexpected failures before sending the notice.

    Mark the exception as handled even when the responder raises. Discord HTTP
    failures while sending are logged and suppressed; other response failures
    propagate after the marker is set.
    """
    if is_error_presented(error):
        return

    original = unwrap_error(error)
    notice = build_error_notice(original, locale)
    if isinstance(original, UnsolvableLayoutError | LayoutSearchExhaustedError):
        context = {**(context or {}), "layout": original.context}
    if notice.error_id is not None:
        application_context = _safe_log_context(original.context) if isinstance(original, SquidError) else None
        safe_context = _safe_log_context(context)
        # Capture before logging, so the stored tail is what the command was doing before it
        # failed rather than an echo of the traceback the report already carries. The context is
        # the redacted one: persisting is more exposing than logging, not less.
        await _capture(
            original,
            notice,
            surface=surface,
            context={**safe_context, "application_context": application_context},
            reports=reports,
        )
        # Both widths are logged: a backend that cannot do prefix queries still resolves whichever
        # one the reporter quoted by exact match.
        logger.error(
            "Discord failure [error_id=%s error_ref=%s surface=%s context=%r application_context=%r]",
            notice.error_id,
            notice.reference,
            surface,
            _safe_log_context(context),
            application_context,
            exc_info=original,
            extra=captured(),
        )

    try:
        await responder(notice.to_node())
    except discord.HTTPException:
        logger.exception(
            "Failed to send Discord error response [error_id=%s surface=%s]",
            notice.error_id,
            surface,
        )
    finally:
        mark_error_presented(original)


def _error_responder(request: sd.Request[Any]) -> ErrorResponder:
    """Answer privately unless a managed defer already fixed the audience.

    A public "thinking" placeholder can only be completed publicly; an ephemeral follow-up
    would leave it spinning forever.
    """

    async def respond(node: sl.LayoutNode[sl.ComponentsV2Target, sd.Request]) -> None:
        if request.deferred is None:
            await request.respond(node, audience="personal")
        else:
            await request.respond(node)

    return respond


def render_request_error(
    request: sd.Request[Any], error: Exception
) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
    """Render a failed command or component's notice in the request's locale."""
    return build_error_notice(error, request.localization.locale).to_node()


async def observe_request_error(
    request: sd.Request[Any], error: Exception, delivery: sd.delivery.DeliveryResult | None
) -> None:
    """Record a failed command or component after attempting to present its notice."""
    await record_operation_error(
        error,
        locale=request.localization.locale,
        result=delivery,
        presented=delivery is not None,
        reports=_reports_from(request.client),
        request=request,
    )


UI_ERRORS = sd.ErrorPolicy(render=render_request_error, observe=observe_request_error)


async def handle_context_error[BotT: commands.Bot](
    context: commands.Context[BotT],
    error: BaseException,
) -> None:
    """Handle an exception raised by a prefix or hybrid command."""

    request = await sd.request(context)
    command_name = context.command.qualified_name if context.command is not None else None
    await _handle_discord_error(
        error,
        _error_responder(request),
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

    request = await sd.request(interaction)
    command = interaction.command
    await _handle_discord_error(
        error,
        _error_responder(request),
        surface=surface,
        context={
            "command": command.name if command is not None else None,
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
        },
        locale=_presentation_locale(interaction),
        reports=_reports_from(interaction.client),
    )


async def record_operation_error(
    error: BaseException,
    *,
    locale: str | None,
    result: sd.delivery.DeliveryResult | None,
    presented: bool,
    reports: ErrorReportService | None = None,
    request: sd.Request[Any] | None = None,
) -> None:
    """Capture an operation failure and mark it only when its card reached Discord."""
    if is_error_presented(error):
        return
    original = unwrap_error(error)
    notice = build_error_notice(original, locale)
    context: dict[str, object] = {
        "channel_id": result.message.channel.id if result is not None and result.message is not None else None,
        "message_id": result.message_id if result is not None else None,
    }
    if request is not None:
        command = request.command
        context.update(
            command=command.qualified_name if command is not None else None,
            guild_id=request.guild.id if request.guild is not None else None,
            channel_id=getattr(request.channel, "id", None),
            owner=type(request.owner).__qualname__,
        )
    if isinstance(original, UnsolvableLayoutError | LayoutSearchExhaustedError):
        context = {**(context or {}), "layout": original.context}
    if notice.error_id is not None:
        application_context = _safe_log_context(original.context) if isinstance(original, SquidError) else None
        await _capture(
            original,
            notice,
            surface="command_operation",
            context={**context, "application_context": application_context},
            reports=reports,
        )
        logger.error(
            "Discord failure [error_id=%s error_ref=%s surface=command_operation context=%r application_context=%r]",
            notice.error_id,
            notice.reference,
            context,
            application_context,
            exc_info=original,
            extra=captured(),
        )
    if presented:
        mark_error_presented(original)


class SquidCommandTree[ClientT: discord.Client](app_commands.CommandTree[ClientT]):
    """Application command tree with centralized error handling."""

    @override
    async def _call(self, interaction: discord.Interaction[ClientT]) -> None:
        if interaction.type is discord.InteractionType.autocomplete:
            await super()._call(interaction)  # pyright: ignore[reportPrivateUsage]
            return

        command = interaction.command
        command_name = command.qualified_name if command is not None else "unknown"
        attributes: dict[str, str | int] = {
            "squid.command.name": command_name,
            "squid.surface": TraceSurface.APPLICATION_COMMAND,
        }
        if interaction.guild_id is not None:
            attributes["squid.guild.id"] = interaction.guild_id
        if interaction.channel_id is not None:
            attributes["squid.channel.id"] = interaction.channel_id
        # The correlation scope opens inside the span so it adopts the trace id when one exists.
        # Binding here rather than at notice time is what lets an error report carry the log
        # lines the command produced before it failed.
        with (
            trace_span(f"discord.command {command_name}", attributes) as span,
            correlation_scope(),
        ):
            try:
                localization = (await sd.request(interaction)).localization
            except sd.DiscordUIRuntimeMissing:
                localization = NEUTRAL
            with localization_scope(localization):
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
