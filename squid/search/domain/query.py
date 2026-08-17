"""Typed search query syntax tree."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias

from squid.core.errors import ValidationError
from squid.core.i18n import _


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


ScalarValue: TypeAlias = str | int | float | Decimal | bool


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

    field: str
    operator: ComparisonOperator
    value: ScalarValue | RangeValue
    phrase: bool = False
    storage_field: str | None = None
    value_type: str | None = None


@dataclass(frozen=True, slots=True)
class NotExpression:
    """Negation of an expression."""

    operand: QueryExpression


@dataclass(frozen=True, slots=True)
class BooleanExpression:
    """A flattened Boolean combination."""

    operator: BooleanOperator
    operands: tuple[QueryExpression, ...]

    def __post_init__(self) -> None:
        if len(self.operands) < 2:
            msg = _("Boolean expressions require at least two operands")
            raise ValidationError(msg)


QueryExpression: TypeAlias = TextExpression | FieldExpression | NotExpression | BooleanExpression


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Parsed query and its normalized representation."""

    expression: QueryExpression | None
    normalized: str
