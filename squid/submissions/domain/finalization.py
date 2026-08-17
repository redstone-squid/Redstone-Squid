"""Canonical values passed from synchronized drafts into build creation."""

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from squid.core.errors import JSONValue, ValidationError
from squid.core.i18n import _
from squid.sponsors import PublicSponsor
from squid.submissions.domain.forms import SubmissionOrigin

_STABLE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SubmissionCategory(StrEnum):
    """Stable categories accepted by the submission protocol."""

    DOOR = "door"
    EXTENDER = "extender"
    UTILITY = "utility"
    ENTRANCE = "entrance"
    OTHER = "other"


class DoorOrientation(StrEnum):
    """Direction in which a door exposes its opening."""

    DOOR = "door"
    SKYDOOR = "skydoor"
    TRAPDOOR = "trapdoor"


class ExtenderOrientation(StrEnum):
    """Direction in which an extender moves its payload."""

    HORIZONTAL = "horizontal"
    VERTICAL_UP = "vertical_up"
    VERTICAL_DOWN = "vertical_down"


class SubmissionSchematicVisibility(StrEnum):
    """Submitter-selected visibility for a sanitized schematic."""

    REVIEWER_ONLY = "reviewer_only"
    PUBLIC_DOWNLOAD = "public_download"


class SubmissionSchematicLicense(StrEnum):
    """Licenses offered by the pinned submission manifest."""

    CC0_1_0 = "cc0_1_0"
    CC_BY_4_0 = "cc_by_4_0"
    CC_BY_SA_4_0 = "cc_by_sa_4_0"
    CC_BY_ND_4_0 = "cc_by_nd_4_0"
    CC_BY_NC_4_0 = "cc_by_nc_4_0"
    CC_BY_NC_SA_4_0 = "cc_by_nc_sa_4_0"
    CC_BY_NC_ND_4_0 = "cc_by_nc_nd_4_0"


class SchematicArtifactState(StrEnum):
    """Server-observed state of a schematic supplied for a draft."""

    ABSENT = "absent"
    PROCESSING = "processing"
    SANITIZED = "sanitized"
    REJECTED = "rejected"


class SubmissionAttentionReason(StrEnum):
    """Stable repair reasons shared by every submission transport."""

    UNKNOWN_FIELD = "unknown_field"
    REQUIRED = "required"
    WRONG_TYPE = "wrong_type"
    REQUIRED_VALUE = "required_value"
    BELOW_MINIMUM = "below_minimum"
    ABOVE_MAXIMUM = "above_maximum"
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    TOO_FEW_ITEMS = "too_few_items"
    TOO_MANY_ITEMS = "too_many_items"
    UNKNOWN_OPTION = "unknown_option"
    SCHEMA_UNSUPPORTED = "schema_unsupported"
    SCHEMATIC_REQUIRED = "schematic_required"
    SCHEMATIC_PROCESSING = "schematic_processing"
    SCHEMATIC_REJECTED = "schematic_rejected"
    MEDIA_PROCESSING = "media_processing"
    MEDIA_REJECTED = "media_rejected"
    SPONSOR_UNAVAILABLE = "sponsor_unavailable"
    TARGET_REJECTED = "target_rejected"
    RETRY_EXHAUSTED = "retry_exhausted"


class FinalizationJobStatus(StrEnum):
    """Durable state of one source-draft finalization job."""

    PENDING = "pending"
    CLAIMED = "claimed"
    NEEDS_ATTENTION = "needs_attention"
    COMPLETED = "completed"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class SubmissionAttentionIssue:
    """One actionable field/reason pair safe to return to a client."""

    field_id: str
    reason: SubmissionAttentionReason

    def __post_init__(self) -> None:
        if _STABLE_KEY.fullmatch(self.field_id) is None:
            msg = _("invalid submission attention field ID: {field_id}")
            raise ValidationError(msg, message_params={"field_id": self.field_id})


@dataclass(frozen=True, slots=True)
class SubmissionDimensions:
    """A positive width, height, and depth in blocks."""

    width: int
    height: int
    depth: int

    def __post_init__(self) -> None:
        if min(self.width, self.height, self.depth) < 1:
            msg = _("submission dimensions must be positive")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class SubmissionTaxonomy:
    """Approved stable keys and unapproved free-text proposals."""

    restriction_keys: tuple[str, ...] = ()
    restriction_proposals: tuple[str, ...] = ()
    showcase_tag_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_stable_keys(self.restriction_keys, "restriction")
        _require_stable_keys(self.showcase_tag_keys, "showcase tag")


@dataclass(frozen=True, slots=True)
class SchematicRightsPolicy:
    """Explicit publication grant and privacy-preserving sanitizer policy."""

    visibility: SubmissionSchematicVisibility
    license: SubmissionSchematicLicense | None
    rights_attested: bool
    include_inventories: bool
    include_free_text: bool

    def __post_init__(self) -> None:
        if self.visibility is SubmissionSchematicVisibility.PUBLIC_DOWNLOAD:
            if self.license is None or not self.rights_attested:
                msg = _("public schematics require a license and rights attestation")
                raise ValidationError(msg)
        elif self.license is not None or self.rights_attested:
            msg = _("reviewer-only schematics cannot carry a public distribution grant")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class VerifiedSubmissionArtifacts:
    """Only artifacts whose readiness was established by backend-owned state."""

    normalized_media_upload_ids: tuple[UUID, ...] = ()
    sanitized_schematic_id: UUID | None = None

    def __post_init__(self) -> None:
        identifiers = (*self.normalized_media_upload_ids, self.sanitized_schematic_id)
        if any(identifier is not None and identifier.int == 0 for identifier in identifiers):
            msg = _("verified artifact identifiers cannot be nil UUIDs")
            raise ValidationError(msg)
        if len(self.normalized_media_upload_ids) != len(set(self.normalized_media_upload_ids)):
            msg = _("normalized media upload identifiers must be unique")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class SubmissionArtifactReadiness:
    """Backend assessment of every artifact currently associated with a draft."""

    schematic_state: SchematicArtifactState = SchematicArtifactState.ABSENT
    sanitized_schematic_id: UUID | None = None
    normalized_media_upload_ids: tuple[UUID, ...] = ()
    issues: tuple[SubmissionAttentionIssue, ...] = ()

    def __post_init__(self) -> None:
        if (self.schematic_state is SchematicArtifactState.SANITIZED) != (self.sanitized_schematic_id is not None):
            msg = _("only a sanitized schematic assessment may expose an artifact ID")
            raise ValidationError(msg)
        VerifiedSubmissionArtifacts(self.normalized_media_upload_ids, self.sanitized_schematic_id)

    @property
    def artifacts(self) -> VerifiedSubmissionArtifacts:
        """Project this assessment into references safe for build creation."""
        return VerifiedSubmissionArtifacts(self.normalized_media_upload_ids, self.sanitized_schematic_id)


@dataclass(frozen=True, slots=True)
class DoorTiming:
    """Optional default door timings in game ticks."""

    opening: int | None = None
    visible_opening: int | None = None
    closing: int | None = None
    visible_closing: int | None = None

    def __post_init__(self) -> None:
        _require_nonnegative_optional((self.opening, self.visible_opening, self.closing, self.visible_closing))


@dataclass(frozen=True, slots=True)
class ExtenderTiming:
    """Optional default extender timings in game ticks."""

    extension: int | None = None
    retraction: int | None = None

    def __post_init__(self) -> None:
        _require_nonnegative_optional((self.extension, self.retraction))


@dataclass(frozen=True, slots=True)
class DoorSubmissionDetails:
    """Normalized fields specific to a door submission."""

    opening: SubmissionDimensions
    orientation: DoorOrientation
    pattern_keys: tuple[str, ...] = ()
    pattern_proposals: tuple[str, ...] = ()
    timing: DoorTiming = field(default_factory=DoorTiming)

    def __post_init__(self) -> None:
        _require_stable_keys(self.pattern_keys, "pattern")


@dataclass(frozen=True, slots=True)
class ExtenderSubmissionDetails:
    """Normalized fields specific to an extender submission."""

    orientation: ExtenderOrientation
    extension_length: int
    pattern_keys: tuple[str, ...] = ()
    pattern_proposals: tuple[str, ...] = ()
    timing: ExtenderTiming = field(default_factory=ExtenderTiming)

    def __post_init__(self) -> None:
        if self.extension_length < 1:
            msg = _("extender length must be positive")
            raise ValidationError(msg)
        _require_stable_keys(self.pattern_keys, "pattern")


@dataclass(frozen=True, slots=True)
class GeneralSubmissionDetails:
    """Marker for utility, entrance, and uncategorized submissions."""


type SubmissionCategoryDetails = DoorSubmissionDetails | ExtenderSubmissionDetails | GeneralSubmissionDetails


@dataclass(frozen=True, slots=True)
class NormalizedSubmission:
    """Complete immutable input to the idempotent build-creation target."""

    source_draft_id: UUID
    owner_account_id: int
    origin: SubmissionOrigin
    schema_id: str
    schema_revision: int
    category: SubmissionCategory
    display_name: str | None
    description: str | None
    creators: tuple[str, ...]
    capture_dimensions: SubmissionDimensions
    source_version: str
    version_compatibility: str | None
    taxonomy: SubmissionTaxonomy
    schematic_policy: SchematicRightsPolicy
    completion: str | None
    ai_generated: bool
    sponsor_attribution: bool
    artifacts: VerifiedSubmissionArtifacts
    details: SubmissionCategoryDetails
    source_installation_id: UUID | None = None
    sponsor: PublicSponsor | None = None

    def __post_init__(self) -> None:
        if self.source_draft_id.int == 0 or self.owner_account_id < 1:
            msg = _("normalized submission provenance is invalid")
            raise ValidationError(msg)
        if self.schema_revision < 1 or not self.schema_id:
            msg = _("normalized submission schema provenance is invalid")
            raise ValidationError(msg)
        if not self.creators or not self.source_version:
            msg = _("normalized submissions require creators and an exact source version")
            raise ValidationError(msg)
        compatible = (
            (self.category is SubmissionCategory.DOOR and isinstance(self.details, DoorSubmissionDetails))
            or (self.category is SubmissionCategory.EXTENDER and isinstance(self.details, ExtenderSubmissionDetails))
            or (
                self.category in {SubmissionCategory.UTILITY, SubmissionCategory.ENTRANCE, SubmissionCategory.OTHER}
                and isinstance(self.details, GeneralSubmissionDetails)
            )
        )
        if not compatible:
            msg = _("{category} submission has incompatible category details")
            raise ValidationError(msg, message_params={"category": self.category.value})
        if self.origin is not SubmissionOrigin.PAPER and self.source_installation_id is not None:
            msg = _("Only Paper submissions may retain an installation provenance ID.")
            raise ValidationError(msg)
        if self.sponsor_attribution:
            if (
                self.origin is not SubmissionOrigin.PAPER
                or self.source_installation_id is None
                or self.sponsor is None
                or self.sponsor.installation_id != self.source_installation_id
            ):
                msg = _("Sponsor attribution requires a matching Paper installation projection.")
                raise ValidationError(msg)
        elif self.sponsor is not None:
            msg = _("A submission cannot retain a sponsor projection when attribution was not requested.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class SubmissionTargetResult:
    """Stable result returned by retry-safe build creation."""

    build_id: int
    target_key: str
    provenance: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.build_id < 1 or _STABLE_KEY.fullmatch(self.target_key) is None:
            msg = _("submission target result identity is invalid")
            raise ValidationError(msg)
        object.__setattr__(self, "provenance", deepcopy(dict(self.provenance)))


def _require_stable_keys(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        msg = _("{label} keys must be unique")
        raise ValidationError(msg, message_params={"label": label})
    if any(_STABLE_KEY.fullmatch(value) is None for value in values):
        msg = _("{label} keys must be stable lowercase identifiers")
        raise ValidationError(msg, message_params={"label": label})


def _require_nonnegative_optional(values: tuple[int | None, ...]) -> None:
    if any(value is not None and value < 0 for value in values):
        msg = _("submission timings cannot be negative")
        raise ValidationError(msg)
