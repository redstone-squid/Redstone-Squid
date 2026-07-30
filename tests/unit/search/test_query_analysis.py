"""Search query analysis tests."""

from squid.search.application import SearchQueryParser, is_filter_only, positive_text_expressions
from squid.search.domain import ComparisonOperator, FieldExpression, TextExpression


def test_only_positive_text_clauses_participate_in_ranking() -> None:
    query = SearchQueryParser().parse("door NOT description:slow volume<50 tag:seamless")

    assert positive_text_expressions(query) == (
        TextExpression("door"),
        FieldExpression("tag", ComparisonOperator.EQUAL, "seamless"),
    )
    assert not is_filter_only(query)


def test_structured_and_negative_text_query_is_filter_only() -> None:
    query = SearchQueryParser().parse("status:confirmed volume<50 NOT title:broken")

    assert is_filter_only(query)
