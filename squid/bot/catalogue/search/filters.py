"""Edit a search query through filter rows and independent filter and sort panels.

Select fields together, then enter their values through a picker, menu, or form
for each field. The batch applies after the last value is entered.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, cast, override

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.catalogue.search.clauses import CatalogueQuery
from squid.bot.catalogue.search.labels import filter_label
from squid.bot.lookup import RequestAuthorizer, SuggestionSource
from squid.bot.ui import tr
from squid.core.actors import ActingContext
from squid.core.search.application import SearchService
from squid.core.search.application.fields import FieldRegistry
from squid.core.search.application.parser import SearchQueryParser
from squid.core.search.domain.fields import FieldDefinition, FieldType
from squid.core.search.domain.query import (
    BooleanOperator,
    ComparisonOperator,
    FieldExpression,
    NotExpression,
    PresenceExpression,
    QueryExpression,
    RangeValue,
    TextExpression,
    combine_clauses,
    query_leaves,
    replace_clause,
)
from squid.core.search.domain.scope import SearchScope
from squid.core.suggestions.domain import Suggestion, SuggestionRequest
from squid_ui.ext.discord.target_types import ComponentsV2Target

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
_PICKER_PAGE_SIZE = 10

type GuidedNode = sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]


@dataclass(frozen=True, slots=True)
class SearchSelection:
    query: CatalogueQuery
    sort: str


OPERATORS = {
    ComparisonOperator.EQUAL: "",
    ComparisonOperator.LESS_THAN: "<",
    ComparisonOperator.LESS_THAN_OR_EQUAL: "<=",
    ComparisonOperator.GREATER_THAN: ">",
    ComparisonOperator.GREATER_THAN_OR_EQUAL: ">=",
}


class GuidedSearch(sl.Component[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    query: CatalogueQuery = sl.state(CatalogueQuery())
    sort: str = sl.state("")
    filter_open: bool = sl.state(False)
    filter_picker: sp.SearchPicker[Suggestion] | None = sl.state(None, opaque=True)
    """The field list, or the named values of `selected` once a field is chosen."""
    selected: FieldDefinition | None = sl.state(None)
    """The field whose value is being chosen in the Add filter panel."""
    pending_fields: tuple[FieldDefinition, ...] = sl.state(())
    pending_clauses: tuple[QueryExpression, ...] = sl.state(())
    sort_picker: sp.SearchPicker[Suggestion] | None = sl.state(None, opaque=True)

    def __init__(
        self,
        service: SearchService,
        *,
        scope: SearchScope,
        query: CatalogueQuery,
        sort: str,
        on_apply: Callable[[SearchSelection], Awaitable[None]],
        actions: Callable[[], tuple[sl.semantic.ActionControl[sd.Request[Any, RedstoneSquid]], ...]] | None = None,
        creators: Mapping[str, str] | None = None,
    ) -> None:
        self.service = service
        self.creators = creators
        self.scope = scope
        self.query = query
        self.sort = sort
        self.on_apply = on_apply
        self.actions = actions
        self.parser = SearchQueryParser(scope=scope)

    @override
    async def on_load(self) -> None:
        self.parser = SearchQueryParser(await self.service.fields(context=ActingContext.current()), scope=self.scope)

    @property
    def clauses(self) -> tuple[QueryExpression, ...]:
        expression = self.query.parsed.expression
        return tuple(item for _, item in query_leaves(expression)) if expression is not None else ()

    def _set_expression(self, expression: QueryExpression | None) -> None:
        self.query = self.query.with_expression(self.parser, expression)

    def _replace(self, index: int, replacement: QueryExpression | None) -> None:
        expression = self.query.parsed.expression
        assert expression is not None
        path, _ = query_leaves(expression)[index]
        self._set_expression(replace_clause(expression, path, replacement))

    def _append(self, *additions: QueryExpression) -> None:
        expression = self.query.parsed.expression
        clauses = additions if expression is None else (expression, *additions)
        self._set_expression(combine_clauses(BooleanOperator.AND, *clauses))

    @override
    def render(self) -> tuple[GuidedNode, ...]:
        nodes: list[GuidedNode] = [self._controls()]
        clauses = self.clauses
        if clauses:
            nodes.append(
                sl.region(
                    *(sl.unbreakable(self._clause_row(index, item)) for index, item in enumerate(clauses)),
                    key="clauses",
                    title="",
                    page_size=_CLAUSES_PER_PAGE,
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
                self._query_form(self.query.text),
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
        field = self.selected
        if field is None:
            return (
                sl.heading(tr(t"guided-search-add-filter = Add filter")),
                *((self.filter_picker,) if self.filter_picker is not None else ()),
            )
        name = field.display_name
        step = len(self.pending_clauses) + 1
        total = step + len(self.pending_fields)
        heading = sl.heading(
            tr(t"guided-search-filter-progress = Filter by {name} ({step} of {total})")
            if total > 1
            else tr(t"guided-search-filter-heading = Filter by {name}")
        )
        change = sl.action_control(
            tr(t"guided-search-cancel-batch = Cancel adding filters")
            if total > 1
            else tr(t"guided-search-change-field = Choose another field"),
            self._change_field,
            key="change-field",
        )
        presence = sl.form(
            tr(t"guided-search-check-presence = Check presence"),
            self._presence_form(field),
            key=f"{field.name}.presence",
            on_submit=partial(self._add_presence, field),
        )
        if field.name in VALUE_SOURCES and self.filter_picker is not None:
            return (heading, self.filter_picker, sl.action_controls(presence, change, key="filter-step"))
        values = _menu_values(field, self.scope)
        if values:
            return (
                heading,
                sl.choices(
                    *(sl.choice(label, key=value) for value, label in values),
                    key=f"{field.name}.value-choice",
                    on_change=partial(self._choose_menu_value, field),
                ),
                sl.action_controls(presence, change, key="filter-step"),
            )
        return (
            heading,
            sl.action_controls(
                sl.form(
                    tr(t"guided-search-selected-field = Set {name} filter"),
                    self._field_form(field),
                    key=f"{field.name}.value",
                    on_submit=partial(self._add, field),
                ),
                presence,
                change,
                key="filter-step",
                display=sl.semantic.ControlDisplay.INDIVIDUAL,
            ),
        )

    def _clause_row(self, index: int, item: QueryExpression) -> GuidedNode:
        operand = item.operand if isinstance(item, NotExpression) else item
        negated = isinstance(item, NotExpression)
        if isinstance(operand, PresenceExpression):
            form = self._presence_form(operand.field, is_set=operand.is_set != negated)
        elif isinstance(operand, FieldExpression):
            form = self._field_form(operand.field, operand, negated=negated)
        else:
            form = self._text_form(operand, negated=negated)
        return sl.action_controls(
            sl.form(
                filter_label(item, self.creators),
                form,
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
        return sl.forms.FormSpec[sd.Request[Any, Any]](
            tr(t"guided-search-query-title = Search query"),
            (
                sl.forms.TextField(
                    key="query",
                    label=tr(t"guided-search-query-label = Words and filters, e.g. door width<=5"),
                    default=query,
                    required=False,
                    maximum=1000,
                    strip=False,
                ),
            ),
        )

    async def _advanced(self, event: sd.SubmitEvent[RedstoneSquid]) -> None:
        self.query = CatalogueQuery.from_text(self.parser, str(event.values.get("query") or ""))
        event.reveal(sl.element_ref(sl.semantic.FormTrigger, "edit-0"))
        self._close_filter()
        await self._refresh()

    def _field_form(
        self,
        field: FieldDefinition,
        existing: FieldExpression | None = None,
        *,
        negated: bool = False,
    ) -> sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]]:
        """Build the form that adds or edits a filter on `field`, prefilled from `existing`."""
        label = field.display_name
        raw = existing.value if existing is not None else None
        value = raw.lower if isinstance(raw, RangeValue) else raw
        default = str(value).lower() if isinstance(value, bool) else str(value) if value is not None else ""
        fields: list[sl.forms.FormField[Any, Any]] = []
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
        choices = field.choices(self.scope)
        if choices and (not default or default in choices):
            fields.append(
                sl.forms.ChoiceField(
                    key="value",
                    label=label,
                    default=default or None,
                    options=tuple(
                        sl.forms.ChoiceOption(item, item.replace("_", " ").capitalize(), item) for item in choices
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
        return sl.forms.FormSpec[sd.Request[Any, Any]](label, tuple(fields))

    def _presence_form(
        self, field: FieldDefinition, *, is_set: bool = True
    ) -> sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]]:
        return sl.forms.FormSpec[sd.Request[Any, Any]](
            field.display_name,
            (
                sl.forms.ChoiceField(
                    key="presence",
                    label=tr(t"guided-search-field-presence = Field presence"),
                    default="set" if is_set else "missing",
                    options=(
                        sl.forms.ChoiceOption("set", tr(t"guided-search-is-set = Is set"), "set"),
                        sl.forms.ChoiceOption("missing", tr(t"guided-search-is-not-set = Is not set"), "missing"),
                    ),
                ),
            ),
        )

    def _text_form(
        self, item: QueryExpression, *, negated: bool = False
    ) -> sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]]:
        assert isinstance(item, TextExpression)
        return sl.forms.FormSpec[sd.Request[Any, Any]](
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

    def _field_expression(self, field: FieldDefinition, event: sd.SubmitEvent[RedstoneSquid]) -> FieldExpression:
        value = FieldRegistry.coerce(field, str(event.values["value"]), scope=self.scope)
        operator = str(event.values.get("operator") or "")
        if operator == "range":
            upper = FieldRegistry.coerce(field, str(event.values.get("upper") or ""), scope=self.scope)
            return FieldExpression(field, ComparisonOperator.EQUAL, RangeValue(value, upper))
        comparison = next(comparison for comparison, symbol in OPERATORS.items() if symbol == operator)
        return FieldExpression(field, comparison, value, phrase=isinstance(value, str))

    async def _edit_clause(self, index: int, event: sd.SubmitEvent[RedstoneSquid]) -> None:
        item = self.clauses[index]
        item = item.operand if isinstance(item, NotExpression) else item
        if isinstance(item, PresenceExpression):
            replacement = PresenceExpression(item.field, is_set=event.values["presence"] == "set")
        elif isinstance(item, FieldExpression):
            replacement = self._field_expression(item.field, event)
        else:
            replacement = TextExpression(str(event.values["value"]), phrase=True)
        self._replace(index, NotExpression(replacement) if event.values.get("negated") else replacement)
        await self._refresh()

    def close_panels(self) -> None:
        self._close_filter()
        self.sort_picker = None

    def _close_filter(self) -> None:
        self.filter_open = False
        self.filter_picker = None
        self.selected = None
        self.pending_fields = ()
        self.pending_clauses = ()

    async def _picker(
        self,
        event: sd.ActionEvent[RedstoneSquid],
        source: str,
        on_pick: sp.SearchPickHandler[Suggestion],
        *,
        key: str,
        label: Callable[[Suggestion], sl.TextLike],
        search_label: sl.TextLike,
        context: Mapping[str, str] | None = None,
        maximum: int = 1,
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

        initial_source = search("")
        picker = sp.SearchPicker(
            search,
            key=key,
            identity=lambda item: item.value,
            label=label,
            description=lambda item: item.description or "",
            on_pick=on_pick,
            maximum=maximum,
            fetch_size=_PICKER_PAGE_SIZE,
            show_heading=False,
            show_query=True,
            search_label=search_label,
            hide_search_when_complete=initial_source.enumerable,
        )
        picker.session = sp.BrowseSession(initial_source, identity=picker.identity, fetch_size=picker.fetch_size)
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
            self._fields_picked,
            key="filter-lookup",
            label=lambda item: self.parser.registry.display_name(item.value),
            search_label=tr(t"guided-search-search-filter = Search fields"),
            context={"scope": self.scope},
            maximum=_PICKER_PAGE_SIZE,
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
            label=lambda item: self.parser.registry.sort_label(item.value),
            search_label=tr(t"guided-search-search-options = Search sort orders"),
            context={"scope": self.scope},
        )

    async def _fields_picked(self, event: sl.ActionEvent, items: tuple[Suggestion, ...]) -> None:
        fields = tuple(field for item in items if (field := self.parser.registry.resolve(item.value)) is not None)
        if fields:
            await self._open_field(cast(sd.ActionEvent[RedstoneSquid], event), fields[0])
            self.pending_fields = fields[1:]

    async def _open_field(self, event: sd.ActionEvent[RedstoneSquid], field: FieldDefinition) -> None:
        picker = None
        if field.name in VALUE_SOURCES:

            async def chosen(action: sl.ActionEvent, values: tuple[Suggestion, ...]) -> None:
                if values:
                    await self._add_choice(field, values[0].value, action)

            name = field.display_name
            picker = await self._picker(
                event,
                VALUE_SOURCES[field.name],
                chosen,
                key="value-lookup",
                label=lambda value: value.label,
                search_label=tr(t"guided-search-search-values = Search {name}"),
            )
        self.filter_picker = picker
        self.selected = field

    async def _change_field(self, event: sd.ActionEvent[RedstoneSquid]) -> None:
        self.filter_picker = await self._fields_picker(event)
        self.selected = None
        self.pending_fields = ()
        self.pending_clauses = ()

    async def _choose_menu_value(self, field: FieldDefinition, event: sl.ChoiceEvent) -> None:
        if len(event.selected) == 1:
            await self._add_choice(field, event.selected[0], event)

    async def _add_choice(self, field: FieldDefinition, raw: str, event: sl.ActionEvent) -> None:
        value = FieldRegistry.coerce(field, raw, scope=self.scope)
        await self._apply_filter(
            field, FieldExpression(field, ComparisonOperator.EQUAL, value, phrase=isinstance(value, str)), event
        )

    async def _sort_picked(self, _event: sl.ActionEvent, items: tuple[Suggestion, ...]) -> None:
        # Keep the sort panel open with nothing marked, so another order is one choice away.
        if self.sort_picker is not None:
            self.sort_picker.picked = ()
        if items and items[0].value != self.sort:
            self.sort = items[0].value
            await self._refresh()

    async def _add(self, field: FieldDefinition, event: sd.SubmitEvent[RedstoneSquid]) -> None:
        value = self._field_expression(field, event)
        addition = NotExpression(value) if event.values.get("negated") else value
        await self._apply_filter(field, addition, event)

    async def _add_presence(self, field: FieldDefinition, event: sd.SubmitEvent[RedstoneSquid]) -> None:
        await self._apply_filter(field, PresenceExpression(field, is_set=event.values["presence"] == "set"), event)

    async def _apply_filter(self, field: FieldDefinition, addition: QueryExpression, event: sl.ActionEventView) -> None:
        if self.selected != field:
            return
        request_event = cast(sd.ActionEvent[RedstoneSquid], event)
        additions = (*self.pending_clauses, addition)
        if self.pending_fields:
            next_field, *remaining = self.pending_fields
            await self._open_field(request_event, next_field)
            self.pending_fields = tuple(remaining)
            self.pending_clauses = additions
            return
        if self.pending_clauses:
            await self._change_field(request_event)
        elif self.filter_picker is not None:
            self.filter_picker.picked = ()
        self._append(*additions)
        event.reveal(sl.element_ref(sl.semantic.FormTrigger, f"edit-{len(self.clauses) - 1}"))
        await self._refresh()

    async def _remove(self, index: int, _event: sd.PressEvent[RedstoneSquid]) -> None:
        self._replace(index, None)
        await self._refresh()

    async def _refresh(self) -> None:
        await self.on_apply(SearchSelection(self.query, self.sort))


def _menu_values(field: FieldDefinition, scope: SearchScope) -> tuple[tuple[str, sl.TextLike], ...]:
    """Return (value, label) pairs for a field chosen from a short menu, or nothing for other fields."""
    if field.value_type is FieldType.BOOLEAN:
        return (("true", tr(t"guided-search-yes = Yes")), ("false", tr(t"guided-search-no = No")))
    if choices := field.choices(scope):
        return tuple((item, item.replace("_", " ").capitalize()) for item in choices)
    return ()
