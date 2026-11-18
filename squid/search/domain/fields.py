"""Definitions of the fields that search queries can filter and sort by."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from squid.search.domain.scope import CATALOGUE_SCOPES, SearchScope


class FieldType(StrEnum):
    """Storage-independent field value types."""

    TEXT = "text"
    NUMBER = "number"
    TIMESTAMP = "timestamp"
    BOOLEAN = "boolean"


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    """A queryable field exposed by the search API."""

    name: str
    value_type: FieldType
    supports_range: bool = False
    aliases: tuple[str, ...] = ()
    storage_name: str | None = None
    """Facet key used in storage, such as `tag:42`; None uses the public name."""
    supports_sort: bool = False
    ranked: bool = False
    """Whether the value of a clause on this field also counts as text for relevance ranking."""
    unit_scales: tuple[tuple[str, Decimal], ...] = ()
    """Accepted suffixes and multipliers that convert values to the canonical unit."""
    numeric_step: Decimal | None = None
    suggested_values: tuple[str, ...] | None = None
    """Fixed text completions; None reads indexed values, and an empty tuple disables suggestions.

    These are completion hints, not a restriction on accepted query values.
    """
    scopes: frozenset[SearchScope] = frozenset({SearchScope.BUILDS})
    """Catalogue tabs that can filter on this field."""
    value_enum: type[StrEnum] | None = None
    scoped_enums: tuple[tuple[SearchScope, type[StrEnum]], ...] = ()
    value_scopes: tuple[tuple[str, SearchScope], ...] = ()

    def applies_to(self, scope: SearchScope, value: object = None) -> bool:
        """Check the field and any scope restriction on a particular value."""
        if scope not in CATALOGUE_SCOPES:
            return True
        return scope in self.scopes and all(
            value != restricted or scope is allowed for restricted, allowed in self.value_scopes
        )

    def choices(self, scope: SearchScope) -> tuple[str, ...] | None:
        if self.suggested_values is None:
            return None
        return tuple(value for value in self.suggested_values if self.applies_to(scope, value))

    def accepts(self, value: object, scope: SearchScope) -> bool:
        """Validate a value on a resolved field when constructing a query tree."""
        if self.scoped_enums or not self.applies_to(scope, value):
            return False
        if self.value_enum is not None:
            return isinstance(value, self.value_enum)
        match self.value_type:
            case FieldType.TEXT | FieldType.TIMESTAMP:
                return isinstance(value, str)
            case FieldType.NUMBER:
                return isinstance(value, int | float | Decimal) and not isinstance(value, bool)
            case FieldType.BOOLEAN:
                return isinstance(value, bool)
