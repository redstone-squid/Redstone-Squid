"""Analysis helpers shared by search ranking adapters."""

from squid.search.domain import (
    BooleanExpression,
    FieldExpression,
    NotExpression,
    QueryExpression,
    SearchQuery,
    TextExpression,
)

_RANKED_TEXT_FIELDS = frozenset({"title", "description", "tag", "restriction", "type", "pattern", "creator"})


def positive_text_expressions(query: SearchQuery) -> tuple[TextExpression | FieldExpression, ...]:
    """Return positive text clauses usable for ranking after full Boolean filtering."""
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
    elif isinstance(expression, TextExpression) or expression.field in _RANKED_TEXT_FIELDS:
        output.append(expression)
