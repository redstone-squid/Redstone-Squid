"""Adapter from normalized synchronized submissions to the build aggregate."""

from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import Any, Protocol, cast
from uuid import UUID

from squid.core.actors import ActingContext, application_operation
from squid.core.builds.domain import (
    BuildCategory,
    BuildStatus,
    DoorOrientationLiteral,
    Info,
    ShowcaseTag,
    UnresolvedTags,
    read_showcase_tags,
)
from squid.core.builds.domain.credits import name_credits, read_credits
from squid.core.builds.errors import InvalidBuildError
from squid.core.builds.models import BUILD_CLASS_BY_CATEGORY, Build, BuildLink, DoorBuild, ExtenderBuild
from squid.core.errors import InvalidStateError, JSONValue, ValidationError
from squid.core.submissions.domain.finalization import (
    BuildSubmissionRejected,
    DoorSubmissionDetails,
    ExtenderOrientation,
    ExtenderSubmissionDetails,
    NormalizedSubmission,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
)
from squid.core.submissions.domain.links import LINK_FIELDS
from squid.core.tags.domain import TagModerationStatus, TagSemanticKind, coerce_assignment_value
from squid.core.tags.models import TagAssignment, TagDefinition
from squid.core.versions.domain import MinecraftVersion
from squid.core.versions.errors import InvalidVersionError

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

    async def get_by_source_submission_draft_id(self, draft_id: UUID, *, context: ActingContext) -> Build | None: ...

    async def prepare_for_account(
        self,
        build: Build,
        *,
        submitter_account_id: int,
        source_submission_draft_id: UUID,
        display_name: str | None,
        ai_generated: bool,
        context: ActingContext,
    ) -> Build: ...


class ApprovedSubmissionTags(Protocol):
    """Read the currently approved definitions behind stable form option keys."""

    async def public_definitions(self, *, context: ActingContext) -> Sequence[TagDefinition]: ...


class CanonicalSubmissionVersions(Protocol):
    """Resolve compatibility claims and read canonical version names."""

    async def list_all(self, *, context: ActingContext) -> Sequence[MinecraftVersion]: ...

    async def resolve_spec(self, version_spec: str, *, context: ActingContext) -> list[str]: ...


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

    @application_operation
    async def prepare(
        self, submission: NormalizedSubmission, *, context: ActingContext
    ) -> Build | BuildSubmissionRejected:
        """Resolve a candidate without saving it, or return an existing matching source build."""
        existing = await self._builds.get_by_source_submission_draft_id(submission.source_draft_id, context=context)
        if existing is not None:
            if existing.submitter_account_id != submission.owner_account_id or existing.sponsor != submission.sponsor:
                return _target_rejected()
            return existing
        if submission.source_version is not None:
            rejection = await self._validate_source_version(submission.source_version)
            if rejection is not None:
                return rejection
        resolved = await self._resolve_tags(submission)
        if isinstance(resolved, BuildSubmissionRejected):
            return resolved
        build = _to_build(submission, *resolved)
        if submission.version_compatibility:
            # Keep an unparseable claim as editable text without guessing
            # compatible releases or refusing an otherwise usable build.
            with suppress(ValueError, InvalidVersionError):
                build.versions = list(
                    dict.fromkeys(await self._versions.resolve_spec(submission.version_compatibility, context=context))
                )
        try:
            return await self._builds.prepare_for_account(
                build,
                submitter_account_id=submission.owner_account_id,
                source_submission_draft_id=submission.source_draft_id,
                display_name=submission.display_name,
                ai_generated=submission.ai_generated,
                context=context,
            )
        except InvalidBuildError, InvalidStateError:
            return _target_rejected()

    async def _validate_source_version(self, source_version: str) -> BuildSubmissionRejected | None:
        canonical_versions = {
            str(version) for version in await self._versions.list_all(context=ActingContext.current())
        }
        if source_version not in canonical_versions:
            return BuildSubmissionRejected(
                (SubmissionAttentionIssue("source_version", SubmissionAttentionReason.UNKNOWN_OPTION),)
            )
        return None

    async def _resolve_tags(
        self, submission: NormalizedSubmission
    ) -> tuple[Mapping[str, TagDefinition], list[TagAssignment]] | BuildSubmissionRejected:
        """Look up the submitted option keys and parse showcase tag values.

        Returns the definitions by key and the showcase tag assignments, or the field issues:
        `unknown_option` for a key that names no approved tag of the field's kind, and
        `wrong_type` for a showcase value its tag cannot take, including a missing one.
        """
        definitions = {
            definition.stable_key: definition
            for definition in await self._tags.public_definitions(context=ActingContext.current())
            if definition.moderation_status is TagModerationStatus.APPROVED
        }
        requested: dict[str, tuple[tuple[str, ...], TagSemanticKind]] = {
            "restrictions": (submission.tags.restriction_keys, TagSemanticKind.RESTRICTION),
            "showcase_tags": (tuple(tag.key for tag in submission.tags.showcase_tags), TagSemanticKind.SHOWCASE),
        }
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
                selected[key] = definition
            if invalid:
                issues.append(SubmissionAttentionIssue(field_id, SubmissionAttentionReason.UNKNOWN_OPTION))
        showcase: list[TagAssignment] = []
        for tag in submission.tags.showcase_tags:
            if (definition := selected.get(tag.key)) is None:
                continue
            try:
                showcase.append(
                    TagAssignment(definition, coerce_assignment_value(definition, tag.value), origin="submitted")
                )
            except ValidationError:
                issues.append(SubmissionAttentionIssue("showcase_tags", SubmissionAttentionReason.WRONG_TYPE))
        if submission.display_name is not None and len(submission.display_name.strip()) > 120:
            issues.append(SubmissionAttentionIssue("display_name", SubmissionAttentionReason.TOO_LONG))
        if issues:
            return BuildSubmissionRejected(tuple(dict.fromkeys(issues)))
        return selected, showcase


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


def _to_build(
    submission: NormalizedSubmission, definitions: Mapping[str, TagDefinition], showcase: list[TagAssignment]
) -> Build:
    restriction_values = _restriction_values(submission.tags.restriction_keys, definitions)

    pattern_names = [definitions[key].display_name for key in submission.details.pattern_keys]
    pattern_proposals = submission.details.pattern_proposals

    extra_info: dict[str, JSONValue] = {
        "source_submission": _source_submission(submission),
    }
    UnresolvedTags().add(
        restrictions=[
            *submission.tags.restriction_proposal_fields,
            *(("miscellaneous_restrictions", name) for name in submission.tags.restriction_proposals),
        ],
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
        "tags": showcase,
        "extra_info": cast(Info, extra_info),
        "credits": list(submission.credits),
        "links": [BuildLink(url=link.url, media_type=link.media_type) for link in submission.links],
        "completion_time": submission.completion,
        "description": submission.description,
        "sponsor": submission.sponsor,
    }
    if isinstance(submission.details, DoorSubmissionDetails):
        opening = submission.details.opening
        return DoorBuild(
            **common,
            expansion=submission.details.expansion,
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
    return BUILD_CLASS_BY_CATEGORY[_CATEGORY_MAP[submission.category]](**common, patterns=pattern_names)


def _target_rejected() -> BuildSubmissionRejected:
    return BuildSubmissionRejected((SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),))


def _source_submission(submission: NormalizedSubmission) -> dict[str, JSONValue]:
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
        "sponsor": _sponsor_attribution(submission),
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


def _sponsor_attribution(submission: NormalizedSubmission) -> dict[str, JSONValue] | None:
    if submission.sponsor is None:
        return None
    return {
        "installation_id": str(submission.sponsor.installation_id),
        "display_name": submission.sponsor.display_name,
        "address": submission.sponsor.address,
        "description": submission.sponsor.description,
        "website_url": submission.sponsor.website_url,
    }


def _preview_showcase(answer: JSONValue, definitions: Mapping[str, TagDefinition]) -> list[TagAssignment]:
    """Showcase tag assignments for a draft's answer, leaving out unknown keys and unusable values."""
    try:
        tags = read_showcase_tags(answer if answer is not None else [])
    except ValidationError:
        # Drafts pinned before form revision 9 answer with bare keys.
        keys = answer if isinstance(answer, list) else []
        tags = tuple(ShowcaseTag(key) for key in keys if isinstance(key, str) and key in definitions)
    assignments: list[TagAssignment] = []
    for tag in tags:
        definition = definitions.get(tag.key)
        if definition is None or definition.semantic_kind is not TagSemanticKind.SHOWCASE:
            continue
        with suppress(ValidationError):
            assignments.append(
                TagAssignment(definition, coerce_assignment_value(definition, tag.value), origin="submitted")
            )
    return assignments


def preview_build(
    category: str,
    answers: Mapping[str, JSONValue],
    definitions: Mapping[str, TagDefinition],
) -> Build | None:
    """Build an unsaved, pending build from a draft's answers, for showing its card before submission.

    Unlike _to_build, this accepts incomplete answers: missing values stay None, and option keys
    without an approved definition are left out. It maps fields the same way so the card matches
    the build that finalization will create, except for uploaded media and schematics, which only attach
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
    UnresolvedTags().add(
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
        "tags": _preview_showcase(answers.get("showcase_tags"), definitions),
        "extra_info": cast(Info, extra_info),
        "credits": read_credits(answers.get("credits", [])) or name_credits(keys("creators"), legacy=True),
        "links": list(
            {
                url: BuildLink(url=url, media_type=media_type)
                for field_id, media_type in LINK_FIELDS.items()
                for url in keys(field_id)
            }.values()
        ),
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
        build = BUILD_CLASS_BY_CATEGORY[_CATEGORY_MAP[SubmissionCategory(category)]](**common, patterns=pattern_names)
    build.display_name = text("display_name")
    build.ai_generated = answers.get("ai_generated") is True
    build.submission_status = BuildStatus.PENDING
    return build
