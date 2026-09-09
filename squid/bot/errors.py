"""The bot's error taxonomy: classifying an exception into a user-facing notice, and the discord.py hooks that do it.

Every surface funnels through `build_error_notice`, so the same exception reads the same way whether
it came from a prefix command, an application command, a component or a `pending=` operation. An
exception that matches no known class becomes a generic notice carrying a correlation reference, and
only those are logged and stored as an error report.
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
from squid.bot.utils.permissions import PermissionNodeRequired
from squid.core.errors import DomainError, JSONValue, SquidError
from squid.core.i18n import localization_for, tr
from squid.diagnostics.application import ErrorReportService
from squid.diagnostics.log_capture import captured
from squid.observability import (
    correlated_log_buffer,
    correlation_id,
    correlation_reference,
    correlation_scope,
    record_current_exception,
    trace_span,
)
from squid.permissions.domain import CATALOGUE
from squid_ui.text import NEUTRAL, localization_scope

logger = logging.getLogger(__name__)

_PRESENTED_ATTRIBUTE = "_squid_error_presented"
type ErrorResponder = Callable[[sl.LayoutNode[sl.ComponentsV2Target]], Awaitable[None]]


def _reports_from(client: object) -> ErrorReportService | None:
    """Read the error report store off a bot, tolerating a client that is not one."""
    return getattr(getattr(client, "services", None), "error_reports", None)


@dataclass(frozen=True, slots=True)
class ErrorNotice:
    """Discord-facing text for an exception, carrying no internals.

    `error_id` is set only for an unclassified failure, which is also the only kind captured as an
    error report.
    """

    title: str
    detail: str
    error_id: str | None = None
    reference: str | None = None
    """The shortened form of `error_id` shown on the card, since the user has to retype it.

    Both index the same report: logs and the stored record carry the full id, while a moderator
    looking one up quotes whatever the card showed.
    """

    def to_node(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
        """Lower to `squid.bot.ui.error_node`; the reference, if any, is already inside `detail`."""
        return error_node(self.title, self.detail)


def _safe_log_context(context: Mapping[str, object] | None) -> dict[str, object]:
    """Diagnostic context with Discord user id keys dropped, at every depth of nested mappings."""

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
    """The interaction's guild locale, else the user's, without touching the database.

    Skips the admin-configured guild override on purpose: this runs on every error, including ones
    from objects that carry no concrete bot, so it stays synchronous and safe with test doubles.
    Successful command responses use `squid.bot.i18n.resolve_locale`, which honours the override.
    """
    if interaction is None:
        return None
    guild_locale = getattr(interaction, "guild_locale", None)
    if guild_locale is not None:
        return str(guild_locale)
    locale = getattr(interaction, "locale", None)
    return str(locale) if locale is not None else None


def _present_missing_nodes(error: PermissionNodeRequired) -> ErrorNotice:
    """Name the nodes a caller is missing, with what each one is for.

    Node names are identifiers and stay untranslated; their catalogue descriptions are translated.
    A node absent from the catalogue is silently omitted.
    """
    described = "\n".join(f"`{name}` — {tr(CATALOGUE[name].description)}" for name in error.nodes if name in CATALOGUE)
    if error.forbidden:
        return ErrorNotice(
            tr("Permission withheld"),
            tr("An administrator has explicitly withheld this from you. Ask them if you think that is a mistake.")
            + f"\n\n{described}",
        )
    lead = (
        tr("You need any one of these permissions to use this command:")
        if error.mode == "any"
        else tr("You need these permissions to use this command:")
    )
    return ErrorNotice(
        tr("Missing permission"),
        f"{lead}\n{described}",
    )


def build_error_notice(error: BaseException, locale: str | None = None) -> ErrorNotice:
    """Classify an exception into Discord-facing text, translated into `locale`.

    Command wrappers are unwrapped first. A `DomainError` supplies its own title, message and
    end-user action; framework check and input failures get a fixed notice each; anything else
    becomes "Something went wrong" with a fresh correlation id in `error_id`, which is what marks it
    for capture. Never raises: an unrecognised exception still yields a notice.
    """
    with localization_scope(localization_for(locale)):
        return _build_error_notice(error)


def _build_error_notice(error: BaseException) -> ErrorNotice:
    error = unwrap_error(error)
    if isinstance(error, ConsentRequiredError):
        # Every Discord path that needs consent asks for it first, so reaching here means one
        # slipped the gate. Name the command that can fix it rather than rendering the API's
        # wording, which tells a Discord user to go and accept a notice somewhere they are not.
        return ErrorNotice(
            tr("Consent required"),
            tr("Run `/account consent` to read the privacy notice and accept it."),
        )
    if isinstance(error, DomainError):
        detail = tr(error.message)
        if error.end_user_action:
            detail = f"{detail} {tr(error.end_user_action)}"
        return ErrorNotice(tr(error.title), detail)
    if isinstance(error, commands.NoPrivateMessage):
        return ErrorNotice(
            tr("Server only"),
            tr("This command cannot be used in a private message."),
        )
    if isinstance(error, (commands.MissingRole, commands.MissingAnyRole, commands.MissingPermissions)):
        return ErrorNotice(
            tr("Missing permission"),
            tr("You do not have permission to use this command."),
        )
    if isinstance(error, commands.NotOwner):
        return ErrorNotice(
            tr("Owner only"),
            tr("Only the bot owner can use this command."),
        )
    if isinstance(error, PermissionNodeRequired):
        return _present_missing_nodes(error)
    if isinstance(error, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
        return ErrorNotice(
            tr("Command on cooldown"),
            tr("Try again in {seconds:.1f} seconds.", seconds=error.retry_after),
        )
    if isinstance(error, commands.MaxConcurrencyReached):
        return ErrorNotice(
            tr("Command already running"),
            tr("Wait for the current operation to finish and try again."),
        )
    if isinstance(error, commands.CheckFailure):
        return ErrorNotice(
            tr("Command unavailable"),
            str(error) or tr("You cannot use this command here."),
        )
    if isinstance(error, commands.UserInputError):
        return ErrorNotice(
            tr("Invalid command input"),
            str(error) or tr("Check the command arguments and try again."),
        )
    if isinstance(error, app_commands.TransformerError):
        return ErrorNotice(
            tr("Invalid command input"),
            tr("One of the command options is invalid."),
        )
    if isinstance(error, app_commands.CheckFailure):
        return ErrorNotice(
            tr("Command unavailable"),
            tr("You cannot use this command here."),
        )

    error_id = correlation_id()
    reference = correlation_reference(error_id)
    return ErrorNotice(
        tr("Something went wrong"),
        tr("An unexpected error occurred. Reference: `{error_id}`", error_id=reference),
        error_id,
        reference,
    )


async def _capture(
    error: BaseException,
    notice: ErrorNotice,
    *,
    surface: str,
    context: Mapping[str, object],
    reports: ErrorReportService | None,
) -> None:
    """Store the failure when this process has a report service; a capture failure is logged, never raised.

    Guarded even though `ErrorReportService.record` already swallows, because the buffer drain and
    the service lookup happen out here and the caller still owes the user a reply.
    """
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
    if is_error_presented(error):
        return

    original = unwrap_error(error)
    notice = build_error_notice(original, locale)
    if notice.error_id is not None:
        application_context = _safe_log_context(original.context) if isinstance(original, SquidError) else None
        safe_context = _safe_log_context(context)
        # Capture before logging, so the stored tail is what the command was doing before it failed
        # rather than an echo of the traceback the report already carries. The context is the
        # redacted one: persisting is more exposing than logging, not less.
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

    async def respond(node: sl.LayoutNode[sl.ComponentsV2Target]) -> None:
        if request.deferred is None:
            await request.respond(node, audience="personal")
        else:
            await request.respond(node)

    return respond


def render_request_error(request: sd.Request[Any], error: Exception) -> sl.LayoutNode[sl.ComponentsV2Target]:
    """Render a failed `pending=` command's notice in the request's locale, replacing its card."""
    return build_error_notice(error, request.localization.locale).to_node()


async def observe_request_error(
    request: sd.Request[Any], error: Exception, delivery: sd.delivery.DeliveryResult | None
) -> None:
    """Record a failed `pending=` command once its notice has replaced the card."""
    await record_operation_error(
        error,
        locale=request.localization.locale,
        result=delivery,
        presented=delivery is not None,
        reports=_reports_from(request.client),
    )


COMMAND_ERRORS = sd.ErrorPolicy(render=render_request_error, observe=observe_request_error)
"""The policy every `pending=` command runs under: the notice replaces the pending card, then is recorded."""


async def handle_context_error[BotT: commands.Bot](
    context: commands.Context[BotT],
    error: BaseException,
) -> None:
    """Present an exception from a prefix or hybrid command, once.

    Answers personally unless a managed defer already fixed the audience. A second call for the same
    exception object does nothing, so both the cog hook and the global hook may call it.
    """
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
    """Present an exception from an application command or UI interaction, once.

    `surface` labels the stored report and the log line. As with `handle_context_error`, a repeat
    call for the same exception object is a no-op.
    """
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
) -> None:
    """Capture an operation failure, marking it presented only when `presented` says its card reached Discord.

    Sends nothing itself: the notice has already been rendered by the delivery machinery. An
    unmarked error is presented again by whichever hook catches it next.
    """
    if is_error_presented(error):
        return
    original = unwrap_error(error)
    notice = build_error_notice(original, locale)
    context = {
        "channel_id": result.message.channel.id if result is not None and result.message is not None else None,
        "message_id": result.message_id if result is not None else None,
    }
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
    """Command tree that wraps every non-autocomplete invocation in a trace span, correlation scope and locale.

    The correlation scope is what lets an error report carry the log lines the command produced
    before it failed, so anything raised outside it is recorded without a log tail.
    """

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
