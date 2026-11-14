"""Adapter from normalized synchronized submissions to the build aggregate."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast
from uuid import UUID

from squid.builds.domain import BuildCategory, DoorOrientationLiteral, Info, Status, UnresolvedTaxonomy
from squid.builds.domain.credits import name_credits
from squid.builds.errors import InvalidBuildError
from squid.builds.models import BUILD_CLASS_BY_CATEGORY, Build, DoorBuild, ExtenderBuild
from squid.core.errors import InvalidStateError, JSONValue
from squid.submissions.domain.finalization import (
    BuildSubmissionRejected,
    DoorSubmissionDetails,
    ExtenderOrientation,
    ExtenderSubmissionDetails,
    NormalizedSubmission,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
)
from squid.tags.domain import TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAssignment, TagDefinition
from squid.versions.domain import MinecraftVersion

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

    async def prepare_for_account(
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


class SubmissionBuildPreparation:
    """Translate a prepared submission into a build for the finalization executor.

    Resolve form option keys against current approved tags and canonical Minecraft
    versions, then construct the category-specific Build. The source draft ID
    also identifies an already-created build on retry. BuildSubmissionExecutor
    performs the later transaction that saves a new build and its result.
    """

    def __init__(
        self,
        builds: SubmissionBuildCommands,
        tags: ApprovedSubmissionTags,
        versions: CanonicalSubmissionVersions,
    ) -> None:
        self._builds = builds
        self._tags = tags
        self._versions = versions

    async def prepare(self, submission: NormalizedSubmission) -> Build | BuildSubmissionRejected:
        """Resolve a candidate without saving it, or return an existing matching source build."""
        existing = await self._builds.get_by_source_submission_draft_id(submission.source_draft_id)
        if existing is not None:
            if existing.submitter_account_id != submission.owner_account_id or existing.sponsor != submission.sponsor:
                return _target_rejected()
            return existing
        if submission.source_version is not None:
            rejection = await self._validate_source_version(submission.source_version)
            if rejection is not None:
                return rejection
        definitions = await self._resolve_tags(submission)
        if isinstance(definitions, BuildSubmissionRejected):
            return definitions
        try:
            return await self._builds.prepare_for_account(
                _to_build(submission, definitions),
                submitter_account_id=submission.owner_account_id,
                source_submission_draft_id=submission.source_draft_id,
                display_name=submission.display_name,
                ai_generated=submission.ai_generated,
            )
        except InvalidBuildError, InvalidStateError:
            return _target_rejected()

    async def _validate_source_version(self, source_version: str) -> BuildSubmissionRejected | None:
        canonical_versions = {str(version) for version in await self._versions.list_all()}
        if source_version not in canonical_versions:
            return BuildSubmissionRejected(
                (SubmissionAttentionIssue("source_version", SubmissionAttentionReason.UNKNOWN_OPTION),)
            )
        return None

    async def _resolve_tags(
        self, submission: NormalizedSubmission
    ) -> Mapping[str, TagDefinition] | BuildSubmissionRejected:
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
        if issues:
            return BuildSubmissionRejected(tuple(issues))
        return selected


def _restriction_values(keys: Sequence[str], definitions: Mapping[str, TagDefinition]) -> dict[str, list[str]]:
    """Sort restriction names into the build's wiring, animated, component, and miscellaneous lists.

    Keys without an approved restriction definition are skipped.
    """
    values: dict[str, list[str]] = {field_name: [] for field_name in _RESTRICTION_FIELDS.values()}
    for key in keys:
        definition = definitions.get(key)
        if definition is not None and definition.restriction_type in _RESTRICTION_FIELDS:
            values[_RESTRICTION_FIELDS[definition.restriction_type]].append(definition.display_name)
    return values


def _to_build(submission: NormalizedSubmission, definitions: Mapping[str, TagDefinition]) -> Build:
    restriction_values = _restriction_values(submission.taxonomy.restriction_keys, definitions)

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
    # The form does not ask which kind of restriction a proposal is, so record proposals as miscellaneous.
    UnresolvedTaxonomy().add(
        restrictions=[("miscellaneous_restrictions", name) for name in submission.taxonomy.restriction_proposals],
        patterns=pattern_proposals,
    ).write(cast(Info, extra_info))

    common: dict[str, Any] = {
        "versions": [submission.source_version] if submission.source_version is not None else [],
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
        "credits": name_credits(submission.creators, legacy=True),
        "completion_time": submission.completion,
        "description": submission.description,
        "sponsor": submission.sponsor,
    }
    if isinstance(submission.details, DoorSubmissionDetails):
        opening = submission.details.opening
        return DoorBuild(
            **common,
            door_width=opening.width,
            door_height=opening.height,
            door_depth=opening.depth,
            orientation=(
                cast(DoorOrientationLiteral, submission.details.orientation.value.title())
                if submission.details.orientation is not None
                else None
            ),
            patterns=pattern_names,
            normal_opening_time=submission.details.timing.opening,
            visible_opening_time=submission.details.timing.visible_opening,
            normal_closing_time=submission.details.timing.closing,
            visible_closing_time=submission.details.timing.visible_closing,
        )
    if isinstance(submission.details, ExtenderSubmissionDetails):
        return ExtenderBuild(
            **common,
            orientation=(
                _EXTENDER_ORIENTATION[submission.details.orientation]
                if submission.details.orientation is not None
                else None
            ),
            extension_length=submission.details.extension_length,
            patterns=pattern_names,
            extender_type=pattern_names[0] if pattern_names else None,
            extension_time=submission.details.timing.extension,
            retraction_time=submission.details.timing.retraction,
        )
    return BUILD_CLASS_BY_CATEGORY[_CATEGORY_MAP[submission.category]](**common)


def _target_rejected() -> BuildSubmissionRejected:
    return BuildSubmissionRejected((SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),))


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
        },
        "artifacts": {
            "normalized_media_upload_ids": [str(value) for value in submission.artifacts.normalized_media_upload_ids],
            "schematic_id": (
                str(submission.artifacts.schematic_id) if submission.artifacts.schematic_id is not None else None
            ),
        },
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


def preview_build(
    category: str,
    answers: Mapping[str, JSONValue],
    definitions: Mapping[str, TagDefinition],
) -> Build | None:
    """Build an unsaved, pending build from a draft's answers, for showing its card before submission.

    Unlike _to_build, this accepts incomplete answers: missing values stay None, and option keys
    without an approved definition are left out. It maps fields the same way so the card matches
    the build that finalization will create, except for media and schematics, which only attach
    on finalization.

    Args:
        definitions: Approved tag definitions keyed by stable key, as public_definitions returns.

    Unknown dimensions, orientation, and creators remain empty, just as they do
    during finalization. The title formatter displays incomplete build facts.
    """

    def text(field_id: str) -> str | None:
        value = answers.get(field_id)
        return value if isinstance(value, str) and value.strip() else None

    def integer(field_id: str) -> int | None:
        value = answers.get(field_id)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def keys(field_id: str) -> list[str]:
        value = answers.get(field_id)
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    pattern_names = [definitions[key].display_name for key in keys("patterns") if key in definitions]
    extra_info: dict[str, JSONValue] = {}
    UnresolvedTaxonomy().add(
        restrictions=[("miscellaneous_restrictions", name) for name in keys("restriction_proposals")],
        patterns=keys("pattern_proposals"),
    ).write(cast(Info, extra_info))
    source_version = text("source_version")
    common: dict[str, Any] = {
        "versions": [source_version] if source_version is not None else [],
        "version_spec": text("version_compatibility"),
        "width": integer("capture_width"),
        "height": integer("capture_height"),
        "depth": integer("capture_depth"),
        **_restriction_values(keys("restrictions"), definitions),
        "tags": [
            TagAssignment(definitions[key], provenance="submitted")
            for key in keys("showcase_tags")
            if key in definitions
        ],
        "extra_info": cast(Info, extra_info),
        "credits": name_credits(keys("creators"), legacy=True),
        "completion_time": text("completion"),
        "description": text("description"),
    }
    build: Build
    if category == SubmissionCategory.DOOR:
        build = DoorBuild(
            **common,
            door_width=integer("opening_width"),
            door_height=integer("opening_height"),
            door_depth=integer("opening_depth"),
            orientation=(
                cast(DoorOrientationLiteral, value.title()) if (value := text("door_orientation")) is not None else None
            ),
            patterns=pattern_names,
            normal_opening_time=integer("opening_time"),
            visible_opening_time=integer("visible_opening_time"),
            normal_closing_time=integer("closing_time"),
            visible_closing_time=integer("visible_closing_time"),
        )
    elif category == SubmissionCategory.EXTENDER:
        orientation = text("movement_orientation")
        build = ExtenderBuild(
            **common,
            orientation=_EXTENDER_ORIENTATION.get(ExtenderOrientation(orientation))
            if orientation is not None
            else None,
            extension_length=integer("extension_length"),
            patterns=pattern_names,
            extender_type=pattern_names[0] if pattern_names else None,
            extension_time=integer("extension_time"),
            retraction_time=integer("retraction_time"),
        )
    else:
        build = BUILD_CLASS_BY_CATEGORY[_CATEGORY_MAP[SubmissionCategory(category)]](**common)
    build.display_name = text("display_name")
    build.ai_generated = answers.get("ai_generated") is True
    build.submission_status = Status.PENDING
    return build
