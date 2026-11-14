"""Turn parsed search expressions into SQLAlchemy predicates with bound values."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import and_, false, func, not_, or_, select, true
from sqlalchemy.sql.elements import ColumnElement
from whenever import Instant

from squid.search.domain import (
    BooleanExpression,
    BooleanOperator,
    ComparisonOperator,
    FieldExpression,
    FieldType,
    NotExpression,
    QueryExpression,
    RangeValue,
    SearchQuery,
    TextExpression,
)
from squid.search.models import SearchDocument, SearchDocumentFacet

_DIRECT_TEXT_FIELDS = frozenset({"status"})
_VECTOR_FIELDS = {
    "title": SearchDocument.title_vector,
    "description": SearchDocument.description_vector,
}


def compile_query(query: SearchQuery) -> ColumnElement[bool]:
    """Compile a parsed query into a predicate on SearchDocument; an empty query matches every document.

    User text only ever reaches the statement as bound values. Column and facet
    names come from the field definitions the parser resolved.
    """
    if query.expression is None:
        return true()
    return _compile_expression(query.expression)


def _compile_expression(expression: QueryExpression) -> ColumnElement[bool]:
    if isinstance(expression, TextExpression):
        return _fuzzy_text_predicate(
            cast(ColumnElement[object], SearchDocument.combined_vector),
            cast(ColumnElement[object], SearchDocument.fuzzy_text),
            expression.value,
            phrase=expression.phrase,
            exact_column=cast(ColumnElement[object], SearchDocument.normalized_title),
        )
    if isinstance(expression, NotExpression):
        return not_(_compile_expression(expression.operand))
    if isinstance(expression, BooleanExpression):
        operands = tuple(_compile_expression(operand) for operand in expression.operands)
        return and_(*operands) if expression.operator is BooleanOperator.AND else or_(*operands)
    return _compile_field(expression)


def _compile_field(expression: FieldExpression) -> ColumnElement[bool]:
    """Compare a field against the document column or the facet rows that store it."""
    field = expression.field
    if field.name in _VECTOR_FIELDS:
        if expression.operator is not ComparisonOperator.EQUAL or isinstance(expression.value, RangeValue):
            return false()
        value = str(expression.value)
        vector_match = _VECTOR_FIELDS[field.name].bool_op("@@")(_text_query(value, phrase=expression.phrase))
        if field.name == "description":
            return vector_match
        return or_(
            func.lower(SearchDocument.title) == value.casefold(),
            vector_match,
            func.similarity(SearchDocument.normalized_title, value) > 0.1,
        )
    if field.name in _DIRECT_TEXT_FIELDS:
        return _compile_direct(expression)
    if field.name == "tag":
        if expression.operator is not ComparisonOperator.EQUAL or isinstance(expression.value, RangeValue):
            return false()
        return SearchDocument.tags.contains([str(expression.value)])
    # Use a separate EXISTS for each clause: a document may satisfy two clauses
    # through different facet rows, and matching rows must not duplicate results.
    facet = SearchDocumentFacet
    value_column = {
        FieldType.TEXT: facet.text_value,
        FieldType.NUMBER: facet.numeric_value,
        FieldType.TIMESTAMP: facet.timestamp_value,
        FieldType.BOOLEAN: facet.boolean_value,
    }[field.value_type]
    comparison = _comparison(cast(ColumnElement[object], value_column), expression, field.value_type)
    return (
        select(facet.document_id)
        .where(
            facet.document_id == SearchDocument.id,
            facet.field_name == (field.storage_name or field.name),
            comparison,
        )
        .correlate(SearchDocument)
        .exists()
    )


def _compile_direct(expression: FieldExpression) -> ColumnElement[bool]:
    if expression.operator is not ComparisonOperator.EQUAL or isinstance(expression.value, RangeValue):
        return false()
    value = str(expression.value).casefold()
    return func.lower(SearchDocument.status) == value


def _comparison(
    column: ColumnElement[object],
    expression: FieldExpression,
    field_type: FieldType,
) -> ColumnElement[bool]:
    if isinstance(expression.value, RangeValue):
        lower = _database_value(expression.value.lower, field_type)
        upper = _database_value(expression.value.upper, field_type)
        return column.between(lower, upper)
    value = _database_value(expression.value, field_type)
    return {
        ComparisonOperator.EQUAL: column == value,
        ComparisonOperator.LESS_THAN: column < value,
        ComparisonOperator.LESS_THAN_OR_EQUAL: column <= value,
        ComparisonOperator.GREATER_THAN: column > value,
        ComparisonOperator.GREATER_THAN_OR_EQUAL: column >= value,
    }[expression.operator]


def _database_value(value: str | int | float | Decimal | bool, field_type: FieldType) -> str | Decimal | bool | Instant:
    if field_type is FieldType.NUMBER:
        return Decimal(str(value))
    if field_type is FieldType.TIMESTAMP:
        parsed = datetime.fromisoformat(str(value))
        # Dates and timestamps without an explicit offset are interpreted as UTC.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return Instant(parsed)
    if field_type is FieldType.BOOLEAN:
        return bool(value)
    return str(value)


def _text_query(value: str, *, phrase: bool) -> ColumnElement[object]:
    query_function = func.phraseto_tsquery if phrase else func.plainto_tsquery
    return query_function("simple", value)


def _fuzzy_text_predicate(
    vector: ColumnElement[object],
    fuzzy_column: ColumnElement[object],
    value: str,
    *,
    phrase: bool,
    exact_column: ColumnElement[object],
) -> ColumnElement[bool]:
    return or_(
        func.lower(exact_column) == value.casefold(),
        vector.bool_op("@@")(_text_query(value, phrase=phrase)),
        func.similarity(fuzzy_column, value) > 0.1,
    )
