"""Domain values for unified build tags."""

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class TagDefinition:
    """Application-facing tag metadata."""

    id: int
    stable_key: str
    display_name: str
    authority: TagAuthority
    semantic_kind: TagSemanticKind
    value_type: TagValueType
    moderation_status: TagModerationStatus
    query_name: str | None = None
    restriction_type: str | None = None
    record_operator: RecordOperator | None = None
    canonical_unit: str | None = None
    default_display_unit: str | None = None
    numeric_step: Decimal | None = None
    render_template: str = "{name}"
    default_display_order: int = 0


@dataclass(frozen=True, slots=True)
class TagAssignment:
    """One tag and its strongest declared value on a build."""

    definition: TagDefinition
    value: TagValue = None
    display_unit: str | None = None
    display_order: int | None = None
    evidence: str | None = None
    provenance: str = "submitted"
