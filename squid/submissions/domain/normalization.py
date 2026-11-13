"""Pure conversion from validated form values to canonical submission input."""

from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

from squid.core.errors import InvalidStateError, JSONValue
from squid.sponsors import PublicSponsor
from squid.submissions.domain import DraftSnapshot, SubmissionOrigin
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
    SubmissionArtifactReadiness,
    SubmissionCategory,
    SubmissionDimensions,
    SubmissionSchematicLicense,
    SubmissionSchematicVisibility,
    SubmissionTaxonomy,
)
from squid_i18n import md_tr


def normalize_submission(
    draft: DraftSnapshot,
    answers: Mapping[str, JSONValue],
    readiness: SubmissionArtifactReadiness,
    sponsor: PublicSponsor | None,
    *,
    origin: SubmissionOrigin,
    source_installation_id: UUID | None = None,
) -> NormalizedSubmission:
    """Convert validated form answers and resolved facts to a finalization payload.

    The caller supplies answers checked against the draft's form manifest,
    attachment readiness, and sponsor details. This step only converts them to
    typed submission values; it performs no database or service lookups.
    """
    category = SubmissionCategory(draft.category)
    visibility = SubmissionSchematicVisibility(_required_str(answers, "schematic_visibility"))
    public = visibility is SubmissionSchematicVisibility.PUBLIC_DOWNLOAD
    license_value = _optional_str(answers, "schematic_license") if public else None
    policy = SchematicRightsPolicy(
        visibility=visibility,
        license=SubmissionSchematicLicense(license_value) if license_value is not None else None,
        rights_attested=_required_bool(answers, "rights_attestation") if public else False,
    )
    details: DoorSubmissionDetails | ExtenderSubmissionDetails | GeneralSubmissionDetails
    if category is SubmissionCategory.DOOR:
        details = DoorSubmissionDetails(
            opening=SubmissionDimensions(
                _optional_int(answers, "opening_width"),
                _optional_int(answers, "opening_height"),
                _optional_int(answers, "opening_depth"),
            ),
            orientation=(DoorOrientation(value) if (value := _optional_str(answers, "door_orientation")) else None),
            pattern_keys=_string_tuple(answers, "patterns"),
            pattern_proposals=_string_tuple(answers, "pattern_proposals"),
            timing=DoorTiming(
                _optional_int(answers, "opening_time"),
                _optional_int(answers, "visible_opening_time"),
                _optional_int(answers, "closing_time"),
                _optional_int(answers, "visible_closing_time"),
            ),
        )
    elif category is SubmissionCategory.EXTENDER:
        details = ExtenderSubmissionDetails(
            orientation=(
                ExtenderOrientation(value) if (value := _optional_str(answers, "movement_orientation")) else None
            ),
            extension_length=_optional_int(answers, "extension_length"),
            pattern_keys=_string_tuple(answers, "patterns"),
            pattern_proposals=_string_tuple(answers, "pattern_proposals"),
            timing=ExtenderTiming(
                _optional_int(answers, "extension_time"),
                _optional_int(answers, "retraction_time"),
            ),
        )
    else:
        details = GeneralSubmissionDetails()
    return NormalizedSubmission(
        source_draft_id=draft.id,
        owner_account_id=draft.owner_account_id,
        origin=origin,
        schema_id=draft.schema_id,
        schema_revision=draft.schema_revision,
        category=category,
        display_name=_optional_nonblank_str(answers, "display_name"),
        description=_optional_str(answers, "description"),
        creators=_string_tuple(answers, "creators"),
        capture_dimensions=SubmissionDimensions(
            _optional_int(answers, "capture_width"),
            _optional_int(answers, "capture_height"),
            _optional_int(answers, "capture_depth"),
        ),
        # Older form revisions require these; from revision 4 they may be absent.
        source_version=_optional_str(answers, "source_version"),
        version_compatibility=_optional_str(answers, "version_compatibility"),
        taxonomy=SubmissionTaxonomy(
            restriction_keys=_string_tuple(answers, "restrictions"),
            restriction_proposals=_string_tuple(answers, "restriction_proposals"),
            showcase_tag_keys=_string_tuple(answers, "showcase_tags"),
        ),
        schematic_policy=policy,
        completion=_optional_str(answers, "completion"),
        ai_generated=_required_bool(answers, "ai_generated"),
        sponsor_attribution=(
            _required_bool(answers, "sponsor_attribution") if origin is SubmissionOrigin.PAPER else False
        ),
        artifacts=readiness.artifacts,
        details=details,
        source_installation_id=source_installation_id,
        sponsor=sponsor,
    )


def _required_str(answers: Mapping[str, JSONValue], field_id: str) -> str:
    value = answers.get(field_id)
    if not isinstance(value, str):
        raise InvalidStateError(
            md_tr(t"submissions-field-not-string = validated field {(field_id_text := str(field_id))} is not a string")  # noqa: F841
        )
    return value


def _optional_str(answers: Mapping[str, JSONValue], field_id: str) -> str | None:
    value = answers.get(field_id)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidStateError(
            md_tr(t"submissions-field-not-string = validated field {(field_id_text := str(field_id))} is not a string")  # noqa: F841
        )
    return value


def _optional_nonblank_str(answers: Mapping[str, JSONValue], field_id: str) -> str | None:
    value = _optional_str(answers, field_id)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _optional_int(answers: Mapping[str, JSONValue], field_id: str) -> int | None:
    value = answers.get(field_id)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidStateError(
            md_tr(
                t"submissions-field-not-integer = validated field {(field_id_text := str(field_id))} is not an integer"  # noqa: F841 - captured translation binding
            )
        )
    return value


def _required_bool(answers: Mapping[str, JSONValue], field_id: str) -> bool:
    value = answers.get(field_id)
    if not isinstance(value, bool):
        raise InvalidStateError(
            md_tr(
                t"submissions-validated-field-not = validated field {(field_id_text := str(field_id))} is not a boolean"  # noqa: F841 - captured translation binding
            )
        )
    return value


def _string_tuple(answers: Mapping[str, JSONValue], field_id: str) -> tuple[str, ...]:
    value = answers.get(field_id, ())
    if (
        not isinstance(value, Sequence)
        or isinstance(value, str | bytes)
        or not all(isinstance(item, str) for item in value)
    ):
        raise InvalidStateError(
            md_tr(
                t"submissions-not-string-list = validated field {(field_id_text := str(field_id))} is not a string list"  # noqa: F841 - captured translation binding
            )
        )
    return tuple(cast(Sequence[str], value))
