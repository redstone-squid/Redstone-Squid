"""Represent a parsed search query independently of the database.

TextExpression searches the combined document, while FieldExpression selects a
named field such as width. BooleanExpression and NotExpression combine those
clauses into a tree. The parser resolves each field name to its definition and
converts values before the backend compiles the tree into a filter.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from whenever import Instant

from squid.core.errors import ErrorCode, ValidationError
from squid.core.i18n import tr
from squid.core.search.domain.fields import FieldDefinition, FieldType
from squid.core.search.domain.scope import CATALOGUE_SCOPES, SearchScope
from squid_i18n import md_tr


class BooleanOperator(StrEnum):
    """Boolean operators supported by search."""

    AND = "and"
    OR = "or"


class ComparisonOperator(StrEnum):
    """Allowlisted field comparison operators."""

    EQUAL = "eq"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"


type ScalarValue = str | int | float | Decimal | bool

ACCOUNT_ID_STORAGE = "account_id"
"""Where builds index the public IDs of the accounts they credit."""


@dataclass(frozen=True, slots=True)
class RangeValue:
    """An inclusive field range."""

    lower: ScalarValue
    upper: ScalarValue


@dataclass(frozen=True, slots=True)
class TextExpression:
    """Unqualified text matched against the combined search document."""

    value: str
    phrase: bool = False


@dataclass(frozen=True, slots=True)
class FieldExpression:
    """A typed comparison against a registered field."""

    field: FieldDefinition
    operator: ComparisonOperator
    value: ScalarValue | RangeValue
    phrase: bool = False

    def stored(self) -> tuple[str, ScalarValue | RangeValue]:
        """Return the storage key and value to compare.

        `creator:` takes a credited name or a public creator ID. IDs are stored apart from
        names, so crediting a build to the text of someone's ID does not list it as theirs.
        """
        if self.field.name == "creator" and isinstance(self.value, str):
            try:
                return ACCOUNT_ID_STORAGE, str(UUID(self.value))
            except ValueError:
                pass
        return self.field.storage_name or self.field.name, self.value

    @property
    def ranked(self) -> bool:
        """Whether the value also counts as text for relevance ranking. A creator ID does not."""
        return self.field.ranked and self.stored()[0] != ACCOUNT_ID_STORAGE

    def applies_to(self, scope: SearchScope) -> bool:
        if isinstance(self.value, RangeValue):
            return (
                self.field.supports_range
                and self.operator is ComparisonOperator.EQUAL
                and self.field.accepts(self.value.lower, scope)
                and self.field.accepts(self.value.upper, scope)
            )
        return (self.operator is ComparisonOperator.EQUAL or self.field.supports_range) and self.field.accepts(
            self.value, scope
        )


@dataclass(frozen=True, slots=True)
class PresenceExpression:
    """Check whether a field has a value."""

    field: FieldDefinition
    is_set: bool

    def applies_to(self, scope: SearchScope) -> bool:
        return self.field.applies_to(scope)


@dataclass(frozen=True, slots=True)
class NotExpression:
    """Negation of an expression."""

    operand: QueryExpression


@dataclass(frozen=True, slots=True)
class BooleanExpression:
    """Two or more expressions joined by the same Boolean operator."""

    operator: BooleanOperator
    operands: tuple[QueryExpression, ...]

    def __post_init__(self) -> None:
        if len(self.operands) < 2:
            msg = tr(t"search-boolean-minimum-operands = Boolean expressions require at least two operands")
            raise ValidationError(msg)

    def switched_selection(self, index: int) -> QueryExpression | None:
        """Switch sibling equality filters on one multi-valued field between AND and OR.

        An AND group may also contain unrelated constraints after normalization.
        Only the first selected value owns the switch.
        """
        first = self.operands[index]
        if not isinstance(first, FieldExpression) or not first.field.multi_valued:
            return None
        indices = [
            i
            for i, item in enumerate(self.operands)
            if isinstance(item, FieldExpression)
            and item.field == first.field
            and item.operator is ComparisonOperator.EQUAL
            and not isinstance(item.value, RangeValue)
        ]
        if len(indices) < 2 or index != indices[0]:
            return None
        if self.operator is BooleanOperator.OR and len(indices) != len(self.operands):
            return None
        other = BooleanOperator.OR if self.operator is BooleanOperator.AND else BooleanOperator.AND
        group = BooleanExpression(other, tuple(self.operands[i] for i in indices))
        return combine_clauses(
            self.operator,
            *(group if i == index else item for i, item in enumerate(self.operands) if i == index or i not in indices),
        )


type QueryExpression = TextExpression | FieldExpression | PresenceExpression | NotExpression | BooleanExpression


@dataclass(frozen=True, slots=True)
class QueryClause:
    """One leaf of a parsed query, as `query_leaves` returns it, and how to remove it.

    `without` is the normalized query left after removing the clause, so a client can
    drop a filter from inside an OR group without editing query text.
    """

    expression: QueryExpression
    text: str
    without: str


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A parsed query and its normalized spelling; expression is None for blank input."""

    expression: QueryExpression | None
    normalized: str


@dataclass(frozen=True, slots=True)
class ScopedSearchQuery(SearchQuery):
    """A tree validated against the capabilities of one catalogue index."""

    scope: ClassVar[SearchScope]

    def __post_init__(self) -> None:
        if self.expression is not None and not expression_applies_to(self.expression, self.scope):
            scope = self.scope.value
            raise ValidationError(
                md_tr(t"search-query-incompatible-scope = This query contains filters that cannot be used in {scope}."),
                code=ErrorCode.INVALID_QUERY,
            )


@dataclass(frozen=True, slots=True)
class BuildQuery(ScopedSearchQuery):
    scope = SearchScope.BUILDS


@dataclass(frozen=True, slots=True)
class RecordQuery(ScopedSearchQuery):
    scope = SearchScope.RECORDS


def expression_applies_to(expression: QueryExpression, scope: SearchScope) -> bool:
    """Check the whole subtree, including branches inside OR and NOT."""
    if scope not in CATALOGUE_SCOPES:
        return True
    match expression:
        case BooleanExpression(operands=operands):
            return all(expression_applies_to(operand, scope) for operand in operands)
        case NotExpression(operand=operand):
            return expression_applies_to(operand, scope)
        case TextExpression():
            return True
        case FieldExpression() | PresenceExpression():
            return expression.applies_to(scope)


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
        if isinstance(expression.operand, FieldExpression | PresenceExpression | TextExpression):
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


def has_holder_clauses(query: SearchQuery) -> bool:
    """Tell whether record matches need to be correlated to one holder."""

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


def typed_value(value: ScalarValue, field_type: FieldType) -> str | Decimal | bool | Instant:
    """Convert a query value to the type its field stores.

    Dates and timestamps without an explicit offset are UTC.
    """
    if field_type is FieldType.NUMBER:
        return Decimal(str(value))
    if field_type is FieldType.TIMESTAMP:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return Instant(parsed)
    if field_type is FieldType.BOOLEAN:
        return bool(value)
    return str(value)
