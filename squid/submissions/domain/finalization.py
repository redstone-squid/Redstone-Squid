"""Canonical values passed from synchronized drafts into build creation."""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from squid.core.errors import ValidationError
from squid.core.i18n import tr
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
    """Submitter-selected visibility for an uploaded schematic."""

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
    READY = "ready"
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
    SCHEMATIC_PRIMARY_REQUIRED = "schematic_primary_required"
    MEDIA_PROCESSING = "media_processing"
    MEDIA_REJECTED = "media_rejected"
    SPONSOR_UNAVAILABLE = "sponsor_unavailable"
    TARGET_REJECTED = "target_rejected"
    RETRY_EXHAUSTED = "retry_exhausted"
    PERMISSION_REVOKED = "permission_revoked"


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
            field_id = self.field_id
            raise ValidationError(
                tr(
                    t"submissions-invalid-submission-attention = invalid submission attention field ID: {(field_id_text := str(field_id))}"  # noqa: F841
                )
            )


@dataclass(frozen=True, slots=True)
class SubmissionDimensions:
    """A width, height, and depth in blocks; None means the submitter did not give that part.

    Which parts may be None depends on the form revision: from revision 4 the whole build size
    and a door's depth are optional. Parts that are given must be positive.
    """

    width: int | None
    height: int | None
    depth: int | None

    def __post_init__(self) -> None:
        if any(part is not None and part < 1 for part in (self.width, self.height, self.depth)):
            msg = tr(t"submissions-submission-dimensions-positive = submission dimensions must be positive")
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
    """Explicit publication grant for the uploaded schematic."""

    visibility: SubmissionSchematicVisibility
    license: SubmissionSchematicLicense | None
    rights_attested: bool

    def __post_init__(self) -> None:
        if self.visibility is SubmissionSchematicVisibility.PUBLIC_DOWNLOAD:
            if self.license is None or not self.rights_attested:
                msg = tr(
                    t"submissions-public-schematic-rights-required = "
                    t"public schematics require a license and rights attestation"
                )
                raise ValidationError(msg)
        elif self.license is not None or self.rights_attested:
            msg = tr(
                t"submissions-reviewer-schematics-carry = "
                t"reviewer-only schematics cannot carry a public distribution grant"
            )
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class VerifiedSubmissionArtifacts:
    """References to media and schematics that preparation found ready to use.

    These IDs come from stored processing results, rather than form answers.
    Constructing this value checks the IDs' shape; the readiness assessment is
    responsible for checking that processing succeeded.
    """

    normalized_media_upload_ids: tuple[UUID, ...] = ()
    schematic_id: UUID | None = None

    def __post_init__(self) -> None:
        identifiers = (*self.normalized_media_upload_ids, self.schematic_id)
        if any(identifier is not None and identifier.int == 0 for identifier in identifiers):
            msg = tr(t"submissions-verified-artifact-identifiers = verified artifact identifiers cannot be nil UUIDs")
            raise ValidationError(msg)
        if len(self.normalized_media_upload_ids) != len(set(self.normalized_media_upload_ids)):
            msg = tr(t"submissions-normalized-media-upload = normalized media upload identifiers must be unique")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class SubmissionArtifactReadiness:
    """Backend assessment of every artifact currently associated with a draft."""

    schematic_state: SchematicArtifactState = SchematicArtifactState.ABSENT
    schematic_id: UUID | None = None
    normalized_media_upload_ids: tuple[UUID, ...] = ()
    issues: tuple[SubmissionAttentionIssue, ...] = ()

    def __post_init__(self) -> None:
        if (self.schematic_state is SchematicArtifactState.READY) != (self.schematic_id is not None):
            msg = tr(
                t"submissions-ready-schematic-assessment = only a ready schematic assessment may expose an artifact ID"
            )
            raise ValidationError(msg)
        VerifiedSubmissionArtifacts(self.normalized_media_upload_ids, self.schematic_id)

    @property
    def artifacts(self) -> VerifiedSubmissionArtifacts:
        """Project this assessment into references safe for build creation."""
        return VerifiedSubmissionArtifacts(self.normalized_media_upload_ids, self.schematic_id)


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
    """Normalized fields specific to a door submission.

    The opening always has a width and height; its depth may be unknown.
    """

    opening: SubmissionDimensions
    orientation: DoorOrientation
    pattern_keys: tuple[str, ...] = ()
    pattern_proposals: tuple[str, ...] = ()
    timing: DoorTiming = field(default_factory=DoorTiming)

    def __post_init__(self) -> None:
        _require_stable_keys(self.pattern_keys, "pattern")
        if self.opening.width is None or self.opening.height is None:
            msg = tr(t"submissions-door-opening-required = a door opening needs a width and a height")
            raise ValidationError(msg)


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
            msg = tr(t"submissions-extender-length-positive = extender length must be positive")
            raise ValidationError(msg)
        _require_stable_keys(self.pattern_keys, "pattern")


@dataclass(frozen=True, slots=True)
class GeneralSubmissionDetails:
    """Marker for utility, entrance, and uncategorized submissions."""


type SubmissionCategoryDetails = DoorSubmissionDetails | ExtenderSubmissionDetails | GeneralSubmissionDetails


@dataclass(frozen=True, slots=True)
class NormalizedSubmission:
    """Prepared submission retained as the input to a finalization attempt.

    It combines typed form answers with verified attachment references and
    sponsor details. Workers use this snapshot when creating the build, so
    retries do not reinterpret the client's original answers.
    """

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
    source_version: str | None
    """The Minecraft version the capture was exported from; None when the form did not ask for it."""
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
            msg = tr(t"submissions-normalized-submission-provenance = normalized submission provenance is invalid")
            raise ValidationError(msg)
        if self.schema_revision < 1 or not self.schema_id:
            msg = tr(t"submissions-normalized-submission-schema = normalized submission schema provenance is invalid")
            raise ValidationError(msg)
        if not self.creators or self.source_version == "":
            msg = tr(
                t"submissions-normalized-creators-required = "
                t"normalized submissions require creators, and a source version cannot be blank"
            )
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
            category = self.category.value
            raise ValidationError(
                tr(
                    t"submissions-submission-incompatible-category = {category} "
                    t"submission has incompatible category details"
                )
            )
        if self.origin is not SubmissionOrigin.PAPER and self.source_installation_id is not None:
            msg = tr(
                t"submissions-paper-installation-only = Only "
                t"Paper submissions may retain an installation provenance ID."
            )
            raise ValidationError(msg)
        if self.sponsor_attribution:
            if (
                self.origin is not SubmissionOrigin.PAPER
                or self.source_installation_id is None
                or self.sponsor is None
                or self.sponsor.installation_id != self.source_installation_id
            ):
                msg = tr(
                    t"submissions-sponsor-installation-match-required "
                    t"= Sponsor attribution requires a matching Paper installation projection."
                )
                raise ValidationError(msg)
        elif self.sponsor is not None:
            msg = tr(
                t"submissions-submission-retain-sponsor = A "
                t"submission cannot retain a sponsor projection when attribution was not requested."
            )
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class FinalizedBuild:
    """Stable build identity returned by retry-safe finalization."""

    build_id: int

    def __post_init__(self) -> None:
        if self.build_id < 1:
            msg = tr(t"submissions-finalized-build-identity = finalized build identity is invalid")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class BuildSubmissionRejected:
    """Owner-repair issues returned when canonical build creation is refused."""

    issues: tuple[SubmissionAttentionIssue, ...]

    def __post_init__(self) -> None:
        if not self.issues:
            msg = tr(t"submissions-rejected-build-submission = rejected build submission requires at least one issue")
            raise ValidationError(msg)


type BuildSubmissionResult = FinalizedBuild | BuildSubmissionRejected


def _require_stable_keys(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValidationError(tr(t"submissions-keys-unique = {label} keys must be unique"))
    if any(_STABLE_KEY.fullmatch(value) is None for value in values):
        raise ValidationError(
            tr(t"submissions-keys-stable-lowercase = {label} keys must be stable lowercase identifiers")
        )


def _require_nonnegative_optional(values: tuple[int | None, ...]) -> None:
    if any(value is not None and value < 0 for value in values):
        msg = tr(t"submissions-submission-timings-negative = submission timings cannot be negative")
        raise ValidationError(msg)
