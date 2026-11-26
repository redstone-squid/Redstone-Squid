"""Turn parsed search expressions into SQLAlchemy predicates with bound values.

Bare text matches a document's weighted vector, and a clause on a field matches
that field's values. Words become tsquery terms, ORed with the close spellings in
the search's TermExpansion; a quoted phrase must appear in order within one value;
a keyword compares the whole normalized value. Negated clauses never use close
spellings, so `NOT slime` excludes only what was typed.
"""

from decimal import Decimal
from typing import cast

from sqlalchemy import and_, false, not_, null, or_, select, true
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement, SQLColumnExpression
from sqlalchemy.sql.selectable import ScalarSelect, Select

from squid.core.search.domain import (
    BooleanExpression,
    BooleanOperator,
    ComparisonOperator,
    FieldExpression,
    FieldType,
    NotExpression,
    PresenceExpression,
    QueryExpression,
    RangeValue,
    SearchQuery,
    TextExpression,
    TextMatch,
)
from squid.core.search.domain.query import has_holder_clauses, typed_value
from squid.core.search.domain.text import TermExpansion, normalize_text, terms
from squid.core.search.models import SearchDocument, SearchValue

_EXACT = TermExpansion()


def compile_query(query: SearchQuery, expansion: TermExpansion = _EXACT) -> ColumnElement[bool]:
    """Compile a parsed query into a predicate on SearchDocument; an empty query matches every document.

    User text only ever reaches the statement as bound values. Column and field
    names come from the field definitions the parser resolved.
    """
    if query.expression is None:
        return true()
    expression = query.expression
    ordinary = _Compiler(expansion).expression(expression)
    if not has_holder_clauses(query):
        return ordinary
    is_record = SearchDocument.resource_kind == "record"
    return or_(
        and_(not_(is_record), ordinary),
        and_(
            is_record,
            or_(
                matching_holder(query, expansion).is_not(None),
                _Compiler(expansion, on_record=True).expression(expression),
            ),
        ),
    )


def matching_holder(query: SearchQuery, expansion: TermExpansion = _EXACT) -> ScalarSelect[int | None]:
    """Select the first published holder that satisfies the record query."""
    holder = aliased(SearchValue)
    return (
        select(holder.holder_build_id)
        .where(
            holder.document_id == SearchDocument.id,
            holder.field_name == "holder",
            holder.holder_build_id.is_not(None),
            compile_holder_query(query, holder.holder_build_id, expansion),
        )
        .order_by(holder.ordinal)
        .limit(1)
        .correlate(SearchDocument)
        .scalar_subquery()
    )


def compile_holder_query(
    query: SearchQuery, holder_id: SQLColumnExpression[int | None], expansion: TermExpansion = _EXACT
) -> ColumnElement[bool]:
    """Match a record query for the given holder, keeping category clauses on the record."""
    if query.expression is None:
        return true()
    return _Compiler(expansion, on_record=True, holder_id=holder_id).expression(query.expression)


class _Compiler:
    def __init__(
        self,
        expansion: TermExpansion,
        *,
        on_record: bool = False,
        holder_id: SQLColumnExpression[int | None] | None = None,
    ) -> None:
        self._expansion = expansion
        self._on_record = on_record
        self._holder_id = holder_id

    def expression(self, expression: QueryExpression, *, negated: bool = False) -> ColumnElement[bool]:
        if isinstance(expression, TextExpression):
            query = text_query(expression.value, phrase=expression.phrase, expansion=self._fuzzy(negated))
            return true() if query is None else SearchDocument.search_vector.bool_op("@@")(query)
        if isinstance(expression, NotExpression):
            return not_(self.expression(expression.operand, negated=not negated))
        if isinstance(expression, BooleanExpression):
            operands = tuple(self.expression(operand, negated=negated) for operand in expression.operands)
            return and_(*operands) if expression.operator is BooleanOperator.AND else or_(*operands)
        if self._on_record and expression.field.holder_scoped:
            return self._holder_measurement(expression)
        if isinstance(expression, PresenceExpression):
            present = _values(expression.field.storage).exists()
            return present if expression.is_set else not_(present)
        return self._field(expression, negated=negated)

    def _fuzzy(self, negated: bool) -> TermExpansion:
        return _EXACT if negated else self._expansion

    def _holder_measurement(self, expression: FieldExpression | PresenceExpression) -> ColumnElement[bool]:
        if self._holder_id is None:
            return cast(ColumnElement[bool], null())
        measurement = aliased(SearchValue)
        # A scalar subquery preserves SQL unknown for absent measurements, even under NOT.
        value = (
            select(measurement.numeric_value)
            .where(
                measurement.document_id == SearchDocument.id,
                measurement.holder_build_id == self._holder_id,
                measurement.field_name == expression.field.storage,
            )
            .correlate_except(measurement)
            .scalar_subquery()
        )
        if isinstance(expression, PresenceExpression):
            return value.is_not(None) if expression.is_set else value.is_(None)
        return _comparison(cast(ColumnElement[object], value), expression, expression.field.value_type)

    def _field(self, expression: FieldExpression, *, negated: bool) -> ColumnElement[bool]:
        """Compare one field's values. Each clause gets its own EXISTS: a document may
        satisfy two clauses through different values of the same field."""
        field = expression.field
        storage, stored_value = expression.stored()
        if field.value_type is FieldType.TEXT:
            if expression.operator is not ComparisonOperator.EQUAL or isinstance(stored_value, RangeValue):
                return false()
            if field.text_match is TextMatch.WORDS:
                query = text_query(str(stored_value), phrase=expression.phrase, expansion=self._fuzzy(negated))
                if query is None:
                    return true()
                return _values(storage, SearchValue.term_vector.bool_op("@@")(query)).exists()
            return _values(storage, SearchValue.normalized_text == normalize_text(str(stored_value))).exists()
        column = {
            FieldType.NUMBER: SearchValue.numeric_value,
            FieldType.TIMESTAMP: SearchValue.timestamp_value,
            FieldType.BOOLEAN: SearchValue.boolean_value,
        }[field.value_type]
        comparison = _comparison(cast(ColumnElement[object], column), expression, field.value_type, stored_value)
        return _values(storage, comparison).exists()


def text_query(value: str, *, phrase: bool, expansion: TermExpansion) -> ColumnElement[object] | None:
    """Build the tsquery for a word, words or a phrase; None when the text has no terms.

    Text without terms, such as a lone symbol, constrains nothing.
    """
    value_terms = terms(value)
    if not value_terms:
        return None
    if phrase:
        source = " <-> ".join(f"'{term}'" for term in value_terms)
    else:
        source = " & ".join(
            "(" + " | ".join(f"'{match}'" for match in sorted(expansion.matches(term))) + ")" for term in value_terms
        )
    return sql_cast(source, TSQUERY)


def rank_query(value: str) -> ColumnElement[object] | None:
    """Build a tsquery matching any exact term of `value`, for ranking; None without terms."""
    value_terms = sorted(set(terms(value)))
    if not value_terms:
        return None
    return sql_cast(" | ".join(f"'{term}'" for term in value_terms), TSQUERY)


def _values(storage: str, *conditions: ColumnElement[bool]) -> Select[int]:
    """Select the document's own values of a field, leaving out record holders' values."""
    return (
        select(SearchValue.document_id)
        .where(
            SearchValue.document_id == SearchDocument.id,
            SearchValue.holder_build_id.is_(None),
            SearchValue.field_name == storage,
            *conditions,
        )
        .correlate(SearchDocument)
    )


def _comparison(
    column: ColumnElement[object],
    expression: FieldExpression,
    field_type: FieldType,
    compared: object = None,
) -> ColumnElement[bool]:
    """Compare a column with the clause's value, or with `compared` when the stored value differs."""
    target = expression.value if compared is None else compared
    if isinstance(target, RangeValue):
        lower = typed_value(target.lower, field_type)
        upper = typed_value(target.upper, field_type)
        return column.between(lower, upper)
    assert isinstance(target, str | int | float | Decimal | bool)
    value = typed_value(target, field_type)
    return {
        ComparisonOperator.EQUAL: column == value,
        ComparisonOperator.LESS_THAN: column < value,
        ComparisonOperator.LESS_THAN_OR_EQUAL: column <= value,
        ComparisonOperator.GREATER_THAN: column > value,
        ComparisonOperator.GREATER_THAN_OR_EQUAL: column >= value,
    }[expression.operator]
