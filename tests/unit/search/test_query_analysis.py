"""Search query analysis tests."""

from squid.core.search.application import (
    DEFAULT_FIELD_REGISTRY,
    SearchQueryParser,
    is_filter_only,
    positive_text_expressions,
)
from squid.core.search.domain import ComparisonOperator, FieldExpression, TextExpression


def test_only_positive_text_clauses_participate_in_ranking() -> None:
    query = SearchQueryParser().parse("door NOT description:slow volume<50 tag:seamless")

    tag = DEFAULT_FIELD_REGISTRY.resolve("tag")
    assert tag is not None
    assert positive_text_expressions(query) == (
        TextExpression("door"),
        FieldExpression(tag, ComparisonOperator.EQUAL, "seamless"),
    )
    assert not is_filter_only(query)


def test_structured_and_negative_text_query_is_filter_only() -> None:
    query = SearchQueryParser().parse("status:approved volume<50 NOT title:broken")

    assert is_filter_only(query)


def test_a_creator_id_filters_without_ranking() -> None:
    by_id = SearchQueryParser().parse("creator:9D10EB8E-3BD0-44D6-A207-A6A4DC564E58")
    by_name = SearchQueryParser().parse('creator:"Red Stone"')

    assert isinstance(by_id.expression, FieldExpression)
    assert isinstance(by_name.expression, FieldExpression)
    assert by_id.expression.stored() == ("creator_id", "9d10eb8e-3bd0-44d6-a207-a6a4dc564e58")
    assert by_name.expression.stored() == ("creator", "Red Stone")
    assert is_filter_only(by_id)
    assert not is_filter_only(by_name)
