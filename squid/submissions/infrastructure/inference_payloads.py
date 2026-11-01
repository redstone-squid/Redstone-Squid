"""The stored inference draft format, independent of live mapped entities."""

import uuid
from dataclasses import dataclass, field, fields
from decimal import Decimal

from whenever import Instant

from squid.builds.domain import (
    BuildCategory,
    BuildDraft,
    BuildLink,
    DoorOrientationLiteral,
    Info,
    MediaTypeLiteral,
    RecordCategoryLiteral,
    SourceMessage,
    Status,
)
from squid.sponsors import PublicSponsor
from squid.tags.domain import RecordOperator, TagAuthority, TagModerationStatus, TagSemanticKind, TagValue, TagValueType
from squid.tags.models import TagAssignment, TagDefinition


@dataclass(frozen=True)
class BuildLinkPayload:
    """A media link captured in an inference result."""

    url: str
    media_type: MediaTypeLiteral


@dataclass(frozen=True, slots=True)
class TagDefinitionPayload:
    """Tag metadata captured when inference completed."""

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
class TagAssignmentPayload:
    """A tag assignment captured in an inference result."""

    definition: TagDefinitionPayload
    value: TagValue = None
    display_unit: str | None = None
    display_order: int | None = None
    evidence: str | None = None
    provenance: str = "submitted"


@dataclass(kw_only=True)
class BuildDraftPayload:
    """A mutable pre-category accumulator for guided submission and inference.

    Every field is optional so transports can fill it progressively; nothing is
    validated until :meth:`finalize` produces the category subclass.
    """

    category: BuildCategory | None = None
    submission_status: Status | None = None
    record_category: RecordCategoryLiteral | None = None
    versions: list[str] = field(default_factory=list)
    version_spec: str | None = None

    width: int | None = None
    height: int | None = None
    depth: int | None = None

    patterns: list[str] = field(default_factory=list)
    wiring_placement_restrictions: list[str] = field(default_factory=list)
    animated_restrictions: list[str] = field(default_factory=list)
    component_restrictions: list[str] = field(default_factory=list)
    miscellaneous_restrictions: list[str] = field(default_factory=list)
    tags: list[TagAssignmentPayload] = field(default_factory=list)

    extra_info: Info = field(default_factory=Info)
    creators_ign: list[str] = field(default_factory=list)
    links: list[BuildLinkPayload] = field(default_factory=list)

    display_name: str | None = None
    source_submission_draft_id: uuid.UUID | None = None
    sponsor: PublicSponsor | None = None
    submitter_account_id: int | None = None
    completion_time: str | None = None
    completion_at: Instant | None = None
    completion_evidence: str | None = None
    description: str | None = None

    source_messages: tuple[SourceMessage, ...] = ()

    ai_generated: bool | None = None

    # Category-specific facts, staged flat until the category is known.
    door_orientation: DoorOrientationLiteral | None = None
    door_width: int | None = None
    door_height: int | None = None
    door_depth: int | None = None
    normal_opening_time: int | None = None
    normal_closing_time: int | None = None
    visible_opening_time: int | None = None
    visible_closing_time: int | None = None
    extender_orientation: str | None = None
    extension_length: int | None = None
    extender_type: str | None = None

    @classmethod
    def from_draft(cls, draft: BuildDraft) -> BuildDraftPayload:
        """Capture inferred values without storing SQLAlchemy state."""
        values = {item.name: getattr(draft, item.name) for item in fields(cls)}
        values["links"] = [BuildLinkPayload(link.url, link.media_type) for link in draft.links]
        values["tags"] = [
            TagAssignmentPayload(
                definition=TagDefinitionPayload(
                    **{item.name: getattr(tag.definition, item.name) for item in fields(TagDefinitionPayload)}
                ),
                value=tag.value,
                display_unit=tag.display_unit,
                display_order=tag.display_order,
                evidence=tag.evidence,
                provenance=tag.provenance,
            )
            for tag in draft.tags
        ]
        return cls(**values)

    def to_draft(self) -> BuildDraft:
        """Restore editable facts from the captured payload."""
        values = {item.name: getattr(self, item.name) for item in fields(self)}
        values["links"] = [BuildLink(link.url, link.media_type) for link in self.links]
        values["tags"] = [
            TagAssignment(
                definition=TagDefinition(
                    **{item.name: getattr(tag.definition, item.name) for item in fields(TagDefinitionPayload)}
                ),
                value=tag.value,
                display_unit=tag.display_unit,
                display_order=tag.display_order,
                evidence=tag.evidence,
                provenance=tag.provenance,
            )
            for tag in self.tags
        ]
        return BuildDraft(**values)
