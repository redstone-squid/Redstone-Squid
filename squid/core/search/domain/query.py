"""Represent a parsed search query independently of the database.

TextExpression searches the combined document, while FieldExpression selects a
named field such as width. BooleanExpression and NotExpression combine those
clauses into a tree. The parser resolves each field name to its definition and
converts values before the backend compiles the tree into a filter.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from squid.core.errors import ErrorCode, ValidationError
from squid.core.i18n import tr
from squid.core.search.domain.fields import FieldDefinition
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

CREATOR_ID_STORAGE = "creator_id"
"""Where builds index the public IDs of their credited creators."""


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
                return CREATOR_ID_STORAGE, str(UUID(self.value))
            except ValueError:
                pass
        return self.field.storage_name or self.field.name, self.value

    @property
    def ranked(self) -> bool:
        """Whether the value also counts as text for relevance ranking. A creator ID does not."""
        return self.field.ranked and self.stored()[0] != CREATOR_ID_STORAGE

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


type QueryExpression = TextExpression | FieldExpression | PresenceExpression | NotExpression | BooleanExpression


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
