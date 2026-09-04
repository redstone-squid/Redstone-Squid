"""Adapter from normalized synchronized submissions to the build aggregate."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast
from uuid import UUID

from squid.builds.domain import (
    BUILD_CLASS_BY_CATEGORY,
    Build,
    BuildCategory,
    DoorBuild,
    DoorOrientationLiteral,
    ExtenderBuild,
    Info,
)
from squid.builds.errors import InvalidBuildError
from squid.core.errors import InvalidStateError, JSONValue
from squid.submissions.application.finalization import BuildSubmissionRejectedError
from squid.submissions.domain.finalization import (
    DoorSubmissionDetails,
    ExtenderOrientation,
    ExtenderSubmissionDetails,
    NormalizedSubmission,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
    SubmissionTargetResult,
)
from squid.tags.domain import TagAssignment, TagDefinition, TagModerationStatus, TagSemanticKind, TagValueType
from squid.versions.domain import MinecraftVersion

TARGET_KEY = "postgres_builds"

_CATEGORY_MAP = {
    SubmissionCategory.DOOR: BuildCategory.DOOR,
    SubmissionCategory.EXTENDER: BuildCategory.EXTENDER,
    SubmissionCategory.UTILITY: BuildCategory.UTILITY,
    SubmissionCategory.ENTRANCE: BuildCategory.ENTRANCE,
    SubmissionCategory.OTHER: BuildCategory.OTHER,
}
_EXTENDER_ORIENTATION = {
    ExtenderOrientation.HORIZONTAL: "Horizontal",
    ExtenderOrientation.VERTICAL_UP: "Upward",
    ExtenderOrientation.VERTICAL_DOWN: "Downward",
}
_RESTRICTION_FIELDS = {
    "wiring-placement": "wiring_placement_restrictions",
    "animated": "animated_restrictions",
    "component": "component_restrictions",
    "miscellaneous": "miscellaneous_restrictions",
}


class SubmissionBuildCommands(Protocol):
    """The build command needed by synchronized finalization."""

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None: ...

    async def submit_for_account(
        self,
        build: Build,
        *,
        submitter_account_id: int,
        source_submission_draft_id: UUID,
        display_name: str | None,
        ai_generated: bool,
    ) -> Build: ...


class ApprovedSubmissionTags(Protocol):
    """Read the currently approved definitions behind stable form option keys."""

    async def public_definitions(self) -> Sequence[TagDefinition]: ...


class CanonicalSubmissionVersions(Protocol):
    """Read exact version names recognized by build persistence."""

    async def list_all(self) -> Sequence[MinecraftVersion]: ...


class CanonicalBuildSubmissionWriter:
    """Create or retrieve one build using its source draft as the retry key."""

    def __init__(
        self,
        builds: SubmissionBuildCommands,
        tags: ApprovedSubmissionTags,
        versions: CanonicalSubmissionVersions,
    ) -> None:
        self._builds = builds
        self._tags = tags
        self._versions = versions

    async def create_or_get(self, submission: NormalizedSubmission) -> SubmissionTargetResult:
        """Translate a normalized payload and delegate retry-safe creation to builds."""
        existing = await self._builds.get_by_source_submission_draft_id(submission.source_draft_id)
        if existing is not None:
            if existing.submitter_account_id != submission.owner_account_id or existing.sponsor != submission.sponsor:
                raise _target_rejected()
            return _target_result(existing, submission)

        await self._validate_source_version(submission.source_version)
        definitions = await self._resolve_tags(submission)
        build = _to_build(submission, definitions)
        try:
            persisted = await self._builds.submit_for_account(
                build,
                submitter_account_id=submission.owner_account_id,
                source_submission_draft_id=submission.source_draft_id,
                display_name=submission.display_name,
                ai_generated=submission.ai_generated,
            )
        except (InvalidBuildError, InvalidStateError) as error:
            raise _target_rejected() from error
        if persisted.submitter_account_id != submission.owner_account_id or persisted.sponsor != submission.sponsor:
            raise _target_rejected()
        return _target_result(persisted, submission)

    async def _validate_source_version(self, source_version: str) -> None:
        canonical_versions = {str(version) for version in await self._versions.list_all()}
        if source_version not in canonical_versions:
            raise BuildSubmissionRejectedError(
                (SubmissionAttentionIssue("source_version", SubmissionAttentionReason.UNKNOWN_OPTION),)
            )

    async def _resolve_tags(self, submission: NormalizedSubmission) -> Mapping[str, TagDefinition]:
        definitions = {
            definition.stable_key: definition
            for definition in await self._tags.public_definitions()
            if definition.moderation_status is TagModerationStatus.APPROVED
        }
        requested: dict[str, tuple[tuple[str, ...], TagSemanticKind]] = {
            "restrictions": (submission.taxonomy.restriction_keys, TagSemanticKind.RESTRICTION),
            "showcase_tags": (submission.taxonomy.showcase_tag_keys, TagSemanticKind.SHOWCASE),
        }
        if isinstance(submission.details, DoorSubmissionDetails | ExtenderSubmissionDetails):
            requested["patterns"] = (submission.details.pattern_keys, TagSemanticKind.PATTERN)

        issues: list[SubmissionAttentionIssue] = []
        selected: dict[str, TagDefinition] = {}
        for field_id, (keys, expected_kind) in requested.items():
            invalid = False
            for key in keys:
                definition = definitions.get(key)
                if definition is None or definition.semantic_kind is not expected_kind:
                    invalid = True
                    continue
                if expected_kind is TagSemanticKind.RESTRICTION and (
                    definition.restriction_type not in _RESTRICTION_FIELDS
                ):
                    invalid = True
                    continue
                if expected_kind is TagSemanticKind.SHOWCASE and definition.value_type is not TagValueType.NONE:
                    invalid = True
                    continue
                selected[key] = definition
            if invalid:
                issues.append(SubmissionAttentionIssue(field_id, SubmissionAttentionReason.UNKNOWN_OPTION))
        if submission.display_name is not None and len(submission.display_name.strip()) > 120:
            issues.append(SubmissionAttentionIssue("display_name", SubmissionAttentionReason.TOO_LONG))
        if not any(creator.strip() for creator in submission.creators):
            issues.append(SubmissionAttentionIssue("creators", SubmissionAttentionReason.TOO_SHORT))
        if issues:
            raise BuildSubmissionRejectedError(tuple(issues))
        return selected


def _to_build(submission: NormalizedSubmission, definitions: Mapping[str, TagDefinition]) -> Build:
    restriction_values: dict[str, list[str]] = {field_name: [] for field_name in _RESTRICTION_FIELDS.values()}
    for key in submission.taxonomy.restriction_keys:
        definition = definitions[key]
        assert definition.restriction_type is not None
        restriction_values[_RESTRICTION_FIELDS[definition.restriction_type]].append(definition.display_name)

    pattern_names: list[str] = []
    pattern_proposals: tuple[str, ...] = ()
    if isinstance(submission.details, DoorSubmissionDetails | ExtenderSubmissionDetails):
        pattern_names = [definitions[key].display_name for key in submission.details.pattern_keys]
        pattern_proposals = submission.details.pattern_proposals
    showcase_assignments = [
        TagAssignment(definitions[key], provenance="submitted") for key in submission.taxonomy.showcase_tag_keys
    ]

    extra_info: dict[str, JSONValue] = {
        "submission_provenance": _submission_provenance(submission),
    }
    if submission.description is not None:
        extra_info["user"] = submission.description
    if submission.taxonomy.restriction_proposals:
        extra_info["unknown_restrictions"] = {
            "miscellaneous_restrictions": list(submission.taxonomy.restriction_proposals)
        }
    if pattern_proposals:
        extra_info["unknown_patterns"] = list(pattern_proposals)

    common: dict[str, Any] = {
        "versions": [submission.source_version],
        "version_spec": submission.version_compatibility,
        "width": submission.capture_dimensions.width,
        "height": submission.capture_dimensions.height,
        "depth": submission.capture_dimensions.depth,
        "wiring_placement_restrictions": restriction_values["wiring_placement_restrictions"],
        "animated_restrictions": restriction_values["animated_restrictions"],
        "component_restrictions": restriction_values["component_restrictions"],
        "miscellaneous_restrictions": restriction_values["miscellaneous_restrictions"],
        "tags": showcase_assignments,
        "extra_info": cast(Info, extra_info),
        "creators_ign": [creator.strip() for creator in submission.creators if creator.strip()],
        "completion_time": submission.completion,
        "description": submission.description,
        "sponsor": submission.sponsor,
    }
    if isinstance(submission.details, DoorSubmissionDetails):
        return DoorBuild(
            **common,
            door_width=submission.details.opening.width,
            door_height=submission.details.opening.height,
            door_depth=submission.details.opening.depth,
            orientation=cast(DoorOrientationLiteral, submission.details.orientation.value.title()),
            patterns=pattern_names,
            normal_opening_time=submission.details.timing.opening,
            visible_opening_time=submission.details.timing.visible_opening,
            normal_closing_time=submission.details.timing.closing,
            visible_closing_time=submission.details.timing.visible_closing,
        )
    if isinstance(submission.details, ExtenderSubmissionDetails):
        return ExtenderBuild(
            **common,
            orientation=_EXTENDER_ORIENTATION[submission.details.orientation],
            extension_length=submission.details.extension_length,
            patterns=pattern_names,
            extender_type=pattern_names[0] if pattern_names else None,
        )
    return BUILD_CLASS_BY_CATEGORY[_CATEGORY_MAP[submission.category]](**common)


def _target_result(build: Build, submission: NormalizedSubmission) -> SubmissionTargetResult:
    if build.id is None:
        msg = "Build persistence returned an aggregate without an identifier."
        raise RuntimeError(msg)
    return SubmissionTargetResult(
        build_id=build.id,
        target_key=TARGET_KEY,
        provenance=_result_provenance(submission),
    )


def _target_rejected() -> BuildSubmissionRejectedError:
    return BuildSubmissionRejectedError(
        (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)
    )


def _submission_provenance(submission: NormalizedSubmission) -> dict[str, JSONValue]:
    timing: dict[str, JSONValue] = {}
    if isinstance(submission.details, DoorSubmissionDetails):
        timing = {
            "opening": submission.details.timing.opening,
            "visible_opening": submission.details.timing.visible_opening,
            "closing": submission.details.timing.closing,
            "visible_closing": submission.details.timing.visible_closing,
        }
    elif isinstance(submission.details, ExtenderSubmissionDetails):
        timing = {
            "extension": submission.details.timing.extension,
            "retraction": submission.details.timing.retraction,
        }
    return {
        "source_draft_id": str(submission.source_draft_id),
        "owner_account_id": submission.owner_account_id,
        "origin": submission.origin.value,
        "schema_id": submission.schema_id,
        "schema_revision": submission.schema_revision,
        "sponsor_attribution": submission.sponsor_attribution,
        "source_installation_id": (
            str(submission.source_installation_id) if submission.source_installation_id is not None else None
        ),
        "sponsor": _sponsor_provenance(submission),
        "timing": timing,
        "schematic_policy": {
            "visibility": submission.schematic_policy.visibility.value,
            "license": (
                submission.schematic_policy.license.value if submission.schematic_policy.license is not None else None
            ),
            "rights_attested": submission.schematic_policy.rights_attested,
            "include_inventories": submission.schematic_policy.include_inventories,
            "include_free_text": submission.schematic_policy.include_free_text,
        },
        "artifacts": {
            "normalized_media_upload_ids": [str(value) for value in submission.artifacts.normalized_media_upload_ids],
            "sanitized_schematic_id": (
                str(submission.artifacts.sanitized_schematic_id)
                if submission.artifacts.sanitized_schematic_id is not None
                else None
            ),
        },
    }


def _result_provenance(submission: NormalizedSubmission) -> dict[str, JSONValue]:
    return {
        "source_draft_id": str(submission.source_draft_id),
        "owner_account_id": submission.owner_account_id,
        "origin": submission.origin.value,
        "schema_id": submission.schema_id,
        "schema_revision": submission.schema_revision,
        "source_installation_id": (
            str(submission.source_installation_id) if submission.source_installation_id is not None else None
        ),
        "sponsor_installation_id": (
            str(submission.sponsor.installation_id) if submission.sponsor is not None else None
        ),
        "normalized_media_upload_ids": [str(value) for value in submission.artifacts.normalized_media_upload_ids],
        "sanitized_schematic_id": (
            str(submission.artifacts.sanitized_schematic_id)
            if submission.artifacts.sanitized_schematic_id is not None
            else None
        ),
    }


def _sponsor_provenance(submission: NormalizedSubmission) -> dict[str, JSONValue] | None:
    if submission.sponsor is None:
        return None
    return {
        "installation_id": str(submission.sponsor.installation_id),
        "display_name": submission.sponsor.display_name,
        "address": submission.sponsor.address,
        "description": submission.sponsor.description,
        "website_url": submission.sponsor.website_url,
    }
