"""Query text and builder controls edit the same expression."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest

from squid.bot.catalogue.search.clauses import CatalogueQuery
from squid.bot.catalogue.search.filters import GuidedSearch, SearchSelection
from squid.bot.catalogue.search.labels import filter_label
from squid.core.actors import Actor
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.search.application.parser import SearchQueryParser
from squid.search.domain.query import BooleanExpression, BooleanOperator, ComparisonOperator, FieldExpression
from squid.search.domain.scope import SearchScope
from squid.suggestions.application import Candidate, SuggestionRegistry, SuggestionService
from squid.suggestions.application import SuggestionSource as RegisteredSource
from squid_i18n import Text
from squid_ui.ext.discord.testing import StagedUI, iter_component_payloads, stage
from tests.unit.suggestions.test_services import RecordingProvider


def editor(query: str = "") -> GuidedSearch:
    service = AsyncMock()
    service.fields.return_value = DEFAULT_FIELD_REGISTRY
    return GuidedSearch(
        service,
        scope=SearchScope.BUILDS,
        query=CatalogueQuery.from_text(SearchQueryParser(scope=SearchScope.BUILDS), query),
        sort="",
        on_apply=AsyncMock(),
    )


def test_added_filters_preserve_or_grouping() -> None:
    screen = editor("title:door OR title:extender")
    addition = SearchQueryParser().parse("width>=3").expression
    assert addition is not None
    screen._append(addition)
    parsed = screen.query.parsed.expression
    assert isinstance(parsed, BooleanExpression)
    assert parsed.operator is BooleanOperator.AND
    assert isinstance(parsed.operands[0], BooleanExpression)
    assert parsed.operands[0].operator is BooleanOperator.OR
    assert isinstance(parsed.operands[1], FieldExpression)
    assert parsed.operands[1].value == 3


def test_named_values_cannot_inject_query_syntax() -> None:
    value = 'a" OR width:99 OR "b'
    screen = editor()
    field = DEFAULT_FIELD_REGISTRY.resolve("creator")
    assert field is not None
    screen._append(FieldExpression(field, ComparisonOperator.EQUAL, value, phrase=True))
    parsed = screen.query.parsed.expression
    assert isinstance(parsed, FieldExpression)
    assert parsed.value == value


async def test_text_edits_replace_builder_filters_and_apply_exact_query() -> None:
    screen = editor("title:door AND width:3")
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("remove-1"))
        assert screen.query.text == "title:door"
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": "width>=5 AND NOT tag:slime"})
        assert len(screen.clauses) == 2
        await ui.press(ui.key_for("remove-0"))
        assert screen.query.text == "NOT tag:slime"
        cast(AsyncMock, screen.on_apply).assert_awaited_with(SearchSelection(screen.query, ""))


async def test_edit_boolean_group_preserves_other_clauses() -> None:
    screen = editor("(width:3 OR width:4) AND height:5")
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("edit-0"))
        await form.submit({"value": "6", "operator": "0"})
        assert screen.query.text == "(width:6 OR width:4) AND height:5"
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": ""})
        assert screen.query.text == ""
        assert screen.clauses == ()


async def test_long_queries_paginate_removable_clauses() -> None:
    screen = editor(" AND ".join(f"width:{index}" for index in range(8)))
    async with stage(screen, owner=object()) as ui:
        for _ in range(3):
            await ui.press(ui.key_for("__cursor_next.$/clauses"))
        await ui.press(ui.key_for("remove-7"))
        assert len(screen.clauses) == 7


def _suggestions(*sources: tuple[str, tuple[Candidate, ...]]) -> SuggestionService:
    return SuggestionService(
        SuggestionRegistry.of(tuple(RegisteredSource(name, RecordingProvider(items)) for name, items in sources))
    )


def _install(ui: StagedUI[GuidedSearch], monkeypatch: pytest.MonkeyPatch, service: SuggestionService) -> None:
    monkeypatch.setattr(ui._client, "actors", SimpleNamespace(resolve=AsyncMock(return_value=Actor())), raising=False)
    monkeypatch.setattr(ui._client, "services", SimpleNamespace(suggestions=service), raising=False)


async def _choose(ui: StagedUI[GuidedSearch], picker: str, value: str) -> None:
    with anyio.fail_after(2):
        while not any(key.endswith(f"{picker}.results") for key in ui.root.snapshot().handler_keys):
            await anyio.sleep(0.01)
    await ui.select(ui.key_for(f"{picker}.results"), [value])


async def test_filter_and_sort_panels_open_together_and_stay_in_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _suggestions(
        ("search_fields", (Candidate.create("width", "Width"),)),
        ("search_sorts", (Candidate.create("-width", "Widest first"),)),
    )
    screen = editor()
    async with stage(screen, owner=object()) as ui:
        _install(ui, monkeypatch, service)
        for control in ("field", "sort"):
            interaction = await ui.press(ui.key_for(control))
            assert not interaction.sends
        assert screen.filter_open
        assert screen.sort_picker is not None
        assert "## Add filter" in ui.texts()
        assert "## Sort by" in ui.texts()

        await _choose(ui, "sort-lookup", "-width")
        assert screen.sort == "-width"
        assert screen.sort_picker is not None
        assert screen.sort_picker.picked == ()
        await _choose(ui, "filter-lookup", "width")
        assert screen.selected is not None
        assert screen.selected.name == "width"
        assert "## Filter by Width" in ui.texts()
        assert screen.sort_picker is not None, "choosing a field leaves the sort panel open"
        assert not ui.root.finished
        cast(AsyncMock, screen.on_apply).assert_awaited_with(SearchSelection(screen.query, "-width"))
        ui.assert_within_limits()


async def test_named_value_fields_open_their_value_picker_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _suggestions(
        ("search_fields", (Candidate.create("creator", "Creator"),)),
        ("creators", (Candidate.create("Steve", "Steve"), Candidate.create("Alex", "Alex"))),
    )
    screen = editor()
    async with stage(screen, owner=object()) as ui:
        _install(ui, monkeypatch, service)
        await ui.press(ui.key_for("field"))
        await _choose(ui, "filter-lookup", "creator")
        assert not any(key.endswith("value") for key in ui.root.snapshot().handler_keys)
        await _choose(ui, "value-lookup", "Steve")
        assert screen.query.text == 'creator:"Steve"'
        assert screen.filter_open
        assert Text(filter_label(screen.clauses[0])).resolve() == "Creator is Steve"
        picker = screen.filter_picker
        assert picker is not None
        assert picker.picked == ()
        await _choose(ui, "value-lookup", "Alex")
        assert screen.filter_picker is picker
        assert picker.picked == ()
        assert screen.query.text == 'creator:"Steve" AND creator:"Alex"'
        cast(AsyncMock, screen.on_apply).assert_awaited_with(SearchSelection(screen.query, ""))
        ui.assert_within_limits()


async def test_fixed_values_are_chosen_from_a_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _suggestions(("search_fields", (Candidate.create("kind", "Kind"),)))
    screen = editor()
    async with stage(screen, owner=object()) as ui:
        _install(ui, monkeypatch, service)
        await ui.press(ui.key_for("field"))
        await _choose(ui, "filter-lookup", "kind")
        await ui.select(ui.key_for("value-choice"), ["door"])
        assert screen.query.text == 'kind:"door"'
        assert screen.filter_open


async def test_choose_another_field_returns_to_the_field_list(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _suggestions(("search_fields", (Candidate.create("width", "Width"),)))
    screen = editor()
    async with stage(screen, owner=object()) as ui:
        _install(ui, monkeypatch, service)
        await ui.press(ui.key_for("field"))
        await _choose(ui, "filter-lookup", "width")
        assert ui.control(ui.key_for("value")).payload["label"] == "Set Width filter"
        await ui.press(ui.key_for("change-field"))
        assert screen.selected is None
        assert "## Add filter" in ui.texts()
        await _choose(ui, "filter-lookup", "width")


async def test_filter_edit_and_remove_share_one_action_row() -> None:
    screen = editor("width:3")
    async with stage(screen, owner=object()) as ui:
        edit = ui.control(ui.key_for("edit-0"))
        remove = ui.control(ui.key_for("remove-0"))
        rows = [item for item in iter_component_payloads(ui._view().to_components()) if item.get("type") == 1]
        assert any(
            {edit.custom_id, remove.custom_id} <= {child.get("custom_id") for child in row.get("components", [])}
            for row in rows
        )
        assert edit.payload["label"] == "Width is 3"


async def test_guided_edits_preserve_or_and_negation() -> None:
    screen = editor('(closing_time>"5" OR opening_time>"5") AND NOT width:3')
    async with stage(screen, owner=object()) as ui:
        first = await ui.press_for_form(ui.key_for("edit-0"))
        assert {
            getattr(item, "custom_id", None) for item in first.modal.walk_children() if hasattr(item, "custom_id")
        } == {
            "value",
            "operator",
            "upper",
            "negated",
        }
        await first.submit({"value": "7", "operator": "3"})
        assert screen.query.text == '(closing_time>7 OR opening_time>"5") AND NOT width:3'
        await ui.press(ui.key_for("remove-1"))
        assert screen.query.text == "closing_time>7 AND NOT width:3"
        second = await ui.press_for_form(ui.key_for("edit-1"))
        await second.submit({"value": "4", "operator": "0"})
        assert screen.query.text == "closing_time>7 AND NOT width:4"


async def test_guided_range_edit_prefills_and_preserves_bounds() -> None:
    screen = editor("width:[3 TO 5] OR height:7")
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("edit-0"))
        await form.submit({"value": "4", "upper": "6", "operator": "5"})
        assert screen.query.text == "width:[4 TO 6] OR height:7"


async def test_keyword_edit_treats_query_syntax_as_literal_text() -> None:
    screen = editor("door OR width:3")
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("edit-0"))
        await form.submit({"value": "door AND height:99"})
        assert screen.query.text == '"door AND height:99" OR width:3'


async def test_filter_picker_has_specific_search_copy_and_shows_search_text(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _suggestions(("search_fields", (Candidate.create("width", "Width"),)))
    screen = editor()
    async with stage(screen, owner=object()) as ui:
        _install(ui, monkeypatch, service)
        await ui.press(ui.key_for("field"))
        assert "## Search" not in ui.texts()
        assert ui.control(ui.key_for("filter-lookup.search")).payload["label"] == "Search fields"
        form = await ui.press_for_form(ui.key_for("filter-lookup.search"))
        await form.submit({"query": "wid"})
        assert "wid" in ui.texts()
        await _choose(ui, "filter-lookup", "width")
        assert ui.control(ui.key_for("field")).payload["label"] == "Close filters"
        assert ui.control(ui.key_for("value")).payload["label"] == "Set Width filter"


async def test_not_filter_button_preserves_and_can_remove_negation() -> None:
    screen = editor("NOT width:3 OR height:5")
    async with stage(screen, owner=object()) as ui:
        assert ui.control(ui.key_for("edit-0")).payload["label"] == "Not: Width is 3"
        form = await ui.press_for_form(ui.key_for("edit-0"))
        await form.submit({"value": "4", "operator": "0", "negated": True})
        assert screen.query.text == "NOT width:4 OR height:5"
        form = await ui.press_for_form(ui.key_for("edit-0"))
        await form.submit({"value": "4", "operator": "0", "negated": False})
        assert screen.query.text == "width:4 OR height:5"
        await ui.press(ui.key_for("remove-0"))
        assert screen.query.text == "height:5"


async def test_panels_keep_filter_rows_and_survive_filter_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _suggestions(
        ("search_fields", (Candidate.create("width", "Width"),)),
        ("search_sorts", (Candidate.create("-width", "Widest first"),)),
    )
    screen = editor("width:3 AND height:5 AND depth:7")
    async with stage(screen, owner=object()) as ui:
        _install(ui, monkeypatch, service)
        await ui.press(ui.key_for("field"))
        await ui.press(ui.key_for("sort"))
        ui.control(ui.key_for("advanced"))
        for key in ("edit-0", "remove-0", "edit-1", "remove-1", "__cursor_next.$/clauses"):
            ui.control(ui.key_for(key))
        assert ui.control(ui.key_for("field")).payload["style"] == 3
        assert ui.control(ui.key_for("sort")).payload["style"] == 3
        picker = screen.sort_picker
        await ui.press(ui.key_for("__cursor_next.$/clauses"))
        form = await ui.press_for_form(ui.key_for("edit-2"))
        await form.submit({"value": "8", "operator": "0"})
        assert screen.query.text == "width:3 AND height:5 AND depth:8"
        assert screen.sort_picker is picker
        await ui.press(ui.key_for("remove-2"))
        assert screen.query.text == "width:3 AND height:5"
        assert screen.sort_picker is picker
        await ui.press(ui.key_for("field"))
        assert not screen.filter_open
        assert screen.sort_picker is picker
        await ui.press(ui.key_for("sort"))
        assert screen.sort_picker is None
        assert ui.control(ui.key_for("sort")).payload["style"] == 2


async def test_add_several_bounds_then_change_fields_without_closing_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _suggestions(
        ("search_fields", (Candidate.create("width", "Width"), Candidate.create("height", "Height"))),
    )
    screen = editor()
    async with stage(screen, owner=object()) as ui:
        _install(ui, monkeypatch, service)
        await ui.press(ui.key_for("field"))
        await _choose(ui, "filter-lookup", "width")
        for value, operator in (("3", "4"), ("5", "2")):
            form = await ui.press_for_form(ui.key_for("value"))
            await form.submit({"value": value, "operator": operator})
            assert screen.filter_open
            assert screen.selected is not None
            assert screen.selected.name == "width"
        assert screen.query.text == "width>=3 AND width<=5"
        await ui.press(ui.key_for("change-field"))
        await _choose(ui, "filter-lookup", "height")
        form = await ui.press_for_form(ui.key_for("value"))
        await form.submit({"value": "7", "operator": "0"})
        assert screen.query.text == "width>=3 AND width<=5 AND height:7"
        ui.control(ui.key_for("edit-2"))
        cast(AsyncMock, screen.on_apply).assert_awaited_with(SearchSelection(screen.query, ""))
        ui.assert_within_limits()
        await ui.press(ui.key_for("field"))
        assert not screen.filter_open
        assert screen.selected is None
        assert screen.filter_picker is None
        assert ui.control(ui.key_for("field")).payload["label"] == "Add filter"
