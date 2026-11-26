"""Analysis helpers shared by search ranking adapters."""

from squid.core.search.domain import (
    BooleanExpression,
    FieldExpression,
    NotExpression,
    PresenceExpression,
    QueryExpression,
    SearchQuery,
    TextExpression,
    TextMatch,
)
from squid.core.search.domain.text import close_distance, terms


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
    elif isinstance(expression, TextExpression) or (isinstance(expression, FieldExpression) and expression.ranked):
        output.append(expression)


def close_match_terms(query: SearchQuery) -> frozenset[str]:
    """Return the unquoted, non-negated query terms that may match close spellings."""
    found: set[str] = set()
    for expression in positive_text_expressions(query):
        if isinstance(expression, FieldExpression) and expression.field.text_match is not TextMatch.WORDS:
            continue
        if expression.phrase:
            continue
        value = expression.value if isinstance(expression, TextExpression) else str(expression.value)
        found.update(term for term in terms(value) if close_distance(term))
    return frozenset(found)


def mentions_field(query: SearchQuery, name: str) -> bool:
    """Tell whether any clause, including negated ones, names the field."""

    def visit(expression: QueryExpression | None) -> bool:
        match expression:
            case BooleanExpression(operands=operands):
                return any(visit(operand) for operand in operands)
            case NotExpression(operand=operand):
                return visit(operand)
            case FieldExpression(field=field) | PresenceExpression(field=field):
                return field.name == name
            case _:
                return False

    return visit(query.expression)
