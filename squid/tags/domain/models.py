"""Domain values for unified build tags."""

from decimal import Decimal
from enum import StrEnum


class TagAuthority(StrEnum):
    """Who controls a tag definition."""

    OFFICIAL = "official"
    USER = "user"


class TagSemanticKind(StrEnum):
    """The behavior associated with a tag definition."""

    RESTRICTION = "restriction"
    PATTERN = "pattern"
    SHOWCASE = "showcase"


class TagValueType(StrEnum):
    """The scalar value attached to a tag."""

    NONE = "none"
    NUMERIC = "numeric"
    TEXT = "text"
    BOOLEAN = "boolean"


class RecordOperator(StrEnum):
    """How a tag assignment satisfies a record-category predicate."""

    PRESENT = "present"
    EXACT = "exact"
    AT_MOST = "at_most"
    AT_LEAST = "at_least"


class TagModerationStatus(StrEnum):
    """Publication state of a tag definition."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


type TagValue = Decimal | str | bool | None
