"""Discord autocomplete bound to the shared suggestion registry.

Autocomplete is unlike every other command path, and this module exists to handle the differences
in one place rather than in sixty callbacks:

- Discord discards a response after three seconds and there is no deferral, so a slow source must
  degrade to an empty dropdown rather than an error.
- discord.py does not run a command's checks before its autocomplete callback ("Parent checks are
  ignored within an autocomplete"), so a command gated on a permission node has an ungated
  autocomplete unless the source itself is gated.
- `SquidCommandTree._call` short-circuits autocomplete interactions before error handling, so
  anything raised here would surface as a silent Discord-side failure with no log line.
"""

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import anyio
import discord
from discord import app_commands

from squid.bot.utils.permissions import allows
from squid.observability import trace_span
from squid.suggestions.application import SuggestionSource
from squid.suggestions.domain import (
    MAX_SUGGESTIONS,
    Suggestion,
    SuggestionRequest,
    SuggestionViewer,
    ValueType,
    Visibility,
)

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

RESPONSE_BUDGET_SECONDS = 1.2
"""Well inside Discord's three-second autocomplete window, leaving room for the round trip."""

CHOICE_NAME_LIMIT = 100
"""Discord's cap on a choice's display name."""

type ContextResolver = Callable[[discord.Interaction[Any]], Mapping[str, str]]
type AutocompleteCallback = Callable[
    [discord.Interaction[Any], str],
    Any,
]


def guild_context(interaction: discord.Interaction[Any]) -> Mapping[str, str]:
    """Scope a source to the guild the command was run in."""
    return {} if interaction.guild_id is None else {"guild_id": str(interaction.guild_id)}


def suggests(
    source: str,
    *,
    context: ContextResolver | None = None,
    multi: bool = False,
) -> AutocompleteCallback:
    """Build an autocomplete callback answering from a registered suggestion source.

    `multi` completes one entry of a separator-joined list, keeping everything already typed. It is
    opt-in per parameter rather than implied by the source, because the same taxonomy backs both
    single-valued parameters and list-valued ones.
    """

    async def autocomplete(
        interaction: discord.Interaction[Any],
        current: str,
    ) -> list[app_commands.Choice[Any]]:
        with trace_span("suggestions.autocomplete", {"squid.suggestion.source": source}):
            try:
                # `move_on_after` rather than the service's own bound: the budget here is set by
                # Discord's window, and a partial dropdown beats a late one.
                with anyio.move_on_after(RESPONSE_BUDGET_SECONDS):
                    return await _choices(interaction, source, current, context, multi=multi)
            except Exception:
                logger.exception("Autocomplete failed", extra={"source": source, "current": current})
            return []

    # Stamped so the wiring test can read which source a command actually asks for. discord.py
    # validates that the parameter exists; nothing else would catch a source id that does not.
    autocomplete.__squid_source__ = source  # pyrefly: ignore[missing-attribute]
    return autocomplete


def autocompletes(**params: "str | AutocompleteCallback") -> Callable[[Any], Any]:
    """Attach suggestion sources to several parameters of one command.

    Applied above the command decorator, so it receives the built command:

        @autocompletes(build_id="builds", restrictions=suggests("restriction_ids", multi=True))
        @build_group.command(name="view")
        async def view_build(self, ctx, build_id: int) -> None: ...

    A parameter that needs no configuration is named by its source id; anything else passes a
    `suggests(...)` callback directly. discord.py rejects an unknown parameter name or an
    unsupported option type at decoration time, so a mismatch fails at import rather than in
    production.
    """

    def decorator(command: Any) -> Any:
        for parameter, source in params.items():
            callback = suggests(source) if isinstance(source, str) else source
            command.autocomplete(parameter)(callback)
        return command

    return decorator


class _InteractionAuthorizer:
    """Answer permission questions for the user behind an autocomplete interaction."""

    def __init__(self, interaction: "discord.Interaction[squid.bot.app.RedstoneSquid]") -> None:
        self._interaction = interaction

    async def allows(self, node: str) -> bool:
        return await allows(self._interaction, node)


async def _choices(
    interaction: discord.Interaction[Any],
    source_id: str,
    current: str,
    context: ContextResolver | None,
    *,
    multi: bool,
) -> list[app_commands.Choice[Any]]:
    service = interaction.client.services.suggestions
    source = service.registry.get(source_id)
    if source is None:
        logger.error("Command references an unregistered suggestion source", extra={"source": source_id})
        return []

    separator = source.multi_value if multi else None
    prefix, query = _split(current, separator)
    result = await service.suggest(
        SuggestionRequest(
            source=source_id,
            query=query,
            limit=MAX_SUGGESTIONS,
            context=dict(context(interaction)) if context is not None else {},
            locale=str(interaction.locale),
            viewer=await _viewer(interaction, source),
        ),
        authorizer=_InteractionAuthorizer(interaction),
    )
    return [_choice(item, source, prefix, separator) for item in result.items]


async def _viewer(
    interaction: discord.Interaction[Any],
    source: SuggestionSource,
) -> SuggestionViewer:
    """Resolve the caller's identity, paying for the account lookup only when a source needs it."""
    if source.visibility is not Visibility.VIEWER_SCOPED:
        return SuggestionViewer(guild_id=interaction.guild_id)
    bot = interaction.client
    account_id = await bot.account_ids.resolve(bot.services.accounts, interaction.user.id)
    return SuggestionViewer(account_id=account_id, guild_id=interaction.guild_id)


def _split(current: str, separator: str | None) -> tuple[str, str]:
    """Split a list-valued input into what is already committed and the entry being typed."""
    if separator is None or separator not in current:
        return "", current
    prefix, _, tail = current.rpartition(separator)
    return prefix + separator, tail.lstrip()


def _choice(
    item: Suggestion,
    source: SuggestionSource,
    prefix: str,
    separator: str | None,
) -> app_commands.Choice[Any]:
    if separator is not None:
        # A list-valued parameter is a string parameter by construction, whatever the type of the
        # individual entries: the value carries everything already typed alongside this one.
        return app_commands.Choice(name=_name(item, prefix, separator), value=prefix + item.value)
    return app_commands.Choice(
        name=_name(item, prefix, separator),
        value=int(item.value) if source.value_type is ValueType.INTEGER else item.value,
    )


def _name(item: Suggestion, prefix: str, separator: str | None) -> str:
    label = item.label if item.description is None else f"{item.label} — {item.description}"
    if separator is not None and prefix:
        label = f"{prefix}{label}"
    if len(label) <= CHOICE_NAME_LIMIT:
        return label
    # Keep the tail: for a list the entry being completed is at the end, and it is the part the
    # user is choosing between.
    return "…" + label[-(CHOICE_NAME_LIMIT - 1) :]
