"""Edit one search query through text, clause rows, and inline value pickers."""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, cast, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.lookup import PickHandler, RequestAuthorizer, SuggestionSource
from squid.bot.ui import tr, truncate_display_text
from squid.records.domain import BuildKind
from squid.search.application import SearchService
from squid.search.application.fields import FieldRegistry
from squid.search.application.parser import SearchQueryParser, render_expression
from squid.search.domain.fields import FieldDefinition, FieldType
from squid.search.domain.query import BooleanExpression, BooleanOperator, QueryExpression
from squid.suggestions.domain import Suggestion, SuggestionRequest

VALUE_SOURCES = {
    "creator": "creators",
    "restriction": "approved_restrictions",
    "pattern": "approved_patterns",
    "tag": "approved_showcase_tags",
    "version": "approved_source_versions",
}


@dataclass(frozen=True, slots=True)
class Filter:
    field: str
    operator: str
    value: str

    @property
    def query(self) -> str:
        return f"{self.field}{self.operator or ':'}{json.dumps(self.value, ensure_ascii=False)}"


@dataclass(frozen=True, slots=True)
class SearchSelection:
    query: str
    sort: str


class GuidedSearch(sl.Component[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    query: str = sl.state("")
    sort: str = sl.state("")
    selected: FieldDefinition | None = sl.state(None, persist=False)
    picker: sp.SearchPicker[Suggestion] | None = sl.state(None, opaque=True)
    picking_sort: bool = sl.state(False)
    picker_title: sl.TextLike = sl.state("")
    clause_page: int = sl.state(0)

    def __init__(
        self,
        service: SearchService,
        *,
        scope: str,
        query: str,
        sort: str,
        on_apply: Callable[[SearchSelection], Awaitable[None]],
        on_cancel: Callable[[], Awaitable[None]],
    ) -> None:
        self.service = service
        self.scope = scope
        self.query = query
        self.sort = sort
        self.on_apply = on_apply
        self.on_cancel = on_cancel
        self.parser = SearchQueryParser()

    @override
    async def on_load(self) -> None:
        self.parser = SearchQueryParser(await self.service.fields())

    @property
    def clauses(self) -> tuple[QueryExpression, ...]:
        expression = self.parser.parse(self.query).expression
        if expression is None:
            return ()
        if isinstance(expression, BooleanExpression) and expression.operator is BooleanOperator.AND:
            return expression.operands
        return (expression,)

    def _set_clauses(self, clauses: tuple[QueryExpression, ...]) -> None:
        self.query = " AND ".join(f"({render_expression(item)})" for item in clauses)
        self.query = self.parser.parse(self.query).normalized
        self.clause_page = min(self.clause_page, max(0, (len(clauses) - 1) // 2))

    def _append(self, item: Filter) -> None:
        expression = self.parser.parse(item.query).expression
        assert expression is not None
        self._set_clauses((*self.clauses, expression))
        self.selected = None

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        if self.picker is not None:
            return (
                sl.heading(self.picker_title),
                self.picker,
                sl.action_controls(
                    sl.action_control(
                        tr(t"guided-search-relevance = Use relevance"),
                        self._relevance,
                        key="relevance",
                        available=self.picking_sort,
                    ),
                    sl.action_control(
                        tr(t"guided-search-back-query = Back to query"), self._back_to_query, key="picker-back"
                    ),
                    key="picker-actions",
                ),
            )
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = [
            sl.heading(tr(t"guided-search-title = Search and filters")),
            sl.paragraph(
                self.query
                or (
                    tr(t"guided-search-all-builds = All builds")
                    if self.scope == "builds"
                    else tr(t"guided-search-all-records = All records")
                )
            ),
            sl.note(
                tr(
                    t"guided-search-shared-query-help = Edit the query or build it with filters. Both edit the same search."
                )
            ),
            sl.action_controls(
                sl.form(
                    tr(t"guided-search-edit-query = Edit query"),
                    self._query_form(self.query),
                    key="advanced",
                    on_submit=self._advanced,
                ),
                sl.action_control(tr(t"guided-search-add-filter = Add filter"), self._choose_field, key="field"),
                sl.action_control(tr(t"guided-search-sorting = Sorting"), self._choose_sort, key="sort"),
                sl.action_control(tr(t"guided-search-reset = Reset"), self._reset, key="reset"),
                key="search-controls",
            ),
            sl.note(self.sort or tr(t"guided-search-sort-relevance = Sort: Relevance")),
        ]
        clauses = self.clauses
        start = self.clause_page * 2
        for index, item in enumerate(clauses[start : start + 2], start):
            clause = render_expression(item)
            label = truncate_display_text(clause, 65)
            nodes.append(
                sl.action_controls(
                    sl.form(
                        tr(t"guided-search-edit-filter = Edit {label}"),
                        self._query_form(clause),
                        key=f"edit-{index}",
                        on_submit=partial(self._edit_clause, index),
                    ),
                    sl.action_control(
                        tr(t"guided-search-remove-clause = Remove"),
                        partial(self._remove, index),
                        key=f"remove-{index}",
                    ),
                    key=f"clause-{index}",
                    display=sl.semantic.ControlDisplay.INDIVIDUAL,
                )
            )
        if len(clauses) > 2:
            nodes.append(
                sl.action_controls(
                    sl.action_control(
                        tr(t"guided-search-previous-filters = Previous filters"),
                        partial(self._page, -1),
                        key="previous-clauses",
                        available=self.clause_page > 0,
                    ),
                    sl.action_control(
                        tr(t"guided-search-next-filters = Next filters"),
                        partial(self._page, 1),
                        key="next-clauses",
                        available=start + 2 < len(clauses),
                    ),
                    key="clause-pages",
                )
            )
        if self.selected is not None:
            field = self.selected
            value: sl.forms.FormField
            label = field.name.replace("_", " ").title()
            if field.suggested_values:
                value = sl.forms.ChoiceField(
                    key="value",
                    label=label,
                    options=tuple(
                        sl.forms.ChoiceOption(item, item.replace("_", " ").title(), item)
                        for item in field.suggested_values
                    ),
                )
            elif field.value_type is FieldType.BOOLEAN:
                value = sl.forms.BoolField(key="value", label=label)
            else:
                value = sl.forms.TextField(
                    key="value",
                    label=label,
                    maximum=200,
                    description=tr(
                        t"guided-search-value-help = Enter a value, including units for numeric fields or an ISO date."
                    ),
                )
            if field.name in VALUE_SOURCES:
                nodes.append(
                    sl.action_controls(
                        sl.action_control(
                            tr(t"guided-search-choose-value = Choose named value"),
                            partial(self._choose_value, field),
                            key="choose-value",
                        ),
                        key="value-lookup",
                    )
                )
            operators = (
                (("", "Equals"), ("<", "Less than"), ("<=", "At most"), (">", "Greater than"), (">=", "At least"))
                if field.supports_range
                else (("", "Equals"),)
            )
            nodes.append(
                sl.form(
                    tr(t"guided-search-add-filter-value = Add filter value"),
                    sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]](
                        label,
                        (
                            sl.forms.ChoiceField(
                                key="operator",
                                label=tr(t"guided-search-comparison = Comparison"),
                                default="",
                                options=tuple(
                                    sl.forms.ChoiceOption(str(index), name, operator)
                                    for index, (operator, name) in enumerate(operators)
                                ),
                            ),
                            value,
                        ),
                    ),
                    key="value",
                    on_submit=partial(self._add, field),
                )
            )
        nodes.append(
            sl.action_controls(
                sl.action_control(tr(t"guided-search-close-panel = Close filters"), self._cancel, key="cancel"),
                key="search-actions",
            )
        )
        return tuple(nodes)

    def _query_form(self, query: str) -> sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]]:
        return sl.forms.FormSpec(
            tr(t"guided-search-edit-query = Edit query"),
            (
                sl.forms.TextField(
                    key="query",
                    label=tr(t"guided-search-query-label = Query"),
                    default=query,
                    required=False,
                    maximum=1000,
                ),
            ),
        )

    async def _advanced(self, event: sd.SubmitEvent) -> None:
        self.query = self.parser.parse(str(event.values.get("query") or "")).normalized
        self.clause_page = 0
        self.selected = None
        await self._refresh()

    async def _edit_clause(self, index: int, event: sd.SubmitEvent) -> None:
        expression = self.parser.parse(str(event.values.get("query") or "")).expression
        clauses = self.clauses
        self._set_clauses(clauses[:index] + (() if expression is None else (expression,)) + clauses[index + 1 :])
        await self._refresh()

    async def _page(self, direction: int, _event: sd.PressEvent) -> None:
        self.clause_page += direction

    async def _reset(self, _event: sd.PressEvent) -> None:
        self.query = ""
        self.sort = ""
        self.selected = None
        self.clause_page = 0
        await self._refresh()

    async def _back_to_query(self, _event: sd.PressEvent[RedstoneSquid]) -> None:
        self.picker = None

    async def _pick(
        self,
        event: sd.ActionEvent[RedstoneSquid],
        source: str,
        title: sl.TextLike,
        on_pick: PickHandler,
        *,
        context: Mapping[str, str] | None = None,
    ) -> None:
        request = event.context
        viewer = await request.client.actors.resolve(request)
        source_context = dict(context or {})
        if request.guild is not None:
            source_context["guild_id"] = str(request.guild.id)
        suggestion = SuggestionRequest(source, context=source_context, viewer=viewer, locale=str(event.locale))

        def search(query: str) -> SuggestionSource:
            return SuggestionSource(
                request.client.services.suggestions, replace(suggestion, query=query), RequestAuthorizer(request)
            )

        async def picked(action: sl.ActionEvent, items: tuple[Suggestion, ...]) -> None:
            if items:
                await on_pick(cast(sd.ActionEvent, action), items[0])
                self.picker = None

        self.picking_sort = source == "search_sorts"
        self.picker_title = title
        picker = sp.SearchPicker(
            search,
            key="search-lookup",
            identity=lambda item: item.value,
            label=lambda item: item.label,
            description=lambda item: item.description or "",
            on_pick=picked,
            page_size=10,
        )
        picker.session = sp.BrowseSession(search(""), identity=lambda item: item.value, page_size=10)
        self.picker = picker

    async def _choose_field(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await self._pick(
            event,
            "search_fields",
            tr(t"guided-search-add-filter = Add filter"),
            self._field_picked,
            context={"scope": self.scope},
        )

    async def _field_picked(self, _event: sd.ActionEvent, item: Suggestion) -> None:
        field = (await self.service.fields()).resolve(item.value)
        if field is not None and field.name == "kind":
            field = replace(field, suggested_values=tuple(kind.value for kind in BuildKind))
        elif field is not None and field.name == "status":
            field = replace(
                field,
                suggested_values=("resolved", "unresolved") if self.scope == "records" else ("confirmed", "pending"),
            )
        self.selected = field

    async def _choose_value(self, field: FieldDefinition, event: sd.PressEvent[RedstoneSquid]) -> None:
        async def selected(_event: sd.ActionEvent, item: Suggestion) -> None:
            self._append(Filter(field.name, "", item.value))
            await self._refresh()
            self.selected = None

        await self._pick(event, VALUE_SOURCES[field.name], field.name.replace("_", " ").title(), selected)

    async def _choose_sort(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await self._pick(
            event,
            "search_sorts",
            tr(t"guided-search-sort = Choose sorting"),
            self._sort_picked,
            context={"scope": self.scope},
        )

    async def _sort_picked(self, _event: sd.ActionEvent, item: Suggestion) -> None:
        self.sort = item.value
        await self._refresh()

    async def _relevance(self, _event: sd.PressEvent[RedstoneSquid]) -> None:
        self.sort = ""
        self.picker = None
        await self._refresh()

    async def _add(self, field: FieldDefinition, event: sd.SubmitEvent) -> None:
        raw = str(event.values["value"])
        value = FieldRegistry.coerce(field, raw)
        self._append(
            Filter(
                field.name,
                str(event.values["operator"]),
                str(value).lower() if isinstance(value, bool) else str(value),
            )
        )
        self.selected = None
        await self._refresh()

    async def _remove(self, index: int, _event: sd.PressEvent) -> None:
        self._set_clauses(tuple(item for position, item in enumerate(self.clauses) if position != index))
        await self._refresh()

    async def _refresh(self) -> None:
        await self.on_apply(SearchSelection(self.query, self.sort))

    async def _cancel(self, _event: sd.PressEvent[RedstoneSquid]) -> None:
        await self.on_cancel()
