"""Strict serialization for versioned durable finalization payloads."""

from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from squid.builds.domain import RestrictionField, ShowcaseTag
from squid.builds.domain.credits import Credit
from squid.builds.domain.expansion import DoorExpansion
from squid.core.errors import DataIntegrityError, JSONValue
from squid.core.errors import ValidationError as DomainValidationError
from squid.sponsors import PublicSponsor
from squid.submissions.domain import SubmissionOrigin
from squid.submissions.domain.finalization import (
    DoorOrientation,
    DoorSubmissionDetails,
    DoorTiming,
    ExtenderOrientation,
    ExtenderSubmissionDetails,
    ExtenderTiming,
    GeneralSubmissionDetails,
    NormalizedSubmission,
    SchematicRightsPolicy,
    SubmissionCategory,
    SubmissionDimensions,
    SubmissionSchematicLicense,
    SubmissionSchematicVisibility,
    SubmissionTaxonomy,
    VerifiedSubmissionArtifacts,
)
from squid.submissions.domain.links import SubmissionLink, SubmissionLinkType


class _StrictPayloadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _DimensionsPayload(_StrictPayloadModel):
    # Payloads written before form revision 4 always carry all three parts.
    width: int | None
    height: int | None
    depth: int | None

    def to_domain(self) -> SubmissionDimensions:
        return SubmissionDimensions(self.width, self.height, self.depth)


class _RestrictionProposalPayload(_StrictPayloadModel):
    field: RestrictionField
    name: str


class _ShowcaseTagPayload(_StrictPayloadModel):
    key: str
    value: str | None = None


class _TaxonomyPayload(_StrictPayloadModel):
    restriction_keys: list[str]
    restriction_proposals: list[str]
    showcase_tags: list[_ShowcaseTagPayload] = []
    # Bare keys, as payloads queued before showcase tags took values carry them. Read, never written.
    showcase_tag_keys: list[str] = Field(default=[], exclude=True)
    restriction_proposal_fields: list[_RestrictionProposalPayload]

    def to_domain(self) -> SubmissionTaxonomy:
        return SubmissionTaxonomy(
            restriction_keys=tuple(self.restriction_keys),
            restriction_proposals=tuple(self.restriction_proposals),
            showcase_tags=(
                *(ShowcaseTag(key) for key in self.showcase_tag_keys),
                *(ShowcaseTag(tag.key, tag.value) for tag in self.showcase_tags),
            ),
            restriction_proposal_fields=tuple((value.field, value.name) for value in self.restriction_proposal_fields),
        )


class _SchematicPolicyPayload(_StrictPayloadModel):
    visibility: str
    license: str | None
    rights_attested: bool

    def to_domain(self) -> SchematicRightsPolicy:
        return SchematicRightsPolicy(
            visibility=SubmissionSchematicVisibility(self.visibility),
            license=SubmissionSchematicLicense(self.license) if self.license is not None else None,
            rights_attested=self.rights_attested,
        )


class _ArtifactsPayload(_StrictPayloadModel):
    normalized_media_upload_ids: list[str]
    schematic_id: str | None

    def to_domain(self) -> VerifiedSubmissionArtifacts:
        return VerifiedSubmissionArtifacts(
            normalized_media_upload_ids=tuple(UUID(identifier) for identifier in self.normalized_media_upload_ids),
            schematic_id=UUID(self.schematic_id) if self.schematic_id is not None else None,
        )


class _DoorTimingPayload(_StrictPayloadModel):
    opening: int | None
    visible_opening: int | None
    closing: int | None
    visible_closing: int | None


class _DoorDetailsPayload(_StrictPayloadModel):
    kind: Literal["door"]
    opening: _DimensionsPayload
    orientation: str | None
    pattern_keys: list[str]
    pattern_proposals: list[str]
    expansion: DoorExpansion | None = None
    timing: _DoorTimingPayload

    @field_validator("expansion", mode="before")
    @classmethod
    def read_expansion(cls, value: object) -> DoorExpansion | None:
        # The outer wire contract is strict, but JSON arrays represent immutable tuples here.
        return DoorExpansion.model_validate(value) if value is not None else None

    def to_domain(self) -> DoorSubmissionDetails:
        return DoorSubmissionDetails(
            expansion=self.expansion,
            opening=self.opening.to_domain(),
            orientation=DoorOrientation(self.orientation) if self.orientation is not None else None,
            pattern_keys=tuple(self.pattern_keys),
            pattern_proposals=tuple(self.pattern_proposals),
            timing=DoorTiming(
                self.timing.opening,
                self.timing.visible_opening,
                self.timing.closing,
                self.timing.visible_closing,
            ),
        )


class _ExtenderTimingPayload(_StrictPayloadModel):
    extension: int | None
    retraction: int | None


class _ExtenderDetailsPayload(_StrictPayloadModel):
    kind: Literal["extender"]
    orientation: str | None
    extension_length: int | None
    pattern_keys: list[str]
    pattern_proposals: list[str]
    timing: _ExtenderTimingPayload

    def to_domain(self) -> ExtenderSubmissionDetails:
        return ExtenderSubmissionDetails(
            orientation=ExtenderOrientation(self.orientation) if self.orientation is not None else None,
            extension_length=self.extension_length,
            pattern_keys=tuple(self.pattern_keys),
            pattern_proposals=tuple(self.pattern_proposals),
            timing=ExtenderTiming(self.timing.extension, self.timing.retraction),
        )


class _GeneralDetailsPayload(_StrictPayloadModel):
    kind: Literal["general"]
    pattern_keys: list[str]
    pattern_proposals: list[str]

    def to_domain(self) -> GeneralSubmissionDetails:
        return GeneralSubmissionDetails(
            pattern_keys=tuple(self.pattern_keys), pattern_proposals=tuple(self.pattern_proposals)
        )


type _DetailsPayload = Annotated[
    _DoorDetailsPayload | _ExtenderDetailsPayload | _GeneralDetailsPayload,
    Field(discriminator="kind"),
]


class _SponsorPayload(_StrictPayloadModel):
    installation_id: str
    display_name: str | None
    address: str | None
    description: str | None
    website_url: str | None

    def to_domain(self) -> PublicSponsor:
        return PublicSponsor(
            installation_id=UUID(self.installation_id),
            display_name=self.display_name,
            address=self.address,
            description=self.description,
            website_url=self.website_url,
        )


class _LinkPayload(_StrictPayloadModel):
    url: str
    media_type: SubmissionLinkType

    def to_domain(self) -> SubmissionLink:
        return SubmissionLink(self.url, self.media_type)


class FinalizationPayload(_StrictPayloadModel):
    """The current durable schema, with optional sponsor attribution."""

    payload_schema: Literal[1]
    source_draft_id: str
    owner_account_id: int
    origin: str
    schema_id: str
    schema_revision: int
    category: str
    display_name: str | None
    description: str | None
    credits: list[dict[str, JSONValue]]
    links: list[_LinkPayload]
    sponsor_attribution: bool
    source_installation_id: str | None
    sponsor: _SponsorPayload | None
    capture_dimensions: _DimensionsPayload
    source_version: str | None
    version_compatibility: str | None
    taxonomy: _TaxonomyPayload
    schematic_policy: _SchematicPolicyPayload
    completion: str | None
    ai_generated: bool
    artifacts: _ArtifactsPayload
    details: _DetailsPayload

    @model_validator(mode="after")
    def require_matching_details(self) -> FinalizationPayload:
        category = SubmissionCategory(self.category)
        compatible = (
            (category is SubmissionCategory.DOOR and isinstance(self.details, _DoorDetailsPayload))
            or (category is SubmissionCategory.EXTENDER and isinstance(self.details, _ExtenderDetailsPayload))
            or (
                category in {SubmissionCategory.UTILITY, SubmissionCategory.ENTRANCE, SubmissionCategory.OTHER}
                and isinstance(self.details, _GeneralDetailsPayload)
            )
        )
        if not compatible:
            msg = "finalization payload category and details do not match"
            raise ValueError(msg)
        return self

    def to_domain(self) -> NormalizedSubmission:
        return NormalizedSubmission(
            source_draft_id=UUID(self.source_draft_id),
            owner_account_id=self.owner_account_id,
            origin=SubmissionOrigin(self.origin),
            schema_id=self.schema_id,
            schema_revision=self.schema_revision,
            category=SubmissionCategory(self.category),
            display_name=self.display_name,
            description=self.description,
            credits=tuple(Credit.from_json(value) for value in self.credits),
            links=tuple(link.to_domain() for link in self.links),
            capture_dimensions=self.capture_dimensions.to_domain(),
            source_version=self.source_version,
            version_compatibility=self.version_compatibility,
            taxonomy=self.taxonomy.to_domain(),
            schematic_policy=self.schematic_policy.to_domain(),
            completion=self.completion,
            ai_generated=self.ai_generated,
            sponsor_attribution=self.sponsor_attribution,
            artifacts=self.artifacts.to_domain(),
            details=self.details.to_domain(),
            source_installation_id=UUID(self.source_installation_id)
            if self.source_installation_id is not None
            else None,
            sponsor=self.sponsor.to_domain() if self.sponsor is not None else None,
        )


def encode_submission(submission: NormalizedSubmission) -> dict[str, object]:
    """Encode the one current schema, independent of category or sponsorship."""
    try:
        payload = FinalizationPayload.model_validate(_submission_document(submission))
        payload.to_domain()
    except (PydanticValidationError, DomainValidationError, TypeError, ValueError) as error:
        msg = "normalized submission cannot be encoded as a durable payload"
        raise DataIntegrityError(msg) from error
    return cast(dict[str, object], payload.model_dump(mode="json"))


def decode_submission(value: object) -> NormalizedSubmission:
    """Decode the current strict payload; retired versions are rejected."""
    try:
        payload = FinalizationPayload.model_validate(value, strict=True)
        return payload.to_domain()
    except (PydanticValidationError, DomainValidationError, TypeError, ValueError) as error:
        msg = "persisted normalized submission payload is invalid"
        raise DataIntegrityError(msg) from error


def _submission_document(submission: NormalizedSubmission) -> dict[str, object]:
    details: dict[str, object]
    if isinstance(submission.details, DoorSubmissionDetails):
        details = {
            "kind": "door",
            "expansion": submission.details.expansion.model_dump(mode="json") if submission.details.expansion else None,
            "opening": _dimensions_document(submission.details.opening),
            "orientation": submission.details.orientation.value if submission.details.orientation is not None else None,
            "pattern_keys": list(submission.details.pattern_keys),
            "pattern_proposals": list(submission.details.pattern_proposals),
            "timing": {
                "opening": submission.details.timing.opening,
                "visible_opening": submission.details.timing.visible_opening,
                "closing": submission.details.timing.closing,
                "visible_closing": submission.details.timing.visible_closing,
            },
        }
    elif isinstance(submission.details, ExtenderSubmissionDetails):
        details = {
            "kind": "extender",
            "orientation": submission.details.orientation.value if submission.details.orientation is not None else None,
            "extension_length": submission.details.extension_length,
            "pattern_keys": list(submission.details.pattern_keys),
            "pattern_proposals": list(submission.details.pattern_proposals),
            "timing": {
                "extension": submission.details.timing.extension,
                "retraction": submission.details.timing.retraction,
            },
        }
    else:
        details = {
            "kind": "general",
            "pattern_keys": list(submission.details.pattern_keys),
            "pattern_proposals": list(submission.details.pattern_proposals),
        }
    return {
        "payload_schema": 1,
        "source_draft_id": str(submission.source_draft_id),
        "owner_account_id": submission.owner_account_id,
        "origin": submission.origin.value,
        "schema_id": submission.schema_id,
        "schema_revision": submission.schema_revision,
        "category": submission.category.value,
        "display_name": submission.display_name,
        "description": submission.description,
        "credits": [credit.to_json() for credit in submission.credits],
        "links": [{"url": link.url, "media_type": link.media_type} for link in submission.links],
        "capture_dimensions": _dimensions_document(submission.capture_dimensions),
        "source_version": submission.source_version,
        "version_compatibility": submission.version_compatibility,
        "taxonomy": {
            "restriction_keys": list(submission.taxonomy.restriction_keys),
            "restriction_proposals": list(submission.taxonomy.restriction_proposals),
            "showcase_tags": [tag.to_json() for tag in submission.taxonomy.showcase_tags],
            "restriction_proposal_fields": [
                {"field": field, "name": name} for field, name in submission.taxonomy.restriction_proposal_fields
            ],
        },
        "schematic_policy": {
            "visibility": submission.schematic_policy.visibility.value,
            "license": (
                submission.schematic_policy.license.value if submission.schematic_policy.license is not None else None
            ),
            "rights_attested": submission.schematic_policy.rights_attested,
        },
        "completion": submission.completion,
        "ai_generated": submission.ai_generated,
        "sponsor_attribution": submission.sponsor_attribution,
        "source_installation_id": (
            str(submission.source_installation_id) if submission.source_installation_id is not None else None
        ),
        "sponsor": _sponsor_document(submission.sponsor),
        "artifacts": {
            "normalized_media_upload_ids": [str(value) for value in submission.artifacts.normalized_media_upload_ids],
            "schematic_id": (
                str(submission.artifacts.schematic_id) if submission.artifacts.schematic_id is not None else None
            ),
        },
        "details": details,
    }


def _dimensions_document(value: SubmissionDimensions) -> dict[str, object]:
    return {"width": value.width, "height": value.height, "depth": value.depth}


def _sponsor_document(sponsor: PublicSponsor | None) -> dict[str, object] | None:
    if sponsor is None:
        return None
    return {
        "installation_id": str(sponsor.installation_id),
        "display_name": sponsor.display_name,
        "address": sponsor.address,
        "description": sponsor.description,
        "website_url": sponsor.website_url,
    }
