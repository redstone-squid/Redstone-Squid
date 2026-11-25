"""Search named domain objects in a child screen, keeping identifiers inside the selection."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, override

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
from squid.bot.utils.permissions import allows
from squid.core.actors import ActingContext
from squid.core.suggestions.application import Candidate, SuggestionService, rank
from squid.core.suggestions.application.ports import SuggestionAuthorizer
from squid.core.suggestions.domain import SourceKind, Suggestion, SuggestionRequest
from squid_ui.ext.discord.target_types import ComponentsV2Target

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


type PickHandler = Callable[[sd.ActionEvent, Suggestion], Awaitable[None]]


class RequestAuthorizer:
    def __init__(self, request: sd.Request[Any, RedstoneSquid]) -> None:
        self.request = request

    async def allows(self, node: str) -> bool:
        return await allows(self.request, node)


class SuggestionSource:
    """Page a complete enumerable catalogue, or one explicitly bounded search result."""

    capabilities = sl.sources.SourceCapabilities(backward=True, offsets=True, count=sl.sources.CountPrecision.EXACT)

    def __init__(
        self,
        service: SuggestionService,
        request: SuggestionRequest,
        authorizer: SuggestionAuthorizer,
    ) -> None:
        self.service = service
        self.request = request
        self.authorizer = authorizer
        self.enumerable = service.registry.resolve(request.source).kind is SourceKind.ENUMERABLE
        self._items: tuple[Suggestion, ...] | None = None

    async def fetch(self, position: sl.sources.Position, fetch_size: int) -> sl.sources.Batch[Suggestion]:
        if self._items is None:
            request = replace(self.request, limit=0, query="") if self.enumerable else self.request
            result = await self.service.suggest(
                request,
                authorizer=self.authorizer,
                raise_on_failure=True,
                context=ActingContext.for_actor(request.viewer),
            )
            self._items = (
                rank(self.request.query, (Candidate(item) for item in result.items), limit=len(result.items))
                if self.enumerable
                else result.items
            )
        return await sl.sources.list_source(self._items).fetch(position, fetch_size)


class LookupScreen(sd.Screen[Suggestion]):
    policy = sd.ResponseSpec(audience="personal", timeout=300, session=sd.SessionSpec("domain-lookup"))

    def __init__(
        self,
        title: sl.TextLike,
        service: SuggestionService,
        request: SuggestionRequest,
        authorizer: SuggestionAuthorizer,
        *,
        confirm: sl.TextLike,
    ) -> None:
        self.title = title
        self.confirm = confirm
        self.bounded = service.registry.resolve(request.source).kind is not SourceKind.ENUMERABLE

        def search(query: str) -> SuggestionSource:
            return SuggestionSource(service, replace(request, query=query), authorizer)

        self.picker = sp.SearchPicker(
            search,
            key="lookup",
            identity=lambda item: item.value,
            label=lambda item: item.label,
            description=lambda item: item.description or f"{item.kind} · {item.value[:8]}",
            on_pick=self._picked,
            fetch_size=25,
            hide_search_when_complete=not self.bounded,
        )

    @override
    async def on_load(self) -> None:
        self.picker.session = sp.BrowseSession(self.picker.search(""), identity=lambda item: item.value, fetch_size=25)

    async def _picked(self, _event: sl.ActionEvent[Any], _items: tuple[Suggestion, ...]) -> None:
        pass

    async def _confirm(self, event: sd.PressEvent) -> None:
        if self.picker.picked:
            self.complete(event, self.picker.picked[0])

    async def _cancel(self, event: sd.PressEvent) -> None:
        self.cancel(event)

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        help_text = (
            (sl.note(tr(t"lookup-bounded = Up to 25 matches are shown. Search by name to narrow the results.")),)
            if self.bounded
            else ()
        )
        return (
            sl.heading(self.title),
            *help_text,
            self.picker,
            sl.action_controls(
                sl.action_control(self.confirm, self._confirm, key="confirm", available=bool(self.picker.picked)),
                sl.action_control(tr(t"lookup-cancel = Cancel"), self._cancel, key="cancel"),
                key="lookup-actions",
            ),
        )


async def pick(
    event: sd.ActionEvent[RedstoneSquid],
    source: str,
    title: sl.TextLike,
    on_pick: PickHandler,
    *,
    confirm: sl.TextLike | None = None,
    context: Mapping[str, str] | None = None,
) -> None:
    """Return a selected object through the parent screen's current action context."""
    request = event.context
    assert request.root is not None
    parent = request.root.component
    assert isinstance(parent, sd.Screen)
    viewer = await request.client.actors.resolve(request)
    source_context = dict(context or {})
    if request.guild is not None:
        source_context["guild_id"] = str(request.guild.id)
    child = LookupScreen(
        title,
        request.client.services.suggestions,
        SuggestionRequest(source, context=source_context, viewer=viewer, locale=str(event.locale)),
        RequestAuthorizer(request),
        confirm=confirm or tr(t"lookup-use-selection = Use selection"),
    )

    async def selected(result: sd.ScreenResultEvent[Suggestion]) -> None:
        await on_pick(result, result.value)

    await parent.open_child(event, child, on_result=selected)
