"""Read revision-pinned candidate facts without saving or publishing a build."""

from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.actors import ActingContext
from squid.core.builds.application.editing import BuildEditPatch
from squid.core.builds.domain import BuildStatus, UnresolvedTags
from squid.core.builds.domain.expansion import DoorExpansion
from squid.core.builds.history_models import BuildHistorySnapshot
from squid.core.builds.models import Build, BuildVersion, DoorBuild, ExtenderBuild
from squid.core.records.application.preview import PreviewInputs, RecordPreview
from squid.core.records.application.snapshots import RecordSnapshot
from squid.core.records.domain import TimingVariant
from squid.core.records.infrastructure.repository import PostgresRecordRepository, door_candidate, extender_candidate
from squid.core.records.models import Record
from squid.core.submissions.application.build_answers import BuildAnswers, patch_from_answers
from squid.core.submissions.application.build_target import preview_build
from squid.core.submissions.application.drafts import StoredDraft
from squid.core.submissions.application.reviews import SubmissionDetail
from squid.core.submissions.domain import ChoiceOption
from squid.core.tags.domain import TagModerationStatus
from squid.core.tags.infrastructure.loading import definition_units
from squid.core.tags.models import TagAssignment, TagDefinition
from squid.core.versions.application.services import VersionService
from squid.core.versions.errors import InvalidVersionError
from squid.core.versions.models import Version


class PostgresPreviewReader:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], versions: VersionService) -> None:
        self.sessions = sessions
        self.versions = versions

    async def capture(
        self, subject: StoredDraft | SubmissionDetail, context: ActingContext
    ) -> RecordPreview | PreviewInputs:
        at = datetime.now(UTC)
        revision = subject.snapshot.revision if isinstance(subject, StoredDraft) else subject.review_revision
        if isinstance(subject, StoredDraft):
            source = subject.edit_source
            build_id, base = (source.build_id, source.revision) if source else (0, None)
        else:
            build_id, base = subject.submission.build_id, subject.review_revision
        async with self.sessions() as session:
            records = PostgresRecordRepository(lambda: session)
            await records.begin_computation()
            definitions = {d.id: d for d in await session.scalars(select(TagDefinition).options(*definition_units()))}
            approved = {
                d.stable_key: d
                for d in definitions.values()
                if d.moderation_status == TagModerationStatus.APPROVED and not d.withdrawn
            }
            versions = {v.name: v.id for v in await session.scalars(select(Version))}
            # Project onto transient objects. No changes to tags or builds belong in this transaction.
            session.expunge_all()
            patch = None
            timing: tuple[TimingVariant, ...] = ()
            if isinstance(subject, StoredDraft) and subject.edit_source is None:
                build = preview_build(subject.snapshot.category, subject.snapshot.answers, approved)
                if build is not None:
                    for field in ("patterns", "restrictions"):
                        keys = subject.snapshot.answers.get(field)
                        if isinstance(keys, list):
                            build.tags.extend(
                                TagAssignment(approved[key]) for key in keys if isinstance(key, str) and key in approved
                            )
                if isinstance(build, DoorBuild):
                    build.expansion = subject.door_expansion
                if build is not None and build.version_spec:
                    with suppress(ValueError, InvalidVersionError):
                        build.versions = await self.versions.resolve_spec(build.version_spec, context=context)
            else:
                history = await session.scalar(
                    select(BuildHistorySnapshot.snapshot)
                    .where(BuildHistorySnapshot.build_id == build_id, BuildHistorySnapshot.build_revision == base)
                    .order_by(BuildHistorySnapshot.id.desc())
                    .limit(1)
                )
                if not history:
                    return RecordPreview(revision, at, ("review_revision",), ())
                build, timing = _historical_build(history, definitions)
                if build is not None:
                    if isinstance(subject, StoredDraft):
                        assert subject.edit_source is not None
                        original = BuildAnswers(subject.edit_source.answers, UnresolvedTags.read(build.extra_info))
                        labels = tuple(ChoiceOption(d.stable_key, d.display_name) for d in approved.values())
                        patch = patch_from_answers(
                            original,
                            subject.snapshot.answers,
                            original.unresolved,
                            {"approved_restrictions": labels, "approved_patterns": labels},
                        )
                    elif subject.submission.kind == "edit":
                        values: dict[str, object] = {
                            k: v
                            for k, v in subject.facts.items()
                            if k in BuildEditPatch.__dataclass_fields__ and k not in {"credits", "showcase_tags"}
                        }
                        if isinstance(values.get("door_expansion"), dict):
                            values["door_expansion"] = DoorExpansion.model_validate(values["door_expansion"])
                        patch = BuildEditPatch.from_attributes(values)
                    if patch:
                        patch.apply(build)
            if not isinstance(build, DoorBuild | ExtenderBuild):
                return RecordPreview(revision, at, (), ())
            build.id = build_id
            build.build_versions = [BuildVersion(versions[name]) for name in build.versions if name in versions]
            if patch is not None:
                changes = patch.attributes()
                selected: dict[str, set[str]] = {}
                if "door_type" in changes:
                    selected["pattern"] = set(build.patterns)
                if changes.keys() & build.restrictions.keys():
                    selected["restriction"] = {name for group in build.restrictions.values() for name in group or ()}
                assignments = {
                    a.definition.id: a
                    for a in build.tags
                    if a.definition.semantic_kind not in selected
                    or a.definition.display_name in selected[a.definition.semantic_kind]
                }
                for definition in approved.values():
                    if (
                        definition.display_name in selected.get(definition.semantic_kind, ())
                        and definition.id not in assignments
                    ):
                        assignments[definition.id] = TagAssignment(definition)
                build.tags = list(assignments.values())
            for assignment in build.tags:
                assignment.tag_id = assignment.definition.id
            missing = []
            if build.extra_info.get("unknown_patterns"):
                missing.append("patterns")
            if isinstance(build, DoorBuild):
                for field, value in (
                    ("door_orientation", build.orientation),
                    ("opening_width", build.door_width),
                    ("opening_height", build.door_height),
                ):
                    if value is None and (field == "door_orientation" or build.expansion is None):
                        missing.append(field)
                candidate = (
                    None if missing else door_candidate(build, timing, size="variable" if build.expansion else None)
                )
            else:
                if not build.orientation:
                    missing.append("movement_orientation")
                if build.extension_length is None:
                    missing.append("extension_length")
                candidate = None if missing else extender_candidate(build, timing)
            if candidate is None:
                return RecordPreview(revision, at, tuple(missing), ())
            kind = candidate.kind
            pin = await records.active_current_version_id()
            snapshot = RecordSnapshot(
                rules_revision_id=await records.active_rules_revision_id(),
                kind=kind,
                version_ids=tuple(dict.fromkeys((None, pin))),
                sources=tuple(await records.list_confirmed(kind)),
                requested=tuple(await records.list_requested_categories(kind)),
                captured_at=at,
            )
            identities = {
                (version, key, criterion): identifier
                for version, key, criterion, identifier in await session.execute(
                    select(Record.version_id, Record.category_key, Record.criterion, Record.public_id).where(
                        Record.build_kind == kind
                    )
                )
            }
            titles = {
                identifier: title
                for identifier, title in await session.execute(
                    select(Build.id, Build.display_name).where(
                        Build.submission_status == BuildStatus.APPROVED, Build.category == build.category
                    )
                )
            }
        return PreviewInputs(revision, snapshot, candidate, identities, titles)


def _historical_build(
    history: dict[str, Any], definitions: dict[int, TagDefinition]
) -> tuple[Build | None, tuple[TimingVariant, ...]]:
    common = history["build"]
    if details := history.get("doors"):
        attributes = dict(details)
        if attributes.get("expansion"):
            attributes["expansion"] = DoorExpansion.model_validate(attributes["expansion"])
        build: Build = DoorBuild(**attributes)
        methods = (
            "opening_time",
            "visible_opening_time",
            "closing_time",
            "visible_closing_time",
            "opening_reset_time",
            "closing_reset_time",
        )
        variants = history.get("door_timing_variants", ())
    elif details := history.get("extenders"):
        build = ExtenderBuild(**details)
        methods = ("retraction_time", "extension_time", "retraction_reset_time", "extension_reset_time")
        variants = history.get("extender_timing_variants", ())
    else:
        return None, ()
    build.width, build.height, build.depth = (common.get(key) for key in ("width", "height", "depth"))
    build.extra_info = common.get("extra_info") or {}
    build.completion_at = (
        Instant(datetime.fromisoformat(common["completion_at"])) if common.get("completion_at") else None
    )
    build.versions = [
        f"{v['edition']} {v['major_version']}.{v['minor_version']}.{v['patch_number']}" for v in history["versions"]
    ]
    for tag in history["tags"]:
        definition = definitions.get(tag["tag_id"])
        if definition is None:
            continue
        value = next(
            (tag[key] for key in ("numeric_value", "text_value", "boolean_value") if tag.get(key) is not None), None
        )
        if tag.get("numeric_value") is not None:
            value = Decimal(tag["numeric_value"])
        assignment = TagAssignment(definition, value)
        build.tags.append(assignment)
        if definition.semantic_kind == "pattern":
            build.patterns.append(definition.display_name)
        elif definition.semantic_kind == "restriction":
            fields = {
                "wiring-placement": "wiring_placement_restrictions",
                "component": "component_restrictions",
                "animated": "animated_restrictions",
            }
            getattr(build, fields.get(definition.restriction_type or "", "miscellaneous_restrictions")).append(
                definition.display_name
            )
    return build, tuple(TimingVariant(tuple(row.get(method) for method in methods)) for row in variants or ())
