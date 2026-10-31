"""Cursor-aware query analysis tests.

Every input here is a syntax error to `SearchQueryParser`, which is the point: a query being typed
is never valid, so completion cannot be built on a parser that refuses to read one.
"""

import pytest

from squid.search.application.completion import (
    CompletionKind,
    analyze,
    completes_values,
    render_value,
)
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.search.application.parser import QuerySyntaxError, SearchQueryParser


@pytest.mark.parametrize("query", ["restriction:", 'creator:"Nu', "width:[1 TO ", "restriction:*"])
def test_the_parser_cannot_read_these_but_analysis_still_answers(query: str) -> None:
    with pytest.raises(QuerySyntaxError):
        SearchQueryParser().parse(query)
    assert analyze(query) is not None


def test_a_query_that_already_parses_is_still_completable() -> None:
    """`restriction:sea` is valid — it just is not yet what the user means."""
    assert SearchQueryParser().parse("restriction:sea").normalized == "restriction:sea"
    assert analyze("restriction:sea").prefix == "sea"


def test_a_bare_word_completes_as_a_term() -> None:
    context = analyze("seam")
    assert context.kind is CompletionKind.TERM
    assert context.prefix == "seam"
    assert (context.start, context.end) == (0, 4)


def test_an_empty_query_completes_at_the_start() -> None:
    context = analyze("")
    assert context.kind is CompletionKind.TERM
    assert (context.start, context.end) == (0, 0)


def test_a_field_operator_switches_to_completing_its_value() -> None:
    context = analyze("restriction:sea")
    assert context.kind is CompletionKind.FIELD_VALUE
    assert context.field is not None
    assert context.field.name == "restriction"
    assert context.prefix == "sea"
    # Only the value is replaced, so the field name the user typed survives.
    assert (context.start, context.end) == (12, 15)


def test_an_empty_value_still_resolves_its_field() -> None:
    context = analyze("restriction:")
    assert context.kind is CompletionKind.FIELD_VALUE
    assert context.prefix == ""
    assert (context.start, context.end) == (12, 12)


@pytest.mark.parametrize("operator", ["<", ">", "<=", ">="])
def test_comparison_operators_are_recognized(operator: str) -> None:
    context = analyze(f"width{operator}5")
    assert context.kind is CompletionKind.FIELD_VALUE
    assert context.field is not None
    assert context.field.name == "width"
    assert context.prefix == "5"


def test_an_unknown_field_completes_as_a_field_name_not_as_its_value() -> None:
    """Otherwise a typo would offer values of a field that does not exist."""
    context = analyze("restrictoin:sea")
    assert context.kind is CompletionKind.TERM
    assert context.field is None


def test_an_alias_resolves_to_its_canonical_field() -> None:
    context = analyze("completion_date:2024")
    assert context.field is not None
    assert context.field.name == "completion_at"


def test_only_the_token_under_the_caret_is_completed() -> None:
    context = analyze("kind:door restriction:sea")
    assert context.field is not None
    assert context.field.name == "restriction"
    assert (context.start, context.end) == (22, 25)


def test_an_explicit_cursor_completes_mid_query() -> None:
    query = "restriction:sea AND width:3"
    context = analyze(query, cursor=15)
    assert context.field is not None
    assert context.field.name == "restriction"
    assert (context.start, context.end) == (12, 15)


def test_a_cursor_past_the_end_is_clamped() -> None:
    assert analyze("seam", cursor=999).end == 4


def test_an_open_quote_keeps_a_space_inside_the_value() -> None:
    """A name being typed is one value; the space in it does not end the token."""
    context = analyze('creator:"Nu Ha')
    assert context.kind is CompletionKind.FIELD_VALUE
    assert context.prefix == "Nu Ha"
    assert context.quoted is True


def test_a_closed_quote_ends_the_value() -> None:
    context = analyze('restriction:"Full Lamp" wid')
    assert context.kind is CompletionKind.TERM
    assert context.prefix == "wid"


def test_an_unclosed_bracket_is_a_range() -> None:
    context = analyze("width:[1 TO ")
    assert context.kind is CompletionKind.RANGE
    assert context.field is not None
    assert context.field.name == "width"


def test_a_closed_range_returns_to_completing_terms() -> None:
    context = analyze("width:[1 TO 5] AND rest")
    assert context.kind is CompletionKind.TERM
    assert context.prefix == "rest"


def test_parentheses_bound_a_token() -> None:
    context = analyze("(restriction:sea")
    assert context.field is not None
    assert (context.start, context.end) == (13, 16)


@pytest.mark.parametrize(
    ("name", "listable"),
    [("restriction", True), ("creator", True), ("width", False), ("created_at", False)],
)
def test_only_text_fields_have_values_worth_listing(name: str, listable: bool) -> None:
    """Nobody wants the twelve hundred widths that happen to be indexed."""
    field = DEFAULT_FIELD_REGISTRY.resolve(name)
    assert field is not None
    assert completes_values(field) is listable


def test_a_value_with_a_space_is_quoted_so_it_stays_one_token() -> None:
    assert render_value("Full Lamp", quoted=False) == '"Full Lamp"'
    assert render_value("seamless", quoted=False) == "seamless"


def test_an_already_quoted_value_stays_quoted() -> None:
    assert render_value("seamless", quoted=True) == '"seamless"'


def test_quotes_and_backslashes_in_a_value_are_escaped() -> None:
    rendered = render_value('a "b" \\ c', quoted=False)
    assert SearchQueryParser().parse(f"title:{rendered}") is not None


def test_a_rendered_value_parses_back_as_the_value_that_was_chosen() -> None:
    """The round trip that matters: completing must not silently widen the search."""
    query = f"restriction:{render_value('Full Lamp', quoted=False)}"
    parsed = SearchQueryParser().parse(query)
    assert parsed.normalized == 'restriction:"Full Lamp"'
