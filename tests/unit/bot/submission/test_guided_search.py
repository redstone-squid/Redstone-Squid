"""Query text and builder controls edit the same expression."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest

from squid.bot.submission.guided_search import Filter, GuidedSearch, SearchSelection
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.search.application.parser import SearchQueryParser
from squid.search.domain.query import BooleanExpression, BooleanOperator, FieldExpression
from squid.suggestions.application import Candidate, SuggestionRegistry, SuggestionService
from squid.suggestions.application import SuggestionSource as RegisteredSource
from squid_ui_discord.testing import iter_component_payloads, stage
from tests.unit.suggestions.test_services import RecordingProvider


def editor(query: str = "") -> GuidedSearch:
    service = AsyncMock()
    service.fields.return_value = DEFAULT_FIELD_REGISTRY
    return GuidedSearch(
        service,
        scope="builds",
        query=query,
        sort="",
        on_apply=AsyncMock(),
    )


def test_added_filters_preserve_or_grouping() -> None:
    screen = editor("title:door OR title:extender")
    screen._append(Filter("width", ">=", "3"))
    parsed = SearchQueryParser().parse(screen.query).expression
    assert isinstance(parsed, BooleanExpression)
    assert parsed.operator is BooleanOperator.AND
    assert isinstance(parsed.operands[0], BooleanExpression)
    assert parsed.operands[0].operator is BooleanOperator.OR
    assert isinstance(parsed.operands[1], FieldExpression)
    assert parsed.operands[1].value == 3


def test_named_values_cannot_inject_query_syntax() -> None:
    value = 'a" OR width:99 OR "b'
    screen = editor()
    screen._append(Filter("creator", "", value))
    parsed = SearchQueryParser().parse(screen.query).expression
    assert isinstance(parsed, FieldExpression)
    assert parsed.value == value


async def test_text_edits_replace_builder_filters_and_apply_exact_query() -> None:
    screen = editor("title:door AND width:3")
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("remove-1"))
        assert screen.query == "title:door"
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": "width>=5 AND NOT tag:slime"})
        assert len(screen.clauses) == 2
        await ui.press(ui.key_for("remove-0"))
        assert screen.query == "NOT tag:slime"
        cast(AsyncMock, screen.on_apply).assert_awaited_with(SearchSelection("NOT tag:slime", ""))


async def test_edit_boolean_group_preserves_other_clauses() -> None:
    screen = editor("(width:3 OR width:4) AND height:5")
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("edit-0"))
        await form.submit({"value": "6", "operator": "0"})
        assert screen.query == "(width:6 OR width:4) AND height:5"
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": ""})
        assert screen.query == ""
        assert screen.clauses == ()


async def test_long_queries_paginate_removable_clauses() -> None:
    screen = editor(" AND ".join(f"width:{index}" for index in range(8)))
    async with stage(screen, owner=object()) as ui:
        for _ in range(3):
            await ui.press(ui.key_for("next-clauses"))
        await ui.press(ui.key_for("remove-7"))
        assert len(screen.clauses) == 7


async def test_field_and_sort_pickers_stay_in_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SuggestionService(
        SuggestionRegistry.of(
            (
                RegisteredSource("search_fields", RecordingProvider((Candidate.create("width", "Width"),))),
                RegisteredSource("search_sorts", RecordingProvider((Candidate.create("-width", "Widest first"),))),
            )
        )
    )
    screen = editor()
    async with stage(screen, owner=object()) as ui:
        monkeypatch.setattr(ui._client, "actors", SimpleNamespace(resolve=AsyncMock(return_value=None)), raising=False)
        monkeypatch.setattr(ui._client, "services", SimpleNamespace(suggestions=service), raising=False)
        for control, value in (("field", "width"), ("sort", "-width")):
            interaction = await ui.press(ui.key_for(control))
            assert not interaction.sends
            with anyio.fail_after(2):
                while not any(key.endswith("search-lookup.results") for key in ui.root.snapshot().handler_keys):
                    await anyio.sleep(0.01)
            await ui.select(ui.key_for("search-lookup.results"), [value])
            assert screen.picker is None
            assert not ui.root.finished
        assert screen.selected is not None
        assert screen.selected.name == "width"
        assert screen.sort == "-width"
        cast(AsyncMock, screen.on_apply).assert_awaited_with(SearchSelection("", "-width"))


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
        assert "width:3" in edit.payload["label"]


async def test_guided_edits_preserve_or_and_negation() -> None:
    screen = editor('(closing_time>"5" OR opening_time>"5") AND NOT width:3')
    async with stage(screen, owner=object()) as ui:
        first = await ui.press_for_form(ui.key_for("edit-0"))
        assert {item.custom_id for item in first.modal.walk_children() if hasattr(item, "custom_id")} == {
            "value",
            "operator",
            "upper",
        }
        await first.submit({"value": "7", "operator": "3"})
        assert screen.query == '(closing_time>7 OR opening_time>"5") AND NOT width:3'
        await ui.press(ui.key_for("remove-1"))
        assert screen.query == "closing_time>7 AND NOT width:3"
        second = await ui.press_for_form(ui.key_for("edit-1"))
        await second.submit({"value": "4", "operator": "0"})
        assert screen.query == "closing_time>7 AND NOT width:4"


async def test_guided_range_edit_prefills_and_preserves_bounds() -> None:
    screen = editor("width:[3 TO 5] OR height:7")
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("edit-0"))
        await form.submit({"value": "4", "upper": "6", "operator": "5"})
        assert screen.query == "width:[4 TO 6] OR height:7"


async def test_keyword_edit_treats_query_syntax_as_literal_text() -> None:
    screen = editor("door OR width:3")
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("edit-0"))
        await form.submit({"value": "door AND height:99"})
        assert screen.query == '"door AND height:99" OR width:3'


async def test_filter_picker_has_specific_search_copy_and_shows_search_text(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = RecordingProvider((Candidate.create("width", "Width"),))
    service = SuggestionService(SuggestionRegistry.of((RegisteredSource("search_fields", provider),)))
    screen = editor()
    async with stage(screen, owner=object()) as ui:
        monkeypatch.setattr(ui._client, "actors", SimpleNamespace(resolve=AsyncMock(return_value=None)), raising=False)
        monkeypatch.setattr(ui._client, "services", SimpleNamespace(suggestions=service), raising=False)
        await ui.press(ui.key_for("field"))
        assert "## Search" not in ui.texts()
        assert ui.control(ui.key_for("search-lookup.search")).payload["label"] == "Search filter"
        form = await ui.press_for_form(ui.key_for("search-lookup.search"))
        await form.submit({"query": "wid"})
        assert "wid" in ui.texts()
        with anyio.fail_after(2):
            while not any(key.endswith("search-lookup.results") for key in ui.root.snapshot().handler_keys):
                await anyio.sleep(0.01)
        await ui.select(ui.key_for("search-lookup.results"), ["width"])
        assert ui.control(ui.key_for("field")).payload["label"] == "Filter: Width"
