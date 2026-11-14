"""Build search clauses through named fields while preserving existing advanced expressions."""

import json
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, override

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.app import RedstoneSquid
from squid.bot.lookup import pick
from squid.bot.ui import tr
from squid.records.domain import BuildKind
from squid.search.application import SearchService
from squid.search.application.fields import FieldRegistry
from squid.search.domain.fields import FieldDefinition, FieldType
from squid.suggestions.domain import Suggestion

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
    advanced: str
    keywords: str
    filters: tuple[Filter, ...]


def combine_query(advanced: str, keywords: str, filters: tuple[Filter, ...]) -> str:
    parts = [f"({advanced})"] if advanced.strip() else []
    parts.extend(json.dumps(word, ensure_ascii=False) for word in keywords.split())
    parts.extend(item.query for item in filters)
    return " AND ".join(parts)


class GuidedSearch(sd.Screen[SearchSelection, Any, RedstoneSquid]):
    policy = sd.ResponseSpec(timeout=300, session=sd.SessionSpec("guided-search"))
    advanced: str = sl.state("")
    keywords: str = sl.state("")
    sort: str = sl.state("")
    filters: tuple[Filter, ...] = sl.state(())
    selected: FieldDefinition | None = sl.state(None, persist=False)

    def __init__(
        self, service: SearchService, *, scope: str, query: str, sort: str, previous: SearchSelection | None = None
    ) -> None:
        self.service = service
        self.scope = scope
        self.advanced = query
        self.sort = sort
        if previous is not None:
            self.advanced, self.keywords, self.filters = previous.advanced, previous.keywords, previous.filters

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = [
            sl.heading(tr(t"guided-search-title = Search and filters")),
            sl.note(
                tr(
                    t"guided-search-help = Every filter below must match. Existing advanced queries keep their grouping."
                )
            ),
            sl.action_controls(
                sl.form(
                    tr(t"guided-search-keywords = Keywords"),
                    sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]](
                        tr(t"guided-search-keywords = Keywords"),
                        (
                            sl.forms.TextField(
                                key="keywords",
                                label=tr(t"guided-search-keywords = Keywords"),
                                default=self.keywords,
                                required=False,
                                maximum=500,
                            ),
                        ),
                    ),
                    key="keywords",
                    on_submit=self._keywords,
                ),
                sl.action_control(tr(t"guided-search-add-filter = Choose filter"), self._choose_field, key="field"),
                sl.action_control(tr(t"guided-search-sort = Choose sorting"), self._choose_sort, key="sort"),
                sl.action_control(tr(t"guided-search-relevance = Use relevance"), self._relevance, key="relevance"),
                key="search-controls",
            ),
        ]
        for index, item in enumerate(self.filters):
            nodes.append(
                sl.stack(
                    sl.paragraph(f"{item.field.replace('_', ' ').title()} {item.operator or '='} {item.value}"),
                    sl.action_controls(
                        sl.action_control(
                            tr(t"guided-search-remove = Remove filter"),
                            partial(self._remove, index),
                            key=f"remove-{index}",
                        ),
                        key=f"filter-{index}",
                    ),
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
        if self.advanced:
            nodes.append(sl.note(self.advanced))
        if self.keywords:
            nodes.append(sl.note(self.keywords))
        if self.sort:
            nodes.append(
                sl.note(self.sort.removeprefix("-").replace("_", " ") + (" ↓" if self.sort.startswith("-") else " ↑"))
            )
        nodes.append(
            sl.action_controls(
                sl.form(
                    tr(t"guided-search-advanced = Advanced query"),
                    sl.forms.FormSpec[sd.Request[Any, RedstoneSquid]](
                        tr(t"guided-search-advanced = Advanced query"),
                        (
                            sl.forms.TextField(
                                key="query",
                                label=tr(t"guided-search-expression = Query expression"),
                                default=self.advanced,
                                required=False,
                                maximum=1000,
                            ),
                        ),
                    ),
                    key="advanced",
                    on_submit=self._advanced,
                ),
                sl.action_control(tr(t"guided-search-run = Search"), self._apply, key="apply"),
                sl.action_control(tr(t"guided-search-cancel = Cancel"), self._cancel, key="cancel"),
                key="search-actions",
            )
        )
        return tuple(nodes)

    async def _keywords(self, event: sd.SubmitEvent) -> None:
        self.keywords = str(event.values.get("keywords") or "")

    async def _advanced(self, event: sd.SubmitEvent) -> None:
        self.advanced = str(event.values.get("query") or "")

    async def _choose_field(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await pick(
            event,
            "search_fields",
            tr(t"guided-search-add-filter = Choose filter"),
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
            self.filters = (*self.filters, Filter(field.name, "", item.value))
            self.selected = None

        await pick(event, VALUE_SOURCES[field.name], field.name.replace("_", " ").title(), selected)

    async def _choose_sort(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await pick(
            event,
            "search_sorts",
            tr(t"guided-search-sort = Choose sorting"),
            self._sort_picked,
            context={"scope": self.scope},
        )

    async def _sort_picked(self, _event: sd.ActionEvent, item: Suggestion) -> None:
        self.sort = item.value

    async def _relevance(self, _event: sd.PressEvent) -> None:
        self.sort = ""

    async def _add(self, field: FieldDefinition, event: sd.SubmitEvent) -> None:
        raw = str(event.values["value"])
        value = FieldRegistry.coerce(field, raw)
        self.filters = (
            *self.filters,
            Filter(
                field.name, str(event.values["operator"]), str(value).lower() if isinstance(value, bool) else str(value)
            ),
        )
        self.selected = None

    async def _remove(self, index: int, _event: sd.PressEvent) -> None:
        self.filters = tuple(item for position, item in enumerate(self.filters) if position != index)

    async def _apply(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        self.complete(
            event,
            SearchSelection(
                combine_query(self.advanced, self.keywords, self.filters),
                self.sort,
                self.advanced,
                self.keywords,
                self.filters,
            ),
        )

    async def _cancel(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        self.cancel(event)
