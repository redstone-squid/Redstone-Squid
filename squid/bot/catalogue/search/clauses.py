"""Edit parsed catalogue queries without losing their Boolean grouping."""

from dataclasses import dataclass, replace
from typing import Self

from squid.search.application.parser import SearchQueryParser
from squid.search.domain.query import (
    BooleanExpression,
    BooleanOperator,
    BuildQuery,
    FieldExpression,
    NotExpression,
    QueryExpression,
    RecordQuery,
    TextExpression,
    expression_applies_to,
)
from squid.search.domain.scope import SearchScope


@dataclass(frozen=True, slots=True)
class CatalogueQuery:
    """The parsed query and, until a filter changes it, the user's exact input."""

    parsed: BuildQuery | RecordQuery = BuildQuery(None, "")
    source: str | None = None

    @classmethod
    def from_text(cls, parser: SearchQueryParser, text: str, *, typed: bool = True) -> Self:
        parsed = parser.parse(text)
        assert isinstance(parsed, BuildQuery | RecordQuery)
        return cls(parsed, source=text if typed else None)

    @property
    def text(self) -> str:
        return self.source if self.source is not None else self.parsed.normalized

    def with_expression(self, parser: SearchQueryParser, expression: QueryExpression | None) -> CatalogueQuery:
        if expression == self.parsed.expression and self.parsed.scope is parser.scope:
            return self
        parsed = parser.query(expression)
        assert isinstance(parsed, BuildQuery | RecordQuery)
        return CatalogueQuery(parsed)

    def with_shared_from(self, parser: SearchQueryParser, source: CatalogueQuery) -> CatalogueQuery:
        """Take the other tab's shared clauses while keeping this tab's local clauses."""
        shared, local = self._split()
        incoming, incoming_local = source._split()
        if not local and not incoming_local:
            query = self.with_expression(parser, source.parsed.expression)
            raw = source.source
            # In Records, leading title words can have a different meaning from Builds.
            if raw is not None and parser.parse(raw).expression == query.parsed.expression:
                return replace(query, source=raw)
            return query
        if shared == incoming:
            return self
        return self.with_expression(parser, combine_clauses(BooleanOperator.AND, *incoming, *local))

    def _split(self) -> tuple[tuple[QueryExpression, ...], tuple[QueryExpression, ...]]:
        expression = self.parsed.expression
        if expression is None:
            return (), ()
        clauses = (
            expression.operands
            if isinstance(expression, BooleanExpression) and expression.operator is BooleanOperator.AND
            else (expression,)
        )
        shared: list[QueryExpression] = []
        local: list[QueryExpression] = []
        other_scope = SearchScope.RECORDS if isinstance(self.parsed, BuildQuery) else SearchScope.BUILDS
        for clause in clauses:
            (shared if expression_applies_to(clause, other_scope) else local).append(clause)
        return tuple(shared), tuple(local)


def combine_clauses(operator: BooleanOperator, *clauses: QueryExpression) -> QueryExpression | None:
    """Join clauses, flattening adjacent groups that use the same operator."""
    operands = tuple(
        operand
        for clause in clauses
        for operand in (
            clause.operands if isinstance(clause, BooleanExpression) and clause.operator is operator else (clause,)
        )
    )
    return BooleanExpression(operator, operands) if len(operands) > 1 else next(iter(operands), None)


def query_leaves(
    expression: QueryExpression, path: tuple[int, ...] = ()
) -> tuple[tuple[tuple[int, ...], QueryExpression], ...]:
    """Keep each clause's address so edits preserve its surrounding Boolean operators."""
    if isinstance(expression, BooleanExpression):
        return tuple(
            leaf for index, item in enumerate(expression.operands) for leaf in query_leaves(item, (*path, index))
        )
    if isinstance(expression, NotExpression):
        if isinstance(expression.operand, FieldExpression | TextExpression):
            return ((path, expression),)
        return query_leaves(expression.operand, (*path, 0))
    return ((path, expression),)


def replace_clause(
    expression: QueryExpression,
    path: tuple[int, ...],
    replacement: QueryExpression | None,
) -> QueryExpression | None:
    """Replace or remove a leaf, collapsing empty and single-child groups."""
    if not path:
        return replacement
    if isinstance(expression, NotExpression):
        child = replace_clause(expression.operand, path[1:], replacement)
        return NotExpression(child) if child is not None else None
    assert isinstance(expression, BooleanExpression)
    children: list[QueryExpression] = []
    for index, item in enumerate(expression.operands):
        child = replace_clause(item, path[1:], replacement) if index == path[0] else item
        if child is not None:
            children.append(child)
    return combine_clauses(expression.operator, *children)
