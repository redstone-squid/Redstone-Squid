"""Edit one search query through filter rows, a filter picker, and a sort picker.

The query string is the single source of truth. Each top-level clause is shown as a
filter row that can be edited or removed, and the "Add filter" and "Sort" panels
open independently, so both can be visible at once. Adding a value keeps its field
open for another filter; users can also return to the field list. Every change is reported to
`on_apply` immediately.

Adding a filter takes two steps: choose a field, then its value. How the value is
chosen depends on the field:

- Fields backed by a named catalogue, such as creators or restrictions, open a
  searchable picker of known values.
- Fields with a short fixed list, such as kind, and yes/no fields show a menu.
- Other fields, such as numbers, dates, and free text, open a form.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, cast, override

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.lookup import PickHandler, RequestAuthorizer, SuggestionSource
from squid.bot.submission.query_clauses import query_leaves, replace_clause
from squid.bot.submission.search_labels import field_label, filter_label, sort_label
from squid.bot.ui import tr
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

# Suggestion sources for fields whose values are names from a catalogue.
VALUE_SOURCES = {
    "creator": "creators",
    "restriction": "approved_restrictions",
    "pattern": "approved_patterns",
    "tag": "approved_showcase_tags",
    "version": "approved_source_versions",
}

# Filter rows shown per page; long queries page through them.
_CLAUSES_PER_PAGE = 2

type GuidedNode = sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]


@dataclass(frozen=True, slots=True)
class SearchSelection:
    query: str
    sort: str


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
    clause_page: int = sl.state(0)
    filter_open: bool = sl.state(False)
    filter_picker: sp.SearchPicker[Suggestion] | None = sl.state(None, opaque=True)
    """The field list, or the named values of `selected` once a field is chosen."""
    selected: FieldDefinition | None = sl.state(None)
    """The field whose value is being chosen in the Add filter panel."""
    sort_picker: sp.SearchPicker[Suggestion] | None = sl.state(None, opaque=True)

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
        self.clause_page = min(self.clause_page, max(0, (len(self.clauses) - 1) // _CLAUSES_PER_PAGE))

    def _replace(self, index: int, replacement: QueryExpression | None) -> None:
        expression = self.parser.parse(self.query).expression
        assert expression is not None
        path, _ = query_leaves(expression)[index]
        self._set_expression(replace_clause(expression, path, replacement))

    def _append(self, addition: QueryExpression) -> None:
        expression = self.parser.parse(self.query).expression
        self._set_expression(
            BooleanExpression(BooleanOperator.AND, (expression, addition)) if expression is not None else addition
        )
        self.clause_page = (len(self.clauses) - 1) // _CLAUSES_PER_PAGE
        if self.filter_picker is not None:
            self.filter_picker.picked = ()

    @override
    def render(self) -> tuple[GuidedNode, ...]:
        nodes: list[GuidedNode] = [self._controls()]
        clauses = self.clauses
        start = self.clause_page * _CLAUSES_PER_PAGE
        for index, item in enumerate(clauses[start : start + _CLAUSES_PER_PAGE], start):
            nodes.append(self._clause_row(index, item))
        if len(clauses) > _CLAUSES_PER_PAGE:
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
                        available=start + _CLAUSES_PER_PAGE < len(clauses),
                    ),
                    key="clause-pages",
                )
            )
        if self.filter_open:
            nodes.extend(self._filter_panel())
        if self.sort_picker is not None:
            nodes.extend((sl.heading(tr(t"guided-search-sort = Sort by")), self.sort_picker))
        return tuple(nodes)

    def _controls(self) -> GuidedNode:
        return sl.action_controls(
            sl.form(
                tr(t"guided-search-edit-query = Type a query"),
                self._query_form(self.query),
                key="advanced",
                on_submit=self._advanced,
            ),
            sl.action_control(
                tr(t"guided-search-close-filters = Close filters")
                if self.filter_open
                else tr(t"guided-search-add-filter = Add filter"),
                self._toggle_filter,
                key="field",
                tone=sl.Tone.SUCCESS if self.filter_open else sl.Tone.NEUTRAL,
            ),
            sl.action_control(
                tr(t"guided-search-sorting = Sort"),
                self._toggle_sort,
                key="sort",
                tone=sl.Tone.SUCCESS if self.sort_picker is not None else sl.Tone.NEUTRAL,
            ),
            *(self.actions() if self.actions is not None else ()),
            key="search-controls",
            display=sl.semantic.ControlDisplay.INDIVIDUAL,
        )

    def _filter_panel(self) -> tuple[GuidedNode, ...]:
        """Render the Add filter panel: the field list, then the chosen field's value step."""
        field = self.selected
        if field is None:
            return (
                sl.heading(tr(t"guided-search-add-filter = Add filter")),
                *((self.filter_picker,) if self.filter_picker is not None else ()),
            )
        name = field_label(field.name)
        heading = sl.heading(tr(t"guided-search-filter-heading = Filter by {name}"))
        change = sl.action_control(
            tr(t"guided-search-change-field = Choose another field"), self._change_field, key="change-field"
        )
        if field.name in VALUE_SOURCES and self.filter_picker is not None:
            return (heading, self.filter_picker, sl.action_controls(change, key="filter-step"))
        values = _menu_values(field)
        if values:
            return (
                heading,
                sl.choices(
                    *(sl.choice(label, key=value) for value, label in values),
                    key="value-choice",
                    selection=sl.controlled((), partial(self._choose_menu_value, field)),
                ),
                sl.action_controls(change, key="filter-step"),
            )
        return (
            heading,
            sl.action_controls(
                sl.form(
                    tr(t"guided-search-selected-field = Set {name} filter"),
                    self._field_form(field),
                    key="value",
                    on_submit=partial(self._add, field),
                ),
                change,
                key="filter-step",
                display=sl.semantic.ControlDisplay.INDIVIDUAL,
            ),
        )

    def _clause_row(self, index: int, item: QueryExpression) -> GuidedNode:
        operand = item.operand if isinstance(item, NotExpression) else item
        negated = isinstance(item, NotExpression)
        return sl.action_controls(
            sl.form(
                filter_label(item),
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

    def _query_form(self, query: str) -> sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]]:
        return sl.forms.FormSpec(
            tr(t"guided-search-query-title = Search query"),
            (
                sl.forms.TextField(
                    key="query",
                    label=tr(t"guided-search-query-label = Words and filters, e.g. door width<=5"),
                    default=query,
                    required=False,
                    maximum=1000,
                ),
            ),
        )

    async def _advanced(self, event: sd.SubmitEvent) -> None:
        self.query = self.parser.parse(str(event.values.get("query") or "")).normalized
        self.clause_page = 0
        self._close_filter()
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
        """Build the form that adds or edits a filter on `field`, prefilled from `existing`."""
        label = field_label(field.name)
        raw = existing.value if existing is not None else None
        value = raw.lower if isinstance(raw, RangeValue) else raw
        default = str(value).lower() if isinstance(value, bool) else str(value) if value is not None else ""
        fields: list[sl.forms.FormField] = []
        # A field without ranges has only one comparison, so it gets no comparison menu.
        if field.supports_range:
            operator = "range" if isinstance(raw, RangeValue) else OPERATORS[existing.operator] if existing else ""
            operators: tuple[tuple[str, sl.TextLike], ...] = (
                ("", tr(t"guided-search-operator-equal = is")),
                ("<", tr(t"guided-search-operator-less = less than")),
                ("<=", tr(t"guided-search-operator-at-most = at most")),
                (">", tr(t"guided-search-operator-greater = more than")),
                (">=", tr(t"guided-search-operator-at-least = at least")),
                ("range", tr(t"guided-search-operator-between = between")),
            )
            fields.append(
                sl.forms.ChoiceField(
                    key="operator",
                    label=tr(t"guided-search-comparison = Comparison"),
                    default=operator,
                    options=tuple(
                        sl.forms.ChoiceOption(str(index), name, op) for index, (op, name) in enumerate(operators)
                    ),
                )
            )
        if field.suggested_values and (not default or default in field.suggested_values):
            fields.append(
                sl.forms.ChoiceField(
                    key="value",
                    label=label,
                    default=default or None,
                    options=tuple(
                        sl.forms.ChoiceOption(item, item.replace("_", " ").capitalize(), item)
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
                    label=tr(t"guided-search-upper = And (only for between)"),
                    default=str(raw.upper) if isinstance(raw, RangeValue) else "",
                    required=False,
                    maximum=200,
                )
            )
        fields.append(
            sl.forms.BoolField(
                key="negated",
                label=tr(t"guided-search-negated = Exclude matches"),
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
                sl.forms.BoolField(
                    key="negated", label=tr(t"guided-search-negated = Exclude matches"), default=negated
                ),
            ),
        )

    def _field_expression(self, field: FieldDefinition, event: sd.SubmitEvent) -> FieldExpression:
        value = FieldRegistry.coerce(field, str(event.values["value"]))
        operator = str(event.values.get("operator") or "")
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
        self._close_filter()
        self.sort_picker = None
        self.clause_page = 0

    def _close_filter(self) -> None:
        self.filter_open = False
        self.filter_picker = None
        self.selected = None

    async def _picker(
        self,
        event: sd.ActionEvent[RedstoneSquid],
        source: str,
        on_pick: PickHandler,
        *,
        key: str,
        label: Callable[[Suggestion], sl.TextLike],
        search_label: sl.TextLike,
        context: Mapping[str, str] | None = None,
    ) -> sp.SearchPicker[Suggestion]:
        """Build an inline picker over a suggestion source; choosing an item calls `on_pick`."""
        request = event.context
        viewer = await request.client.actors.resolve(request)
        source_context = dict(context or {})
        if request.guild is not None:
            source_context["guild_id"] = str(request.guild.id)

        def search(query: str) -> SuggestionSource:
            suggestion = SuggestionRequest(
                source, query=query, context=source_context, viewer=viewer, locale=str(event.locale)
            )
            return SuggestionSource(request.client.services.suggestions, suggestion, RequestAuthorizer(request))

        async def picked(action: sl.ActionEvent, items: tuple[Suggestion, ...]) -> None:
            if items:
                await on_pick(cast(sd.ActionEvent, action), items[0])

        picker = sp.SearchPicker(
            search,
            key=key,
            identity=lambda item: item.value,
            label=label,
            description=lambda item: item.description or "",
            on_pick=picked,
            page_size=10,
            show_heading=False,
            show_query=True,
            search_label=search_label,
        )
        picker.session = sp.BrowseSession(search(""), identity=lambda item: item.value, page_size=10)
        return picker

    async def _toggle_filter(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        if self.filter_open:
            self._close_filter()
            return
        self.filter_picker = await self._fields_picker(event)
        self.filter_open = True

    async def _fields_picker(self, event: sd.ActionEvent[RedstoneSquid]) -> sp.SearchPicker[Suggestion]:
        return await self._picker(
            event,
            "search_fields",
            self._field_picked,
            key="filter-lookup",
            label=lambda item: field_label(item.value),
            search_label=tr(t"guided-search-search-filter = Search fields"),
            context={"scope": self.scope},
        )

    async def _toggle_sort(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        if self.sort_picker is not None:
            self.sort_picker = None
            return
        self.sort_picker = await self._picker(
            event,
            "search_sorts",
            self._sort_picked,
            key="sort-lookup",
            label=lambda item: sort_label(item.value),
            search_label=tr(t"guided-search-search-options = Search sort orders"),
            context={"scope": self.scope},
        )

    async def _field_picked(self, event: sd.ActionEvent, item: Suggestion) -> None:
        field = (await self.service.fields()).resolve(item.value)
        if field is None:
            return
        self.selected = self._field_options(field)
        if field.name in VALUE_SOURCES:

            async def chosen(_event: sd.ActionEvent, value: Suggestion) -> None:
                coerced = FieldRegistry.coerce(field, value.value)
                self._append(FieldExpression(field, ComparisonOperator.EQUAL, coerced, phrase=isinstance(coerced, str)))
                await self._refresh()

            name = field_label(field.name)
            self.filter_picker = await self._picker(
                cast(sd.ActionEvent[RedstoneSquid], event),
                VALUE_SOURCES[field.name],
                chosen,
                key="value-lookup",
                label=lambda value: value.label,
                search_label=tr(t"guided-search-search-values = Search {name}"),
            )
        else:
            self.filter_picker = None

    async def _change_field(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        self.filter_picker = await self._fields_picker(event)
        self.selected = None

    async def _choose_menu_value(self, field: FieldDefinition, event: sl.ChoiceEvent) -> None:
        if len(event.selected) != 1:
            return
        value = FieldRegistry.coerce(field, event.selected[0])
        self._append(FieldExpression(field, ComparisonOperator.EQUAL, value, phrase=isinstance(value, str)))
        await self._refresh()

    async def _sort_picked(self, _event: sd.ActionEvent, item: Suggestion) -> None:
        self.sort = item.value
        # Keep the sort panel open with nothing marked, so another order is one choice away.
        if self.sort_picker is not None:
            self.sort_picker.picked = ()
        await self._refresh()

    async def _add(self, field: FieldDefinition, event: sd.SubmitEvent) -> None:
        value = self._field_expression(field, event)
        addition = NotExpression(value) if event.values.get("negated") else value
        self._append(addition)
        await self._refresh()

    async def _remove(self, index: int, _event: sd.PressEvent) -> None:
        self._replace(index, None)
        await self._refresh()

    async def _refresh(self) -> None:
        await self.on_apply(SearchSelection(self.query, self.sort))


def _menu_values(field: FieldDefinition) -> tuple[tuple[str, sl.TextLike], ...]:
    """Return (value, label) pairs for a field chosen from a short menu, or nothing for other fields."""
    if field.value_type is FieldType.BOOLEAN:
        return (("true", tr(t"guided-search-yes = Yes")), ("false", tr(t"guided-search-no = No")))
    if field.suggested_values:
        return tuple((item, item.replace("_", " ").capitalize()) for item in field.suggested_values)
    return ()
