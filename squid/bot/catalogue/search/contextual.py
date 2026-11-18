"""Hand a Discord editor's search over to slash-command autocomplete and back."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from secrets import token_urlsafe
from time import monotonic
from typing import TYPE_CHECKING, override

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.routes import AppRequest
from squid.bot.ui import text_node, tr
from squid.bot.utils.autocomplete import (
    AUTOCOMPLETE_CHOICE_LIMIT,
    CHOICE_NAME_LIMIT,
    AutocompleteCallback,
    BotInteraction,
    SuggestionChoice,
    suggests,
)
from squid.suggestions.domain import Suggestion, SuggestionRequest
from squid_i18n import discord_text, resolve_text
from squid_ui.ext.discord.target_types import ComponentsV2Target

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid
    from squid.bot.submission.ui.build_edit import BuildEditScreen

_SEARCH_SECONDS = 120
_MAX_SEARCHES = 256
_COMMAND_NAME = "search"
_CHOICE_PREFIX = "~editor:"
_MAX_CHOICES = 4 * AUTOCOMPLETE_CHOICE_LIMIT
_EXPIRED = tr(t"contextual-search-expired = This search ended. Use Search in the editor to start again.")


@dataclass(frozen=True, slots=True)
class _Search:
    parent: BuildEditScreen
    source: str
    title: sl.TextLike
    on_pick: Callable[[sd.ScreenResultEvent[Suggestion]], Awaitable[None]]
    autocomplete: AutocompleteCallback
    expires_at: float
    choices: dict[str, str] = field(default_factory=dict)

    def active(self, now: float) -> bool:
        return now < self.expires_at and self.parent.close_reason is None


class ContextualSearches:
    """Keep the latest editor search for each Discord user and channel."""

    def __init__(self) -> None:
        self._searches: dict[tuple[int, int | None], _Search] = {}
        self._command_mention: str | None = None
        self._mention_expires_at = 0.0

    def active(self, interaction: BotInteraction, *, scoped: bool = False) -> _Search | None:
        if scoped:
            return None
        key = (interaction.user.id, interaction.channel_id)
        search = self._searches.get(key)
        if search is not None and not search.active(monotonic()):
            del self._searches[key]
            return None
        return search

    async def start(
        self,
        request: AppRequest,
        parent: BuildEditScreen,
        source: str,
        title: sl.TextLike,
        on_pick: Callable[[sd.ScreenResultEvent[Suggestion]], Awaitable[None]],
    ) -> None:
        interaction = request.interaction
        assert interaction is not None
        await request.defer("private")
        mention = await self._mention(request.client)
        hint = tr(t"contextual-search-prompt = Use /search here within two minutes. Leave scope empty.")
        content = "\n".join(discord_text(resolve_text(line, request.localization)) for line in (title, hint))
        now = monotonic()
        key = (interaction.user.id, interaction.channel_id)
        self._searches = {identity: search for identity, search in self._searches.items() if search.active(now)}
        self._searches.pop(key, None)
        if len(self._searches) >= _MAX_SEARCHES:
            del self._searches[next(iter(self._searches))]
        search = _Search(parent, source, title, on_pick, suggests(source), now + _SEARCH_SECONDS)
        self._searches[key] = search
        try:
            await request.respond(content if mention is None else f"{content}\n{mention}", audience="personal")
        except BaseException:
            self._discard(interaction, search)
            raise

    async def _mention(self, client: RedstoneSquid) -> str | None:
        if monotonic() >= self._mention_expires_at:
            try:
                commands = await client.tree.fetch_commands()
            except discord.HTTPException:
                return self._command_mention
            self._command_mention = next(
                (
                    command.mention
                    for command in commands
                    if command.name == _COMMAND_NAME and command.type is discord.AppCommandType.chat_input
                ),
                None,
            )
            self._mention_expires_at = monotonic() + 300
        return self._command_mention

    def _discard(self, interaction: BotInteraction, search: _Search) -> None:
        key = (interaction.user.id, interaction.channel_id)
        if self._searches.get(key) is search:
            del self._searches[key]

    async def autocomplete(self, interaction: BotInteraction, current: str, *, scoped: bool) -> list[SuggestionChoice]:
        search = self.active(interaction, scoped=scoped)
        if current.startswith(_CHOICE_PREFIX):
            choice = search.choices.get(current) if search is not None else None
            if choice is None:
                return []
            current = choice
        if search is None:
            return await _query_autocomplete(interaction, current)

        choices = await search.autocomplete(interaction, current)
        if self.active(interaction) is not search:
            return []
        # Carry session identity across the separate autocomplete and command interactions.
        # Short references also fit when the original choice uses Discord's full value length.
        results: list[SuggestionChoice] = []
        for choice in choices:
            token = _CHOICE_PREFIX + token_urlsafe(12)
            search.choices[token] = str(choice.value)
            results.append(discord.app_commands.Choice(name=choice.name, value=token))
        while len(search.choices) > _MAX_CHOICES:
            del search.choices[next(iter(search.choices))]
        return results

    async def handle_command(self, request: AppRequest, query: str, *, scoped: bool) -> bool:
        """Handle an editor search or indicate that the command should search the catalogue."""
        interaction = request.interaction
        assert interaction is not None
        search = self.active(interaction, scoped=scoped)
        if query.startswith(_CHOICE_PREFIX):
            choice = search.choices.get(query) if search is not None else None
            if choice is None:
                await request.respond(text_node(_EXPIRED), audience="personal")
                return True
            query = choice
        if search is None:
            return False
        await self._present(request, search, query)
        return True

    async def _present(self, request: AppRequest, search: _Search, query: str) -> None:
        interaction = request.interaction
        assert interaction is not None
        await request.defer("private")
        result = await request.client.services.suggestions.suggest(
            SuggestionRequest(search.source, query=query, limit=AUTOCOMPLETE_CHOICE_LIMIT, locale=request.locale),
            raise_on_failure=True,
        )
        if self.active(interaction) is not search:
            await request.respond(text_node(_EXPIRED))
            return
        if not result.items:
            await request.respond(text_node(tr(t"contextual-search-empty = No matches. Try another /search query.")))
            return

        async def picked(event: sd.ScreenResultEvent[Suggestion]) -> None:
            if self.active(interaction) is not search:
                await event.notice(_EXPIRED)
                return
            await search.on_pick(event)
            event.transaction.after_commit(lambda _: self._discard(interaction, search))

        async def cancelled(outcome: sd.Cancelled) -> None:
            if outcome.reason is sd.ScreenCancelReason.USER:
                self._discard(interaction, search)

        picker = _SearchResults(search.title, result.items)
        picker.session_key = sd.SessionKey.custom("contextual-search", (interaction.user.id, interaction.channel_id))
        await search.parent.present_child(request, picker, on_result=picked, on_cancel=cancelled)


_query_autocomplete = suggests("search_query")


async def search_autocomplete(interaction: BotInteraction, current: str) -> list[SuggestionChoice]:
    return await interaction.client.contextual_searches.autocomplete(
        interaction, current, scoped=bool(getattr(interaction.namespace, "scope", None))
    )


class _SearchResults(sd.Screen[Suggestion]):
    policy = sd.ResponseSpec(audience="personal", timeout=_SEARCH_SECONDS, session=sd.SessionSpec("contextual-search"))

    def __init__(self, title: sl.TextLike, matches: tuple[Suggestion, ...]) -> None:
        self.title = title
        self.matches = matches

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        return (
            sl.heading(self.title),
            sd.v2.select(
                *(
                    sd.v2.option(match.label[:CHOICE_NAME_LIMIT], str(index))
                    for index, match in enumerate(self.matches)
                ),
                on_select=self._pick,
                key="match",
                placeholder=tr(t"contextual-search-pick = Select a match to add it"),
            ),
            sl.action_controls(
                sl.action_control(tr(t"contextual-search-cancel = Cancel search"), self._cancel, key="cancel"),
                key="search-actions",
            ),
        )

    async def _pick(self, event: sd.SelectionEvent) -> None:
        self.complete(event, self.matches[int(event.values[0])])

    async def _cancel(self, event: sd.PressEvent) -> None:
        self.cancel(event)
