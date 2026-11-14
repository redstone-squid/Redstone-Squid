"""Definitions of the fields that search queries can filter and sort by."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


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
