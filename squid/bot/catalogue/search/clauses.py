"""Edit parsed catalogue queries without losing their Boolean grouping."""

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Self

from squid.core.records.domain import BaseTitle, VersionScope
from squid.core.search.application.parser import SearchQueryParser
from squid.core.search.domain.query import (
    BooleanExpression,
    BooleanOperator,
    BuildQuery,
    ComparisonOperator,
    FieldExpression,
    NotExpression,
    QueryExpression,
    RecordQuery,
    TextExpression,
    expression_applies_to,
)
from squid.core.search.domain.scope import SearchScope


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

    def preference_filters(self, parser: SearchQueryParser) -> str:
        """Keep field-only conjuncts. A mixed OR group belongs to this search as a whole."""
        expression = self.parsed.expression
        if expression is None:
            return ""
        terms = (
            expression.operands
            if isinstance(expression, BooleanExpression) and expression.operator is BooleanOperator.AND
            else (expression,)
        )
        filters = combine_clauses(
            BooleanOperator.AND,
            *(
                term
                for term in terms
                if all(
                    isinstance(leaf.operand if isinstance(leaf, NotExpression) else leaf, FieldExpression)
                    for _, leaf in query_leaves(term)
                )
            ),
        )
        return parser.render(filters) if filters is not None else ""

    def with_expression(self, parser: SearchQueryParser, expression: QueryExpression | None) -> CatalogueQuery:
        if expression == self.parsed.expression and self.parsed.scope is parser.scope:
            return self
        parsed = parser.query(expression)
        assert isinstance(parsed, BuildQuery | RecordQuery)
        return CatalogueQuery(parsed)

    @property
    def record_titles(self) -> frozenset[BaseTitle] | None:
        """Return selected titles, or None when the buttons cannot edit the expression."""
        selection = self._title_filter()
        return selection[1] if selection is not None else None

    def _title_filter(self) -> tuple[tuple[int, ...] | None, frozenset[BaseTitle]] | None:
        return _record_filter(
            self.parsed.expression, "base_title", (BaseTitle.SMALLEST, BaseTitle.FASTEST, BaseTitle.FIRST)
        )

    @property
    def record_versions(self) -> frozenset[VersionScope] | None:
        selection = self._version_filter()
        if selection is None:
            return None
        return selection[1] or frozenset({VersionScope.ALL_TIME})

    def _version_filter(self) -> tuple[tuple[int, ...] | None, frozenset[VersionScope]] | None:
        return _record_filter(self.parsed.expression, "version_scope", tuple(VersionScope))

    def toggle_record_title(self, parser: SearchQueryParser, title: BaseTitle | None) -> CatalogueQuery | None:
        """Toggle one title, or clear the title selection when given None."""
        selection = self._title_filter()
        if selection is None:
            return None
        path, titles = selection
        titles = titles ^ {title} if title is not None else frozenset()
        field = parser.registry.resolve("base_title")
        assert field is not None
        clauses = (FieldExpression(field, ComparisonOperator.EQUAL, item) for item in sorted(titles))
        return self._replace_filter(parser, path, combine_clauses(BooleanOperator.OR, *clauses))

    def select_record_version(self, parser: SearchQueryParser, version: VersionScope) -> CatalogueQuery | None:
        selection = self._version_filter()
        if selection is None:
            return None
        path, versions = selection
        if (versions or {VersionScope.ALL_TIME}) == {version}:
            return None
        field = parser.registry.resolve("version_scope")
        assert field is not None
        replacement = (
            FieldExpression(field, ComparisonOperator.EQUAL, version) if version is not VersionScope.ALL_TIME else None
        )
        return self._replace_filter(parser, path, replacement)

    def _replace_filter(
        self, parser: SearchQueryParser, path: tuple[int, ...] | None, replacement: QueryExpression | None
    ) -> CatalogueQuery:
        expression = self.parsed.expression
        if expression is None:
            expression = replacement
        elif path is not None:
            expression = replace_clause(expression, path, replacement)
        elif replacement is not None:
            expression = combine_clauses(BooleanOperator.AND, expression, replacement)
        return self.with_expression(parser, expression)

    def effective_query(self, parser: SearchQueryParser) -> BuildQuery | RecordQuery:
        """Apply the implicit all-time record scope while preserving the displayed query."""
        if isinstance(self.parsed, RecordQuery):
            selection = self._version_filter()
            if selection is not None and selection[0] is None:
                field = parser.registry.resolve("version_scope")
                assert field is not None
                default = FieldExpression(field, ComparisonOperator.EQUAL, VersionScope.ALL_TIME)
                return self._replace_filter(parser, None, default).parsed
        return self.parsed

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


def _record_filter[ValueT: StrEnum](
    expression: QueryExpression | None, field: str, allowed: tuple[ValueT, ...]
) -> tuple[tuple[int, ...] | None, frozenset[ValueT]] | None:
    """Find the single clause or OR group that the record buttons can edit."""
    if expression is None:
        return None, frozenset()
    conjuncts = (
        tuple(((index,), clause) for index, clause in enumerate(expression.operands))
        if isinstance(expression, BooleanExpression) and expression.operator is BooleanOperator.AND
        else (((), expression),)
    )
    path = None
    values: set[ValueT] = set()
    for clause_path, clause in conjuncts:
        alternatives = (
            clause.operands
            if isinstance(clause, BooleanExpression) and clause.operator is BooleanOperator.OR
            else (clause,)
        )
        if not any(
            isinstance(leaf, FieldExpression) and leaf.field.name == field
            for _, item in query_leaves(clause)
            for leaf in (item.operand if isinstance(item, NotExpression) else item,)
        ):
            continue
        if path is not None:
            return None
        for item in alternatives:
            if (
                not isinstance(item, FieldExpression)
                or item.field.name != field
                or item.operator is not ComparisonOperator.EQUAL
            ):
                return None
            value = next((value for value in allowed if value == item.value), None)
            if value is None:
                return None
            values.add(value)
        path = clause_path
    return path, frozenset(values)
