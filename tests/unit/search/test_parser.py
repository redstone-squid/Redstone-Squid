"""Search query parser tests."""

import pytest

from squid.search.application import QuerySyntaxError, SearchQueryParser
from squid.search.domain import (
    BooleanExpression,
    BooleanOperator,
    ComparisonOperator,
    FieldExpression,
    NotExpression,
    RangeValue,
    TextExpression,
)


def test_parser_applies_and_before_or_and_supports_implicit_and() -> None:
    parsed = SearchQueryParser().parse('compact OR title:"seamless door" status:confirmed')

    assert parsed.expression == BooleanExpression(
        BooleanOperator.OR,
        (
            TextExpression("compact"),
            BooleanExpression(
                BooleanOperator.AND,
                (
                    FieldExpression("title", ComparisonOperator.EQUAL, "seamless door", phrase=True),
                    FieldExpression("status", ComparisonOperator.EQUAL, "confirmed"),
                ),
            ),
        ),
    )
    assert parsed.normalized == 'compact OR (title:"seamless door" AND status:confirmed)'


def test_parser_supports_not_comparisons_and_typed_ranges() -> None:
    parsed = SearchQueryParser().parse("NOT kind:metadata volume:[10 TO 20] opening_time<1.5")

    assert parsed.expression == BooleanExpression(
        BooleanOperator.AND,
        (
            NotExpression(FieldExpression("kind", ComparisonOperator.EQUAL, "metadata")),
            FieldExpression("volume", ComparisonOperator.EQUAL, RangeValue(10, 20)),
            FieldExpression("opening_time", ComparisonOperator.LESS_THAN, 1.5),
        ),
    )


def test_parser_supports_parentheses_and_escaped_phrases() -> None:
    parsed = SearchQueryParser().parse('(tag:slim OR tag:"no observer") NOT "Bob\'s \\"door\\""')

    assert isinstance(parsed.expression, BooleanExpression)
    assert parsed.normalized == '(tag:slim OR tag:"no observer") AND NOT "Bob\'s \\"door\\""'


@pytest.mark.parametrize("query", ["title:*door", "door~2", "title:door^4", "title:do?r"])
def test_parser_rejects_unsupported_lucene_modifiers(query: str) -> None:
    with pytest.raises(QuerySyntaxError, match="unsupported query modifier"):
        SearchQueryParser().parse(query)


def test_parser_reports_unknown_field_location_and_suggestion() -> None:
    with pytest.raises(QuerySyntaxError) as captured:
        SearchQueryParser().parse("titel:door")

    assert captured.value.position == 0
    assert captured.value.suggestions == ("title",)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("title<door", "does not support comparisons"),
        ("title:[a TO z]", "does not support ranges"),
        ("volume:large", "expects a number"),
        ("volume:[1 2]", "expected 'TO'"),
        ('"unterminated', "unterminated quoted phrase"),
        ("door AND", "expected a search term"),
    ],
)
def test_parser_returns_actionable_errors(query: str, message: str) -> None:
    with pytest.raises(QuerySyntaxError, match=message):
        SearchQueryParser().parse(query)


def test_parser_normalizes_alias_and_timestamp() -> None:
    parsed = SearchQueryParser().parse("completion_date>=2026-07-30")

    assert parsed.expression == FieldExpression(
        "completion_at",
        ComparisonOperator.GREATER_THAN_OR_EQUAL,
        "2026-07-30T00:00:00",
    )
    assert parsed.normalized == "completion_at>=2026-07-30T00:00:00"


def test_parser_accepts_empty_query_for_filter_defaults() -> None:
    assert SearchQueryParser().parse("   ").expression is None
