"""Guided filters preserve advanced Boolean grouping and treat values as literals."""

from unittest.mock import AsyncMock

from squid.bot.submission.guided_search import Filter, GuidedSearch, SearchSelection, combine_query
from squid.search.application.parser import SearchQueryParser
from squid.search.domain.query import BooleanExpression, BooleanOperator, FieldExpression
from squid_ui_discord.testing import stage


def test_added_filters_do_not_change_existing_or_grouping() -> None:
    query = combine_query("title:door OR title:extender", "", (Filter("width", ">=", "3"),))
    parsed = SearchQueryParser().parse(query).expression
    assert isinstance(parsed, BooleanExpression)
    assert parsed.operator is BooleanOperator.AND
    assert isinstance(parsed.operands[0], BooleanExpression)
    assert parsed.operands[0].operator is BooleanOperator.OR
    assert isinstance(parsed.operands[1], FieldExpression)
    assert parsed.operands[1].value == 3


def test_named_values_cannot_inject_query_syntax() -> None:
    value = 'a" OR width:99 OR "b'
    parsed = SearchQueryParser().parse(combine_query("", "", (Filter("creator", "", value),))).expression
    assert isinstance(parsed, FieldExpression)
    assert parsed.value == value


async def test_reopening_keeps_removable_filter_rows_and_advanced_text() -> None:
    filters = (Filter("width", "", "3"),)
    previous = SearchSelection("title:door AND width:3", "-width", "title:door", "", filters)
    screen = GuidedSearch(AsyncMock(), scope="builds", query=previous.query, sort=previous.sort, previous=previous)
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("remove-0"))
        assert screen.filters == ()
        assert screen.advanced == "title:door"
        assert screen.sort == "-width"
