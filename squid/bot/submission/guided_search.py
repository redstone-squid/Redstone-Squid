"""Edit one search query through text, clause rows, and inline value pickers."""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Literal, cast, override

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
from squid.search.domain.query import (
    BooleanExpression,
    BooleanOperator,
    ComparisonOperator,
    FieldExpression,
    NotExpression,
    QueryExpression,
    RangeValue,
    TextExpression,
)
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


def query_leaves(
    expression: QueryExpression, path: tuple[int, ...] = ()
) -> tuple[tuple[tuple[int, ...], QueryExpression], ...]:
    """Keep each clause's address so edits preserve its surrounding Boolean operators."""
    if isinstance(expression, BooleanExpression):
        return tuple(
            leaf for index, item in enumerate(expression.operands) for leaf in query_leaves(item, (*path, index))
        )
    if isinstance(expression, NotExpression):
        if isinstance(expression.operand, FieldExpression | TextExpression):
            return ((path, expression),)
        return query_leaves(expression.operand, (*path, 0))
    return ((path, expression),)


def replace_clause(
    expression: QueryExpression,
    path: tuple[int, ...],
    replacement: QueryExpression | None,
) -> QueryExpression | None:
    """Replace or remove a leaf, collapsing empty and single-child groups."""
    if not path:
        return replacement
    if isinstance(expression, NotExpression):
        child = replace_clause(expression.operand, path[1:], replacement)
        return NotExpression(child) if child is not None else None
    assert isinstance(expression, BooleanExpression)
    children = tuple(
        child
        for index, item in enumerate(expression.operands)
        if (child := replace_clause(item, path[1:], replacement) if index == path[0] else item) is not None
    )
    if not children:
        return None
    return children[0] if len(children) == 1 else BooleanExpression(expression.operator, children)


OPERATORS = {
    ComparisonOperator.EQUAL: "",
    ComparisonOperator.LESS_THAN: "<",
    ComparisonOperator.LESS_THAN_OR_EQUAL: "<=",
    ComparisonOperator.GREATER_THAN: ">",
    ComparisonOperator.GREATER_THAN_OR_EQUAL: ">=",
}


class GuidedSearch(sl.Component[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    query: str = sl.state("")
    sort: str = sl.state("")
    selected: FieldDefinition | None = sl.state(None, persist=False)
    picker: sp.SearchPicker[Suggestion] | None = sl.state(None, opaque=True)
    panel: Literal["fields", "sort"] | None = sl.state(None)
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
        actions: Callable[[], tuple[sl.semantic.ActionControl[sd.Request[Any, RedstoneSquid]], ...]] | None = None,
    ) -> None:
        self.service = service
        self.scope = scope
        self.query = query
        self.sort = sort
        self.on_apply = on_apply
        self.actions = actions
        self.parser = SearchQueryParser()

    @override
    async def on_load(self) -> None:
        self.parser = SearchQueryParser(await self.service.fields())

    @property
    def clauses(self) -> tuple[QueryExpression, ...]:
        expression = self.parser.parse(self.query).expression
        return tuple(item for _, item in query_leaves(expression)) if expression is not None else ()

    def _set_expression(self, expression: QueryExpression | None) -> None:
        self.query = self.parser.parse(render_expression(expression) if expression is not None else "").normalized
        self.clause_page = min(self.clause_page, max(0, (len(self.clauses) - 1) // 2))

    def _replace(self, index: int, replacement: QueryExpression | None) -> None:
        expression = self.parser.parse(self.query).expression
        assert expression is not None
        path, _ = query_leaves(expression)[index]
        self._set_expression(replace_clause(expression, path, replacement))

    def _append(self, item: Filter) -> None:
        addition = self.parser.parse(item.query).expression
        assert addition is not None
        expression = self.parser.parse(self.query).expression
        self._set_expression(
            BooleanExpression(BooleanOperator.AND, (expression, addition)) if expression is not None else addition
        )
        self.selected = None
        self.panel = None

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        field_label = self.selected.name.replace("_", " ").title() if self.selected is not None else ""
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = [
            sl.action_controls(
                sl.form(
                    tr(t"guided-search-edit-query = Edit query"),
                    self._query_form(self.query),
                    key="advanced",
                    on_submit=self._advanced,
                ),
                sl.action_control(
                    tr(t"guided-search-add-filter = Add filter"),
                    self._choose_field,
                    key="field",
                    tone=sl.Tone.SUCCESS if self.panel == "fields" else sl.Tone.NEUTRAL,
                ),
                sl.action_control(
                    tr(t"guided-search-sorting = Sorting"),
                    self._choose_sort,
                    key="sort",
                    tone=sl.Tone.SUCCESS if self.panel == "sort" else sl.Tone.NEUTRAL,
                ),
                *(self.actions() if self.actions is not None else ()),
                key="search-controls",
                display=sl.semantic.ControlDisplay.INDIVIDUAL,
            ),
        ]
        if self.picker is not None:
            nodes.extend((sl.heading(self.picker_title), self.picker))
            return tuple(nodes)
        clauses = self.clauses
        start = self.clause_page * 2
        for index, item in enumerate(clauses[start : start + 2], start):
            clause = render_expression(item)
            operand = item.operand if isinstance(item, NotExpression) else item
            negated = isinstance(item, NotExpression)
            label = truncate_display_text(clause, 65)
            nodes.append(
                sl.action_controls(
                    sl.form(
                        tr(t"guided-search-edit-filter = Edit {label}"),
                        self._field_form(self._field_options(operand.field), operand, negated=negated)
                        if isinstance(operand, FieldExpression)
                        else self._text_form(operand, negated=negated),
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
            nodes.append(
                sl.form(
                    tr(t"guided-search-selected-field = Filter: {field_label}"),
                    self._field_form(field),
                    key="value",
                    on_submit=partial(self._add, field),
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
        self.picker = None
        self.panel = None
        await self._refresh()

    def _field_options(self, field: FieldDefinition) -> FieldDefinition:
        if field.name == "kind":
            return replace(field, suggested_values=tuple(kind.value for kind in BuildKind))
        if field.name == "status":
            return replace(
                field,
                suggested_values=("resolved", "unresolved") if self.scope == "records" else ("confirmed", "pending"),
            )
        return field

    def _field_form(
        self,
        field: FieldDefinition,
        existing: FieldExpression | None = None,
        *,
        negated: bool = False,
    ) -> sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]]:
        label = field.name.replace("_", " ").title()
        raw = existing.value if existing is not None else None
        value = raw.lower if isinstance(raw, RangeValue) else raw
        default = str(value).lower() if isinstance(value, bool) else str(value) if value is not None else ""
        operator = "range" if isinstance(raw, RangeValue) else OPERATORS[existing.operator] if existing else ""
        operators = (("", "Equals"),)
        if field.supports_range:
            operators += (
                ("<", "Less than"),
                ("<=", "At most"),
                (">", "Greater than"),
                (">=", "At least"),
                ("range", "Between"),
            )
        fields: list[sl.forms.FormField] = [
            sl.forms.ChoiceField(
                key="operator",
                label=tr(t"guided-search-comparison = Comparison"),
                default=operator,
                options=tuple(
                    sl.forms.ChoiceOption(str(index), name, op) for index, (op, name) in enumerate(operators)
                ),
            ),
        ]
        if field.suggested_values and (not default or default in field.suggested_values):
            fields.append(
                sl.forms.ChoiceField(
                    key="value",
                    label=label,
                    default=default or None,
                    options=tuple(
                        sl.forms.ChoiceOption(item, item.replace("_", " ").title(), item)
                        for item in field.suggested_values
                    ),
                )
            )
        elif field.value_type is FieldType.BOOLEAN:
            fields.append(sl.forms.BoolField(key="value", label=label, default=value is True))
        else:
            fields.append(sl.forms.TextField(key="value", label=label, default=default, maximum=200))
        if field.supports_range:
            fields.append(
                sl.forms.TextField(
                    key="upper",
                    label=tr(t"guided-search-upper = Upper value (Between only)"),
                    default=str(raw.upper) if isinstance(raw, RangeValue) else "",
                    required=False,
                    maximum=200,
                )
            )
        fields.append(
            sl.forms.BoolField(
                key="negated",
                label=tr(t"guided-search-negated = Exclude (NOT)"),
                default=negated,
            )
        )
        return sl.forms.FormSpec(label, tuple(fields))

    def _text_form(
        self, item: QueryExpression, *, negated: bool = False
    ) -> sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]]:
        assert isinstance(item, TextExpression)
        return sl.forms.FormSpec(
            tr(t"guided-search-edit-text = Edit keywords"),
            (
                sl.forms.TextField(
                    key="value", label=tr(t"guided-search-keywords = Keywords"), default=item.value, maximum=500
                ),
                sl.forms.BoolField(key="negated", label=tr(t"guided-search-negated = Exclude (NOT)"), default=negated),
            ),
        )

    def _field_expression(self, field: FieldDefinition, event: sd.SubmitEvent) -> FieldExpression:
        value = FieldRegistry.coerce(field, str(event.values["value"]))
        operator = str(event.values["operator"])
        if operator == "range":
            upper = FieldRegistry.coerce(field, str(event.values.get("upper") or ""))
            return FieldExpression(field, ComparisonOperator.EQUAL, RangeValue(value, upper))
        comparison = next(comparison for comparison, symbol in OPERATORS.items() if symbol == operator)
        return FieldExpression(field, comparison, value, phrase=isinstance(value, str))

    async def _edit_clause(self, index: int, event: sd.SubmitEvent) -> None:
        item = self.clauses[index]
        item = item.operand if isinstance(item, NotExpression) else item
        replacement = (
            self._field_expression(item.field, event)
            if isinstance(item, FieldExpression)
            else TextExpression(str(event.values["value"]), phrase=True)
        )
        self._replace(index, NotExpression(replacement) if event.values.get("negated") else replacement)
        await self._refresh()

    async def _page(self, direction: int, _event: sd.PressEvent) -> None:
        self.clause_page += direction

    def clear(self) -> None:
        self.query = ""
        self.sort = ""
        self.selected = None
        self.picker = None
        self.panel = None
        self.clause_page = 0

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
                if self.panel == "sort" and self.picker is not None:
                    self.picker.picked = ()
                else:
                    self.picker = None

        self.picker_title = title
        picker = sp.SearchPicker(
            search,
            key="search-lookup",
            identity=lambda item: item.value,
            label=lambda item: item.label,
            description=lambda item: item.description or "",
            on_pick=picked,
            page_size=10,
            show_heading=False,
            show_query=True,
            search_label=tr(t"guided-search-search-filter = Search filter")
            if source == "search_fields"
            else tr(t"guided-search-search-options = Search options"),
        )
        picker.session = sp.BrowseSession(search(""), identity=lambda item: item.value, page_size=10)
        self.picker = picker

    async def _choose_field(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        active = self.panel == "fields"
        self.panel = None if active else "fields"
        self.picker = None
        self.selected = None
        if active:
            return
        await self._pick(
            event,
            "search_fields",
            tr(t"guided-search-add-filter = Add filter"),
            self._field_picked,
            context={"scope": self.scope},
        )

    async def _field_picked(self, _event: sd.ActionEvent, item: Suggestion) -> None:
        field = (await self.service.fields()).resolve(item.value)
        self.selected = self._field_options(field) if field is not None else None

    async def _choose_value(self, field: FieldDefinition, event: sd.PressEvent[RedstoneSquid]) -> None:
        async def selected(_event: sd.ActionEvent, item: Suggestion) -> None:
            self._append(Filter(field.name, "", item.value))
            await self._refresh()
            self.selected = None

        await self._pick(event, VALUE_SOURCES[field.name], field.name.replace("_", " ").title(), selected)

    async def _choose_sort(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        active = self.panel == "sort"
        self.panel = None if active else "sort"
        self.picker = None
        self.selected = None
        if active:
            return
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

    async def _add(self, field: FieldDefinition, event: sd.SubmitEvent) -> None:
        value = self._field_expression(field, event)
        addition = NotExpression(value) if event.values.get("negated") else value
        expression = self.parser.parse(self.query).expression
        self._set_expression(BooleanExpression(BooleanOperator.AND, (expression, addition)) if expression else addition)
        self.selected = None
        self.panel = None
        await self._refresh()

    async def _remove(self, index: int, _event: sd.PressEvent) -> None:
        self._replace(index, None)
        await self._refresh()

    async def _refresh(self) -> None:
        await self.on_apply(SearchSelection(self.query, self.sort))
