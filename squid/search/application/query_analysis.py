"""Analysis helpers shared by search ranking adapters."""

from squid.search.domain import (
    BooleanExpression,
    FieldExpression,
    NotExpression,
    QueryExpression,
    SearchQuery,
    TextExpression,
)


def positive_text_expressions(query: SearchQuery) -> tuple[TextExpression | FieldExpression, ...]:
    """Return text clauses and clauses on ranked fields outside an odd number of NOT operators.

    These clauses supply ranking terms only. Their original AND/OR relationships
    are discarded here; the backend must still apply the complete query to filter
    results. For example, `piston NOT slime` contributes only `piston`.
    """
    expressions: list[TextExpression | FieldExpression] = []
    if query.expression is not None:
        _collect_positive_text(query.expression, expressions, negated=False)
    return tuple(expressions)


def is_filter_only(query: SearchQuery) -> bool:
    """Return whether a query has no positive text that should affect rank."""
    return not positive_text_expressions(query)


def _collect_positive_text(
    expression: QueryExpression,
    output: list[TextExpression | FieldExpression],
    *,
    negated: bool,
) -> None:
    if isinstance(expression, NotExpression):
        _collect_positive_text(expression.operand, output, negated=not negated)
    elif isinstance(expression, BooleanExpression):
        for operand in expression.operands:
            _collect_positive_text(operand, output, negated=negated)
    elif negated:
        return
    elif isinstance(expression, TextExpression) or expression.field.ranked:
        output.append(expression)
