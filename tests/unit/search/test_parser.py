"""Search query parser tests."""

from decimal import Decimal

import pytest

from squid.core.records.domain import RecordCriterion, ResolutionStatus
from squid.core.search.application import (
    DEFAULT_FIELD_REGISTRY,
    FieldDefinition,
    FieldRegistry,
    FieldType,
    QuerySyntaxError,
    SearchQueryParser,
)
from squid.core.search.application.fields import BuildSearchStatus
from squid.core.search.domain import (
    BooleanExpression,
    BooleanOperator,
    BuildQuery,
    ComparisonOperator,
    FieldExpression,
    NotExpression,
    RangeValue,
    RecordQuery,
    SearchScope,
    TextExpression,
)


def _field(name: str) -> FieldDefinition:
    field = DEFAULT_FIELD_REGISTRY.resolve(name)
    assert field is not None
    return field


def test_parser_applies_and_before_or_and_supports_implicit_and() -> None:
    parsed = SearchQueryParser().parse('compact OR title:"seamless door" status:confirmed')

    assert parsed.expression == BooleanExpression(
        BooleanOperator.OR,
        (
            TextExpression("compact"),
            BooleanExpression(
                BooleanOperator.AND,
                (
                    FieldExpression(_field("title"), ComparisonOperator.EQUAL, "seamless door", phrase=True),
                    FieldExpression(_field("status"), ComparisonOperator.EQUAL, "confirmed"),
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
            NotExpression(FieldExpression(_field("kind"), ComparisonOperator.EQUAL, "metadata")),
            FieldExpression(_field("volume"), ComparisonOperator.EQUAL, RangeValue(10, 20)),
            FieldExpression(_field("opening_time"), ComparisonOperator.LESS_THAN, 1.5),
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
        _field("completion_at"),
        ComparisonOperator.GREATER_THAN_OR_EQUAL,
        "2026-07-30T00:00:00",
    )
    assert parsed.normalized == "completion_at>=2026-07-30T00:00:00"


def test_default_registry_exposes_sortable_build_recency_fields() -> None:
    created_at = DEFAULT_FIELD_REGISTRY.resolve("created_at")
    updated_at = DEFAULT_FIELD_REGISTRY.resolve("updated_at")

    assert created_at is not None
    assert updated_at is not None
    assert created_at.value_type is FieldType.TIMESTAMP
    assert updated_at.value_type is FieldType.TIMESTAMP
    assert created_at.supports_range
    assert created_at.supports_sort
    assert updated_at.supports_range
    assert updated_at.supports_sort


def test_parser_accepts_empty_query_for_filter_defaults() -> None:
    assert SearchQueryParser().parse("   ").expression is None


def test_parser_coerces_units_for_database_defined_data_tag() -> None:
    closing_delay = FieldDefinition(
        "closing_delay",
        FieldType.NUMBER,
        supports_range=True,
        storage_name="tag:42",
        unit_scales=(("s", Decimal("20")), ("gt", Decimal("1"))),
        numeric_step=Decimal("1"),
    )

    parsed = SearchQueryParser(FieldRegistry((closing_delay,))).parse("closing_delay:[0.4s TO 10gt]")

    assert parsed.expression == FieldExpression(
        closing_delay, ComparisonOperator.EQUAL, RangeValue(Decimal("8.0"), Decimal("10"))
    )


@pytest.mark.parametrize("prefix", ["smallest", "fastest", "fastest smallest", "smallest fastest"])
def test_parser_expands_leading_record_titles(prefix: str) -> None:
    parser = SearchQueryParser()
    expected = f"criterion:{prefix.replace(' ', '_')}"

    assert parser.parse(prefix).expression == parser.parse(expected).expression
    parsed = parser.parse(f"  {prefix.upper()} kind:door width<=5")
    assert parsed.expression == parser.parse(f"{expected} kind:door width<=5").expression
    assert parsed.normalized == f"{expected} AND kind:door AND width<=5"
    assert parser.parse(parsed.normalized) == parsed


@pytest.mark.parametrize(
    ("query", "normalized"),
    [
        ('"smallest" door', '"smallest" AND door'),
        ('"fastest smallest" door', '"fastest smallest" AND door'),
        ("door fastest", "door AND fastest"),
        ("first attempt", "first AND attempt"),
        ("(fastest) door", "(fastest AND door)"),
        ("smallestish door", "smallestish AND door"),
        ("title:fastest", "title:fastest"),
        ("fastest criterion:smallest", "fastest AND criterion:smallest"),
        ("smallest NOT CRITERION:fastest", "smallest AND NOT criterion:fastest"),
    ],
)
def test_parser_preserves_literal_text_and_explicit_record_filters(query: str, normalized: str) -> None:
    parser = SearchQueryParser()
    parsed = parser.parse(query)
    assert parsed.normalized == normalized
    assert parser.parse(normalized) == parsed


def test_record_prefix_preserves_boolean_precedence_and_error_positions() -> None:
    parser = SearchQueryParser()

    assert (
        parser.parse("fastest door OR extender").expression
        == parser.parse("criterion:fastest door OR extender").expression
    )
    with pytest.raises(QuerySyntaxError) as captured:
        parser.parse("fastest smallest AND")
    assert captured.value.position == len("fastest smallest AND")


def test_record_prefix_requires_a_registered_base_title_field() -> None:
    assert SearchQueryParser(FieldRegistry(())).parse("smallest door").normalized == "smallest AND door"


def test_catalogue_scopes_use_distinct_queries_and_enum_values() -> None:
    builds = SearchQueryParser(scope=SearchScope.BUILDS).parse("status:CONFIRMED")
    records = SearchQueryParser(scope=SearchScope.RECORDS).parse("status:resolved")
    shorthand = SearchQueryParser(scope=SearchScope.RECORDS).parse("Fastest")
    assert isinstance(builds, BuildQuery)
    assert isinstance(builds.expression, FieldExpression)
    assert builds.expression.value is BuildSearchStatus.CONFIRMED
    assert isinstance(records, RecordQuery)
    assert isinstance(records.expression, FieldExpression)
    assert records.expression.value is ResolutionStatus.RESOLVED
    assert isinstance(shorthand.expression, FieldExpression)
    assert shorthand.expression.value is RecordCriterion.FASTEST
    assert SearchQueryParser(scope=SearchScope.BUILDS).parse("Fastest").expression == TextExpression("Fastest")


@pytest.mark.parametrize(
    ("scope", "query", "error"),
    [
        (SearchScope.BUILDS, "criterion:first", "cannot be used"),
        (SearchScope.BUILDS, "status:resolved", "expects one of"),
        (SearchScope.RECORDS, "door OR NOT width:5", "cannot be used"),
        (SearchScope.RECORDS, "status:confirmed", "expects one of"),
        (SearchScope.RECORDS, "kind:other", "expects one of"),
    ],
)
def test_catalogue_parser_rejects_wrong_scope_fields_and_values(scope: SearchScope, query: str, error: str) -> None:
    with pytest.raises(QuerySyntaxError, match=error):
        SearchQueryParser(scope=scope).parse(query)
