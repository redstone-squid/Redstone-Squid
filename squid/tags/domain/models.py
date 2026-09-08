"""Domain values for unified build tags."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class TagAuthority(StrEnum):
    """Whether staff or a member defined the tag; only OFFICIAL tags may be restrictions or patterns."""

    OFFICIAL = "official"
    USER = "user"


class TagSemanticKind(StrEnum):
    """What a tag asserts: a restriction the build obeys, a pattern it uses, or a free-form showcase label."""

    RESTRICTION = "restriction"
    PATTERN = "pattern"
    SHOWCASE = "showcase"


class TagValueType(StrEnum):
    """The type of value an assignment carries; NONE tags are bare labels."""

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
    """Publication state; only APPROVED definitions are searchable and assignable."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


type TagValue = Decimal | str | bool | None


@dataclass(frozen=True, slots=True)
class TagDefinition:
    """A tag as the application sees it; the unit and step fields are only ever set on numeric tags."""

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
    """One tag on one build, with the value declared for it; a build carries at most one assignment per tag."""

    definition: TagDefinition
    value: TagValue = None
    display_unit: str | None = None
    display_order: int | None = None
    evidence: str | None = None
    provenance: str = "submitted"
