"""Adapt registered suggestion sources to Discord autocomplete.

Autocomplete cannot defer its response and does not run the parent command's
checks. This adapter applies a short response budget and passes an authorizer
to the suggestion service. Failures are logged and return an empty dropdown
because autocomplete bypasses the command tree's normal error handler.
"""

import logging
from collections.abc import Callable, Coroutine, Mapping
from typing import TYPE_CHECKING

import anyio
import discord
from discord import app_commands

import squid_ui.ext.discord as sd
from squid.bot.utils.permissions import allows
from squid.core.actors import ActingContext, Actor
from squid.core.observability import trace_span
from squid.core.suggestions.application import SuggestionSource
from squid.core.suggestions.domain import (
    ReplacementSpan,
    Suggestion,
    SuggestionRequest,
    ValueType,
    Visibility,
)

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

RESPONSE_BUDGET_SECONDS = 1.2
"""Well inside Discord's three-second autocomplete window, leaving room for the round trip."""

AUTOCOMPLETE_CHOICE_LIMIT = 25
"""Discord's response ceiling, independent of shared suggestion query policy."""

CHOICE_NAME_LIMIT = 100
"""Discord's cap on a choice's display name."""

type BotInteraction = discord.Interaction[squid.bot.app.RedstoneSquid]
type SuggestionChoice = app_commands.Choice[str | int | float]
type ContextResolver = Callable[[BotInteraction], Mapping[str, str]]
type AutocompleteCallback = Callable[[BotInteraction, str], Coroutine[object, object, list[SuggestionChoice]]]


def guild_context(interaction: BotInteraction) -> Mapping[str, str]:
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
    opt-in per parameter rather than implied by the source, because the same tags backs both
    single-valued parameters and list-valued ones.
    """

    async def autocomplete(
        interaction: BotInteraction,
        current: str,
    ) -> list[SuggestionChoice]:
        with trace_span("suggestions.autocomplete", {"squid.suggestion.source": source}):
            try:
                # Include identity and permission lookups in Discord's response budget.
                with anyio.move_on_after(RESPONSE_BUDGET_SECONDS):
                    return await _choices(interaction, source, current, context, multi=multi)
            except Exception:
                logger.exception("Autocomplete failed", extra={"source": source, "current": current})
            return []

    # Stamped so the wiring test can read which source a command actually asks for. discord.py
    # validates that the parameter exists; nothing else would catch a source id that does not.
    # Both checkers type a function object as FunctionType, which has no room for the attribute.
    autocomplete.__squid_source__ = source  # pyrefly: ignore[missing-attribute]  # pyright: ignore[reportFunctionMemberAccess]
    return autocomplete


class _InteractionAuthorizer:
    """Answer permission questions for the user behind an autocomplete interaction."""

    def __init__(self, interaction: BotInteraction) -> None:
        self._interaction = interaction

    async def allows(self, node: str) -> bool:
        return await allows(await sd.request(self._interaction), node)


async def _choices(
    interaction: BotInteraction,
    source_id: str,
    current: str,
    context: ContextResolver | None,
    *,
    multi: bool,
) -> list[SuggestionChoice]:
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
            limit=AUTOCOMPLETE_CHOICE_LIMIT,
            context=dict(context(interaction)) if context is not None else {},
            locale=str(interaction.locale),
            # Discord provides no caret position; the suggestion service assumes the input end.
            cursor=None,
        ),
        authorizer=_InteractionAuthorizer(interaction),
        context=ActingContext.direct(await _viewer(interaction, source)),
    )
    if result.replacement is not None:
        # A choice replaces the whole option value, so a source that completes part of the input
        # has to be spliced back into the rest of it here.
        return [
            _spliced_choice(item, query, result.replacement, prefix)
            for item in result.items[:AUTOCOMPLETE_CHOICE_LIMIT]
        ]
    return [_choice(item, source, prefix, separator) for item in result.items[:AUTOCOMPLETE_CHOICE_LIMIT]]


def _spliced_choice(
    item: Suggestion,
    query: str,
    span: ReplacementSpan,
    prefix: str,
) -> SuggestionChoice:
    """Rebuild the whole option value with one span replaced."""
    value = prefix + query[: span.start] + item.value + query[span.end :]
    return app_commands.Choice(name=_truncate(value), value=value)


async def _viewer(
    interaction: BotInteraction,
    source: SuggestionSource,
) -> Actor:
    """Resolve the caller's identity, paying for the account lookup only when a source needs it."""
    if source.visibility is not Visibility.VIEWER_SCOPED:
        return Actor(discord_user_id=interaction.user.id, guild_id=interaction.guild_id)
    bot = interaction.client
    account_id = await bot.account_ids.resolve(bot.services.accounts, interaction.user.id)
    return Actor(account_id=account_id, discord_user_id=interaction.user.id, guild_id=interaction.guild_id)


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
) -> SuggestionChoice:
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
    return _truncate(label)


def _truncate(label: str) -> str:
    if len(label) <= CHOICE_NAME_LIMIT:
        return label
    # Keep the tail: whether it is a list entry or a query token, the part being completed is at
    # the end, and it is what the user is choosing between.
    return "…" + label[-(CHOICE_NAME_LIMIT - 1) :]
