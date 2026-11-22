"""Turn parsed search expressions into SQLAlchemy predicates with bound values."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import and_, false, func, not_, null, or_, select, true
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement, SQLColumnExpression
from sqlalchemy.sql.selectable import ScalarSelect
from whenever import Instant

from squid.core.search.domain import (
    BooleanExpression,
    BooleanOperator,
    BuildQuery,
    ComparisonOperator,
    FieldExpression,
    FieldType,
    NotExpression,
    PresenceExpression,
    QueryExpression,
    RangeValue,
    SearchQuery,
    TextExpression,
)
from squid.core.search.models import SearchDocument, SearchDocumentFilter

_DIRECT_TEXT_FIELDS = frozenset({"status"})
_TAG_NAME_FIELDS = frozenset({"tag", "restriction", "type", "pattern"})
_VECTOR_FIELDS = {
    "title": SearchDocument.title_vector,
    "description": SearchDocument.description_vector,
}


def compile_query(query: SearchQuery) -> ColumnElement[bool]:
    """Compile a parsed query into a predicate on SearchDocument; an empty query matches every document.

    User text only ever reaches the statement as bound values. Column and field
    names come from the field definitions the parser resolved.
    """
    if query.expression is None:
        return true()
    expression = query.expression
    ordinary = _compile_expression(expression)
    if not has_holder_filters(query):
        return ordinary
    is_record = SearchDocument.resource_kind == "record"
    return or_(
        and_(not_(is_record), ordinary),
        and_(
            is_record,
            or_(
                matching_holder(query).is_not(None),
                _compile_expression(expression, on_record=True),
            ),
        ),
    )


def has_holder_filters(query: SearchQuery) -> bool:
    """Tell whether record matches need to be correlated to a holder."""

    def contains(expression: QueryExpression | None) -> bool:
        match expression:
            case BooleanExpression(operands=operands):
                return any(contains(operand) for operand in operands)
            case NotExpression(operand=operand):
                return contains(operand)
            case FieldExpression(field=field) | PresenceExpression(field=field):
                return field.holder_scoped
            case _:
                return False

    return not isinstance(query, BuildQuery) and contains(query.expression)


def matching_holder(query: SearchQuery) -> ScalarSelect[int | None]:
    """Select the first published holder that satisfies the record query."""
    holder = aliased(SearchDocumentFilter)
    return (
        select(holder.holder_build_id)
        .where(
            holder.document_id == SearchDocument.id,
            holder.field_name == "holder",
            holder.holder_build_id.is_not(None),
            compile_holder_query(query, holder.holder_build_id),
        )
        .order_by(holder.ordinal)
        .limit(1)
        .correlate(SearchDocument)
        .scalar_subquery()
    )


def compile_holder_query(query: SearchQuery, holder_id: SQLColumnExpression[int | None]) -> ColumnElement[bool]:
    """Match a record query for the given holder, keeping category clauses on the record."""
    if query.expression is None:
        return true()
    return _compile_expression(query.expression, on_record=True, holder_id=holder_id)


def _compile_expression(
    expression: QueryExpression,
    *,
    on_record: bool = False,
    holder_id: SQLColumnExpression[int | None] | None = None,
) -> ColumnElement[bool]:
    if isinstance(expression, TextExpression):
        return _fuzzy_text_predicate(
            cast(ColumnElement[object], SearchDocument.combined_vector),
            cast(ColumnElement[object], SearchDocument.fuzzy_text),
            expression.value,
            phrase=expression.phrase,
            exact_column=cast(ColumnElement[object], SearchDocument.normalized_title),
        )
    if isinstance(expression, NotExpression):
        return not_(_compile_expression(expression.operand, on_record=on_record, holder_id=holder_id))
    if isinstance(expression, BooleanExpression):
        operands = tuple(
            _compile_expression(operand, on_record=on_record, holder_id=holder_id) for operand in expression.operands
        )
        return and_(*operands) if expression.operator is BooleanOperator.AND else or_(*operands)
    if on_record and expression.field.holder_scoped:
        if holder_id is None:
            return cast(ColumnElement[bool], null())
        measurement = aliased(SearchDocumentFilter)
        # A scalar subquery preserves SQL unknown for absent measurements, even under NOT.
        value = (
            select(measurement.numeric_value)
            .where(
                measurement.document_id == SearchDocument.id,
                measurement.holder_build_id == holder_id,
                measurement.field_name == (expression.field.storage_name or expression.field.name),
            )
            .correlate_except(measurement)
            .scalar_subquery()
        )
        if isinstance(expression, PresenceExpression):
            return value.is_not(None) if expression.is_set else value.is_(None)
        return _comparison(cast(ColumnElement[object], value), expression, expression.field.value_type)
    if isinstance(expression, PresenceExpression):
        return _compile_presence(expression)
    return _compile_field(expression)


def _compile_presence(expression: PresenceExpression) -> ColumnElement[bool]:
    field = expression.field
    column = {
        "title": SearchDocument.title,
        "description": SearchDocument.description,
        "status": SearchDocument.status,
    }.get(field.name)
    if column is not None:
        present = func.nullif(func.btrim(column, " \t\r\n"), "").is_not(None)
    else:
        values = SearchDocumentFilter
        present = (
            select(values.document_id)
            .where(
                values.document_id == SearchDocument.id,
                values.holder_build_id.is_(None),
                values.field_name == (field.storage_name or field.name),
            )
            .correlate(SearchDocument)
            .exists()
        )
        if field.name == "tag":
            present = or_(present, func.cardinality(SearchDocument.tags) > 0)
    return present if expression.is_set else not_(present)


def _compile_field(expression: FieldExpression) -> ColumnElement[bool]:
    """Compare a document column or its indexed field values."""
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
    # Use a separate EXISTS for each clause: a document may satisfy two clauses
    # through different indexed values. EXISTS keeps each matching document unique.
    storage_name, stored_value = expression.stored()
    filter_value = SearchDocumentFilter
    value_column = {
        FieldType.TEXT: filter_value.text_value,
        FieldType.NUMBER: filter_value.numeric_value,
        FieldType.TIMESTAMP: filter_value.timestamp_value,
        FieldType.BOOLEAN: filter_value.boolean_value,
    }[field.value_type]
    if field.name in _TAG_NAME_FIELDS:
        if expression.operator is not ComparisonOperator.EQUAL or isinstance(expression.value, RangeValue):
            return false()
        value = " ".join(str(expression.value).split()).lower()
        comparison = func.lower(func.btrim(func.regexp_replace(value_column, r"\s+", " ", "g"))) == value
    elif field.name in {"kind", "orientation"}:
        comparison = func.lower(value_column) == str(expression.value).casefold()
    else:
        comparison = _comparison(
            cast(ColumnElement[object], value_column), replace(expression, value=stored_value), field.value_type
        )
    predicate = (
        select(filter_value.document_id)
        .where(
            filter_value.document_id == SearchDocument.id,
            filter_value.holder_build_id.is_(None),
            filter_value.field_name == storage_name,
            comparison,
        )
        .correlate(SearchDocument)
        .exists()
    )
    if field.name == "tag":
        # Keep matching the indexed creator/version terms that tag: historically accepted.
        return or_(predicate, SearchDocument.tags.bool_op("@>")([" ".join(str(expression.value).casefold().split())]))
    return predicate


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
