"""PostgreSQL candidate queries and atomic per-record publication."""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from itertools import batched, pairwise
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Row,
    Select,
    String,
    and_,
    case,
    delete,
    exists,
    false,
    func,
    not_,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, defer, selectinload
from whenever import Instant

from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import DoorBuild, ExtenderBuild
from squid.core.errors import ServiceUnavailableError
from squid.core.events.infrastructure.repository import publish_domain_event
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.core.persistence.paging import KeysetOrder
from squid.core.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.core.records.application.measurements import metric_snapshot
from squid.core.records.application.metrics import holder_metrics
from squid.core.records.application.models import (
    ALL_RECORDS,
    CandidateBuild,
    CandidateTag,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecordResult,
    PriorPublication,
    PublishedRecordResult,
    RebuildSummary,
    RecomputeItem,
    RecordDrift,
    RecordGap,
    RecordHolderPeriod,
    RecordListFilters,
    RecordListSort,
    RecordNameDiagnostic,
    RecordSibling,
    RecordStatus,
    ScopedCategory,
)
from squid.core.records.application.ports import RecomputeLease, StoredRecord
from squid.core.records.domain import (
    BuildKind,
    Candidate,
    CandidateGap,
    DoorCategory,
    ExtenderCategory,
    FormattedTitle,
    RecordCriterion,
    RecordFact,
    TimingVariant,
    VersionScope,
)
from squid.core.records.domain.door_evidence import LiveDoorConfiguration
from squid.core.records.domain.events import RecordChange, RecordChangeKind, RecordRunPublished
from squid.core.records.domain.models import RecomputeTarget
from squid.core.records.infrastructure.fingerprint import record_fingerprint
from squid.core.records.models import (
    ComputationRun,
    DoorTimingVariant,
    ExtenderTimingVariant,
    MaterializationSource,
    Record,
    RecordDefinition,
    RecordDefinitionTag,
    RecordHolder,
    RecordHolderHistory,
    RecordInputGeneration,
    RecordMember,
    RecordRecomputeQueueItem,
    RecordResult,
    RecordSettings,
    RecordTagRole,
    RecordTiedBuild,
    RulesRevision,
)
from squid.core.records.models import RecordDrift as RecordDriftRow
from squid.core.tags.infrastructure.loading import assignment_units
from squid.core.tags.models import TagAssignment, TagDefinition, TagSlug, display_conversion_scale

RULES_REVISION_DOCUMENT_HASH = "312af53ee50a0cb0cee37673a763a6072321039777451997acc23cb26a6ba9ac"
CALCULATOR_VERSION = "8"
FORMATTER_VERSION = "2"

RECORD_KINDS = (BuildKind.DOOR, BuildKind.EXTENDER)
"""The build kinds that have records."""

_CATEGORY_FILTER_LIMIT = 1000
"""The most categories or records one query names as bound parameters."""

RECORD_RECOMPUTE_QUEUE_SPEC = QueueSpec(
    name="record_recomputation",
    model=RecordRecomputeQueueItem,
    key=(RecordRecomputeQueueItem.id,),
    available_at=RecordRecomputeQueueItem.available_at,
    enqueued_at=RecordRecomputeQueueItem.enqueued_at,
    claimed_at=RecordRecomputeQueueItem.claimed_at,
    claim_token=RecordRecomputeQueueItem.claim_token,
    attempts=RecordRecomputeQueueItem.attempts,
    last_error=RecordRecomputeQueueItem.last_error,
    # Retry with backoff rather than abandoning a request: otherwise its published
    # records could stay stale indefinitely. A permanent error keeps retrying too.
)


@dataclass(frozen=True, slots=True)
class _DefinitionWrite:
    """A title definition's mutable presentation and canonical tag membership."""

    criterion: RecordCriterion
    category_key: str
    title: FormattedTitle
    source: MaterializationSource
    tags: tuple[tuple[RecordTagRole, int], ...]

    @property
    def key(self) -> tuple[RecordCriterion, str]:
        return self.criterion, self.category_key


class PostgresRecordRepository:
    """Load candidates and stage record publication in the caller's transaction."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session
        self._recompute_queue = ClaimedRowQueue(RECORD_RECOMPUTE_QUEUE_SPEC)

    async def begin_computation(self) -> None:
        await self._session().execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))

    async def list_confirmed(self, kind: BuildKind) -> Sequence[CandidateBuild]:
        """Load confirmed doors or extenders; return no candidates for unsupported kinds."""
        session = self._session()
        if kind is BuildKind.DOOR:
            return await self._list_doors(session)
        if kind is BuildKind.EXTENDER:
            return await self._list_extenders(session)
        return ()

    async def list_for_door_widths(
        self,
        height: int,
        depth: int,
        orientation: str,
        version_id: int | None,
        *,
        minimum: int,
        maximum: int | None,
    ) -> Sequence[CandidateBuild]:
        """Read fixed doors in a width interval and all potentially matching families."""
        session = self._session()
        # Related facts use separate SELECTs, so pin their view before reading.
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        fixed_domain = and_(
            DoorBuild.door_width >= minimum,
            DoorBuild.door_height == height,
            func.coalesce(DoorBuild.door_depth, 1) == depth,
        )
        if maximum is not None:
            fixed_domain = and_(fixed_domain, DoorBuild.door_width <= maximum)
        statement = (
            select(DoorBuild)
            .where(
                DoorBuild.submission_status == BuildStatus.APPROVED,
                DoorBuild.extra_info["unknown_patterns"][0].is_(None),
                DoorBuild.orientation == orientation,
                or_(DoorBuild.expansion.is_not(None), fixed_domain),
            )
            .options(
                selectinload(DoorBuild.tags).options(*assignment_units()),
                selectinload(DoorBuild.build_versions),
            )
            .order_by(DoorBuild.id)
        )
        if version_id is not None:
            statement = statement.where(DoorBuild.build_versions.any(version_id=version_id))
        doors = tuple((await session.execute(statement)).unique().scalars())
        timings = await self._door_timings(tuple(door.id for door in doors if door.expansion is None))
        return tuple(
            door_candidate(door, timings.get(door.id, ()), size="variable" if door.expansion is not None else None)
            for door in doors
        )

    async def active_rules_revision_id(self) -> int:
        """Return the active rules revision, activating it and queuing full recomputes when needed."""
        session = self._session()
        await _advisory_lock(session, "record-rules-revision-activation")
        active_id = (
            await session.execute(
                select(RulesRevision.id)
                .where(RulesRevision.activated_at.is_not(None))
                .order_by(RulesRevision.activated_at.desc(), RulesRevision.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        rules_revision = (
            await session.execute(
                select(RulesRevision).where(
                    RulesRevision.document_hash == RULES_REVISION_DOCUMENT_HASH,
                    RulesRevision.calculator_version == CALCULATOR_VERSION,
                    RulesRevision.formatter_version == FORMATTER_VERSION,
                )
            )
        ).scalar_one_or_none()
        if rules_revision is not None and rules_revision.id == active_id:
            return rules_revision.id

        now = Instant.now()
        if rules_revision is None:
            rules_revision = RulesRevision(
                document_hash=RULES_REVISION_DOCUMENT_HASH,
                calculator_version=CALCULATOR_VERSION,
                formatter_version=FORMATTER_VERSION,
                activated_at=now,
            )
            session.add(rules_revision)
            await session.flush()
        else:
            rules_revision.activated_at = now
        await session.execute(
            update(RulesRevision)
            .where(RulesRevision.id != rules_revision.id, RulesRevision.activated_at.is_not(None))
            .values(activated_at=None)
        )
        for kind in RECORD_KINDS:
            await _enqueue(session, kind, RecomputeTarget.KIND, "rules_revision_activation")
        return rules_revision.id

    async def active_current_version_id(self) -> int | None:
        """Read the shared pin."""
        return await self._session().scalar(select(RecordSettings.current_version_id).where(RecordSettings.id == 1))

    async def set_current_version(self, version_id: int | None) -> None:
        """Change the shared pin and queue full recomputes of both kinds in the caller's transaction."""
        session = self._session()
        changed = await session.scalar(
            update(RecordSettings)
            .where(RecordSettings.id == 1, RecordSettings.current_version_id.is_distinct_from(version_id))
            .values(current_version_id=version_id, revision=RecordSettings.revision + 1)
            .returning(RecordSettings.id)
        )
        if changed is None:
            return
        for kind in RECORD_KINDS:
            await _enqueue(session, kind, RecomputeTarget.KIND, "current_version_changed")

    async def input_generation(self, kind: BuildKind) -> int:
        return (
            await self._session().execute(
                select(RecordInputGeneration.generation).where(RecordInputGeneration.build_kind == kind)
            )
        ).scalar_one()

    async def prior_publication(self, kind: BuildKind, build_ids: frozenset[int]) -> PriorPublication:
        """Read the kind's live categories and the live categories `build_ids` belong to."""
        session = self._session()
        live = (
            await session.execute(
                select(Record.version_id, Record.category_key)
                .join(RecordResult, RecordResult.record_id == Record.public_id)
                .where(Record.build_kind == kind)
                .distinct()
            )
        ).all()
        memberships: dict[int, set[ScopedCategory]] = defaultdict(set)
        if build_ids:
            rows = await session.execute(
                select(RecordMember.build_id, Record.version_id, Record.category_key)
                .join(Record, Record.public_id == RecordMember.record_id)
                .where(Record.build_kind == kind, RecordMember.build_id.in_(build_ids))
                .distinct()
            )
            for build_id, version_id, key in rows:
                memberships[build_id].add((version_id, key))
        return PriorPublication(
            live=frozenset((version_id, key) for version_id, key in live),
            memberships={build_id: frozenset(categories) for build_id, categories in memberships.items()},
        )

    async def pending_builds(self, kind: BuildKind) -> frozenset[int]:
        builds = await self._session().scalars(
            select(RecordRecomputeQueueItem.build_id).where(
                RecordRecomputeQueueItem.build_kind == kind, RecordRecomputeQueueItem.build_id.is_not(None)
            )
        )
        return frozenset(build_id for build_id in builds if build_id is not None)

    async def record_categories(self, record_ids: frozenset[UUID]) -> frozenset[ScopedCategory]:
        if not record_ids:
            return frozenset()
        rows = await self._session().execute(
            select(Record.version_id, Record.category_key).where(Record.public_id.in_(record_ids))
        )
        return frozenset((version_id, key) for version_id, key in rows)

    async def publish(self, batch: ComputationBatch) -> RebuildSummary:
        """Publish a batch's affected categories in the caller's transaction.

        Each record in `batch.categories` ends up with the computed result, or
        retires when the batch computed none for it. A record already published
        from a newer input generation is left alone, so a slow batch cannot
        undo a faster, newer one. Unchanged results only record the check.
        Changes of holders and liveness become one `RecordRunPublished` event.
        """
        session = self._session()
        await session.execute(
            select(func.pg_advisory_xact_lock_shared(func.hashtextextended("record-rules-revision-activation", 0)))
        )
        active_revision = await session.scalar(
            select(RulesRevision.id).where(
                RulesRevision.id == batch.rules_revision_id, RulesRevision.activated_at.is_not(None)
            )
        )
        if active_revision is None:
            raise ServiceUnavailableError(context={"reason": "record_rules_changed"})
        await _advisory_lock(session, f"record-publish:{batch.kind.value}")
        baseline = not await session.scalar(
            select(exists().where(RecordResult.record_id == Record.public_id, Record.build_kind == batch.kind))
        )
        run = ComputationRun(
            rules_revision_id=batch.rules_revision_id,
            build_kind=batch.kind,
            full=batch.full,
            input_generation=batch.input_generation,
        )
        session.add(run)
        await session.flush()

        definitions = await self._ensure_definitions(session, batch)
        computed = {
            definition.record_id: (record, definition)
            for record, definition in zip(batch.records, definitions, strict=True)
        }
        existing = await self._results_in(session, batch.kind, batch.categories)
        record_ids = computed.keys() | existing.keys()
        records = (
            {
                record.public_id: record
                for record in await session.scalars(select(Record).where(Record.public_id.in_(record_ids)))
            }
            if record_ids
            else {}
        )
        holders = await _holders_of(session, tuple(result.id for result in existing.values()))
        members = await _members_of(session, tuple(record_ids))

        now = Instant.now()
        changes: list[RecordChange] = []
        drift: list[RecordDriftRow] = []
        changed = retired = 0
        member_writes: list[dict[str, object]] = []
        member_deletes: list[tuple[UUID, int]] = []
        for record_id in sorted(record_ids):
            record = records[record_id]
            if record.input_generation is not None and record.input_generation > batch.input_generation:
                continue
            record.input_generation = batch.input_generation
            result = existing.get(record_id)
            previous = holders.get(result.id, ()) if result is not None else ()
            previous_members = members.get(record_id, frozenset())
            category = (record.version_id, record.category_key)
            expected = batch.expected is None or category in batch.expected
            if record_id not in computed:
                assert result is not None
                if not expected:
                    drift.append(
                        RecordDriftRow(
                            run_id=run.id,
                            record_id=record_id,
                            published=_drift_facts(previous, _stored_gaps(result), previous_members),
                            computed={},
                        )
                    )
                await session.delete(result)
                member_deletes.extend((record_id, build_id) for build_id in previous_members)
                await session.execute(
                    update(RecordHolderHistory)
                    .where(RecordHolderHistory.record_id == record_id, RecordHolderHistory.held_until.is_(None))
                    .values(held_until=now)
                )
                record.retired_at = now
                retired += 1
                changes.append(RecordChange(record_id, RecordChangeKind.RETIRED, previous, ()))
                continue

            computed_record, definition = computed[record_id]
            new_members = frozenset(candidate.build_id for candidate in computed_record.category.candidates)
            member_writes.extend(
                {"record_id": record_id, "build_id": b} for b in sorted(new_members - previous_members)
            )
            member_deletes.extend((record_id, b) for b in previous_members - new_members)
            fingerprint = record_fingerprint(computed_record)
            if result is not None and result.fingerprint == fingerprint:
                result.definition_id = definition.id
                result.last_checked_at = now
                if not expected and new_members != previous_members:
                    gaps = computed_record.resolution.gaps
                    drift.append(
                        RecordDriftRow(
                            run_id=run.id,
                            record_id=record_id,
                            published=_drift_facts(previous, gaps, previous_members),
                            computed=_drift_facts(computed_record.resolution.holder_ids, gaps, new_members),
                        )
                    )
                continue

            if not expected:
                drift.append(
                    RecordDriftRow(
                        run_id=run.id,
                        record_id=record_id,
                        published={}
                        if result is None
                        else _drift_facts(previous, _stored_gaps(result), previous_members),
                        computed=_drift_facts(
                            computed_record.resolution.holder_ids, computed_record.resolution.gaps, new_members
                        ),
                    )
                )
            holder_ids = computed_record.resolution.holder_ids
            if result is None:
                result = RecordResult(
                    record_id=record_id, run_id=run.id, definition_id=definition.id, fingerprint=fingerprint
                )
                session.add(result)
                kind = RecordChangeKind.REVIVED if record.retired_at is not None else RecordChangeKind.APPEARED
                changes.append(RecordChange(record_id, kind, (), holder_ids))
            else:
                await session.execute(delete(RecordHolder).where(RecordHolder.result_id == result.id))
                await session.execute(delete(RecordTiedBuild).where(RecordTiedBuild.result_id == result.id))
                if previous != holder_ids:
                    changes.append(RecordChange(record_id, RecordChangeKind.HOLDERS_CHANGED, previous, holder_ids))
            result.run_id = run.id
            result.definition_id = definition.id
            result.fingerprint = fingerprint
            result.gap_reasons = {
                "missing": [{"build_id": gap.build_id, "field": gap.field} for gap in computed_record.resolution.gaps]
            }
            result.history_complete = computed_record.history_complete
            result.door_evidence = computed_record.door_evidence
            result.computed_at = now
            result.last_checked_at = now
            record.retired_at = None
            changed += 1
            await session.flush()
            self._write_holders(session, result, computed_record)
            await session.execute(delete(RecordHolderHistory).where(RecordHolderHistory.record_id == record_id))
            await self._write_history(session, record_id, definition.id, computed_record)

        if member_deletes:
            await session.execute(
                delete(RecordMember).where(tuple_(RecordMember.record_id, RecordMember.build_id).in_(member_deletes))
            )
        if member_writes:
            await session.execute(
                insert(RecordMember).on_conflict_do_nothing().returning(RecordMember.build_id), member_writes
            )
        session.add_all(drift)
        await session.flush()
        if changes:
            await publish_domain_event(
                session, RecordRunPublished(run.id, batch.kind.value, tuple(changes), baseline=baseline)
            )
        return RebuildSummary(
            run_ids=(run.id,),
            records=len(batch.records),
            changed=changed,
            retired=retired,
            with_gaps=sum(bool(record.resolution.gaps) for record in batch.records),
            drift=len(drift),
        )

    async def _results_in(
        self, session: AsyncSession, kind: BuildKind, categories: frozenset[ScopedCategory]
    ) -> dict[UUID, RecordResult]:
        """Load the current results of the records in `categories`."""
        if not categories:
            return {}
        statement = (
            select(RecordResult, Record.version_id, Record.category_key)
            .join(Record, Record.public_id == RecordResult.record_id)
            .where(Record.build_kind == kind)
            # Publication compares fingerprints and only ever overwrites the evidence.
            .options(defer(RecordResult.door_evidence))
        )
        # A large affected set, such as a full batch, is cheaper to filter here
        # than to send as thousands of bound parameters.
        if len(categories) <= _CATEGORY_FILTER_LIMIT:
            by_version: dict[int | None, list[str]] = defaultdict(list)
            for version_id, key in categories:
                by_version[version_id].append(key)
            statement = statement.where(
                or_(
                    *(
                        and_(Record.version_id.is_not_distinct_from(version_id), Record.category_key.in_(keys))
                        for version_id, keys in by_version.items()
                    )
                )
            )
        rows = await session.execute(statement)
        return {result.record_id: result for result, version_id, key in rows if (version_id, key) in categories}

    @staticmethod
    def _write_holders(session: AsyncSession, result: RecordResult, computed: ComputedRecordResult) -> None:
        candidates_by_id = {candidate.build_id: candidate for candidate in computed.category.candidates}
        for build_id in computed.resolution.holder_ids:
            candidate = candidates_by_id[build_id]
            session.add(
                RecordHolder(
                    result_id=result.id,
                    build_id=build_id,
                    rank=1,
                    metric_snapshot=metric_snapshot(computed, candidate),
                    title=computed.title.title,
                    subtitle=computed.title.subtitle,
                    completion_at=_holder_completion(computed, candidate),
                )
            )
        session.add_all(RecordTiedBuild(result_id=result.id, build_id=id) for id in computed.resolution.tied_ids)

    @staticmethod
    async def _write_history(
        session: AsyncSession, record_id: UUID, definition_id: int, computed: ComputedRecordResult
    ) -> None:
        candidates_by_id = {candidate.build_id: candidate for candidate in computed.category.candidates}
        chain: list[RecordHolderHistory] = []
        for period in computed.history:
            for build_id in period.build_ids:
                history = RecordHolderHistory(
                    record_id=record_id,
                    definition_id=definition_id,
                    build_id=build_id,
                    predecessor_id=None,
                    held_from=Instant(period.held_from),
                    held_until=Instant(period.held_until) if period.held_until is not None else None,
                    metric_snapshot=metric_snapshot(computed, candidates_by_id[build_id]),
                )
                session.add(history)
                chain.append(history)
        if chain:
            await session.flush()
            for predecessor, history in pairwise(chain):
                history.predecessor_id = predecessor.id

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        """List current results that missing facts could change."""
        session = self._session()
        statement = (
            select(RecordDefinition, RecordResult)
            .options(selectinload(RecordDefinition.record))
            .join(Record, Record.public_id == RecordDefinition.record_id)
            .join(RecordResult, RecordResult.definition_id == RecordDefinition.id)
            .where(func.jsonb_array_length(RecordResult.gap_reasons["missing"]) > 0)
        )
        if kind is not None:
            statement = statement.where(Record.build_kind == kind)
        rows = (await session.execute(statement)).all()
        return tuple(_gap_from_row(definition, result) for definition, result in rows)

    async def list_title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordNameDiagnostic]:
        """List current canonical titles containing formatter diagnostics."""
        session = self._session()
        statement = (
            select(RecordDefinition)
            .options(selectinload(RecordDefinition.record))
            .join(Record, Record.public_id == RecordDefinition.record_id)
            .join(RecordResult, RecordResult.definition_id == RecordDefinition.id)
            .where(RecordDefinition.title_diagnostics != [])
            .order_by(RecordDefinition.id)
        )
        if kind is not None:
            statement = statement.where(Record.build_kind == kind)
        definitions = (await session.execute(statement)).scalars()
        return tuple(
            RecordNameDiagnostic(
                definition_id=definition.id,
                title=definition.title,
                diagnostics=tuple(definition.title_diagnostics),
                build_kind=definition.build_kind,
            )
            for definition in definitions
        )

    async def get_published_record(self, result_id: int) -> PublishedRecordResult | None:
        """Return a current result by its identifier."""
        return await self._active_record(RecordResult.id == result_id)

    async def get_latest_record(self, record_id: UUID) -> PublishedRecordResult | None:
        """Return the record's current result, or its retired state."""
        if (current := await self._active_record(RecordResult.record_id == record_id)) is not None:
            return current
        session = self._session()
        row = (
            await session.execute(
                select(RecordDefinition, Record.retired_at, _refresh_pending())
                .options(selectinload(RecordDefinition.record))
                .join(Record, Record.public_id == RecordDefinition.record_id)
                .where(Record.public_id == record_id, Record.retired_at.is_not(None))
                .order_by(RecordDefinition.id.desc())
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            return None
        definition, retired_at, pending = row
        assert retired_at is not None
        return PublishedRecordResult(
            result_id=None,
            definition_id=definition.id,
            record_id=record_id,
            title=definition.title,
            subtitle=definition.subtitle,
            criterion=definition.criterion,
            build_kind=definition.build_kind,
            version_scope=definition.version_scope,
            category_key=definition.category_key,
            holder_build_ids=(),
            computed_at=retired_at.to_stdlib(),
            refresh_pending=pending,
            version_id=definition.version_id,
            retired_at=retired_at.to_stdlib(),
        )

    async def get_record_history(self, record_id: UUID) -> tuple[RecordHolderPeriod, ...]:
        """Return holder periods in order; builds sharing a period are grouped."""
        rows = await self._session().execute(
            select(
                RecordHolderHistory.held_from,
                RecordHolderHistory.held_until,
                RecordHolderHistory.build_id,
                RecordHolderHistory.metric_snapshot,
                Record.build_kind,
            )
            .join(Record, Record.public_id == RecordHolderHistory.record_id)
            .where(RecordHolderHistory.record_id == record_id)
            .order_by(RecordHolderHistory.held_from, RecordHolderHistory.build_id)
        )
        periods: dict[tuple[Instant, Instant | None], list[int]] = {}
        snapshots: dict[tuple[Instant, Instant | None], tuple[BuildKind, dict[str, object]]] = {}
        for held_from, held_until, build_id, snapshot, kind in rows:
            periods.setdefault((held_from, held_until), []).append(build_id)
            snapshots.setdefault((held_from, held_until), (kind, snapshot))
        return tuple(
            RecordHolderPeriod(
                build_ids=tuple(build_ids),
                held_from=held_from.to_stdlib(),
                held_until=held_until.to_stdlib() if held_until is not None else None,
                metrics=holder_metrics(*snapshots[held_from, held_until]),
            )
            for (held_from, held_until), build_ids in periods.items()
        )

    async def list_siblings(self, record_id: UUID) -> tuple[RecordSibling, ...]:
        """Return the other records of the record's category, with their latest wording."""
        record = aliased(Record)
        rows = await self._session().execute(
            select(RecordDefinition, Record.retired_at)
            .join(Record, Record.public_id == RecordDefinition.record_id)
            .join(record, (record.build_kind == Record.build_kind) & (record.category_key == Record.category_key))
            .options(selectinload(RecordDefinition.record))
            .where(record.public_id == record_id, Record.public_id != record_id)
            .distinct(Record.criterion, Record.version_scope, Record.version_id, Record.public_id)
            .order_by(
                Record.criterion, Record.version_scope, Record.version_id, Record.public_id, RecordDefinition.id.desc()
            )
        )
        return tuple(
            RecordSibling(
                record_id=definition.record_id,
                title=definition.title,
                subtitle=definition.subtitle,
                criterion=definition.criterion,
                version_scope=definition.version_scope,
                retired=retired_at is not None,
            )
            for definition, retired_at in rows
        )

    async def stored_record(self, record_id: UUID) -> StoredRecord | None:
        session = self._session()
        record = await session.get(Record, record_id)
        if record is None:
            return None
        title = await session.scalar(
            select(RecordDefinition.title)
            .where(RecordDefinition.record_id == record_id)
            .order_by(RecordDefinition.id.desc())
            .limit(1)
        )
        result = await session.scalar(select(RecordResult).where(RecordResult.record_id == record_id))
        holders = await _holders_of(session, (result.id,)) if result is not None else {}
        members = await _members_of(session, (record_id,))
        return StoredRecord(
            record_id=record_id,
            title=title or "",
            kind=record.build_kind,
            criterion=record.criterion,
            version_id=record.version_id,
            category_key=record.category_key,
            holder_build_ids=holders.get(result.id, ()) if result is not None else (),
            gaps=_stored_gaps(result) if result is not None else (),
            member_build_ids=tuple(sorted(members.get(record_id, ()))),
        )

    async def list_published_records(
        self,
        *,
        selector: PageSelector = FIRST_PAGE,
        sort: RecordListSort = RecordListSort.ID,
        filters: RecordListFilters = ALL_RECORDS,
        page_size: int,
    ) -> Page[PublishedRecordResult]:
        """List one page of current results in display order; ties fall back to the result ID."""
        session = self._session()
        listing = await self._filtered(self._published_statement(include_evidence=False), filters)
        page = await _record_order(sort).page(
            session, listing, selector, page_size=page_size, total=await self.count_published_records(filters=filters)
        )
        return replace(page, items=await self._published(page.items, include_evidence=False))

    async def count_published_records(self, *, filters: RecordListFilters = ALL_RECORDS) -> int:
        """Count current results."""
        statement = (
            select(func.count()).select_from(RecordResult).join(Record, Record.public_id == RecordResult.record_id)
        )
        return await self._session().scalar(await self._filtered(statement, filters)) or 0

    async def _filtered[S: Select[*tuple[Any, ...]]](self, statement: S, filters: RecordListFilters) -> S:
        """Apply list filters to a statement that joins `RecordResult` and `Record`."""
        if filters.build_kind is not None:
            statement = statement.where(Record.build_kind == filters.build_kind)
        if filters.criterion is not None:
            statement = statement.where(Record.criterion == filters.criterion)
        if filters.version_scope is not None:
            statement = statement.where(Record.version_scope == filters.version_scope)
        if filters.status is not None:
            has_gaps = func.coalesce(func.jsonb_array_length(RecordResult.gap_reasons["missing"]), 0) > 0
            statement = statement.where(has_gaps if filters.status is RecordStatus.UNRESOLVED else not_(has_gaps))
        tag_ids = list(filters.tag_ids)
        if filters.tag_slugs:
            # A slug names at most one tag, so an unknown slug matches nothing.
            rows = await self._session().execute(
                select(TagSlug.slug, TagSlug.tag_id).where(
                    TagSlug.slug.in_(filters.tag_slugs), TagSlug.tag_id.is_not(None)
                )
            )
            by_slug = {slug: tag_id for slug, tag_id in rows.tuples() if tag_id is not None}
            if len(by_slug) < len(set(filters.tag_slugs)):
                return statement.where(false())
            tag_ids.extend(by_slug.values())
        for tag_id in dict.fromkeys(tag_ids):
            restriction = (
                select(RecordDefinitionTag.tag_id)
                .where(
                    RecordDefinitionTag.definition_id == RecordResult.definition_id,
                    RecordDefinitionTag.tag_id == tag_id,
                )
                .exists()
            )
            # Types appear only in the category key, as `t[3,17]`.
            pattern = Record.category_key.regexp_match(rf"\|t\[([0-9]+,)*{tag_id}(,[0-9]+)*\]")
            statement = statement.where(or_(restriction, pattern))
        return statement

    @staticmethod
    def _published_statement(*, include_evidence: bool) -> Select[RecordResult, RecordDefinition, bool]:
        statement = (
            select(RecordResult, RecordDefinition, _refresh_pending())
            .options(selectinload(RecordDefinition.record))
            .join(RecordDefinition, RecordDefinition.id == RecordResult.definition_id)
            .join(Record, Record.public_id == RecordResult.record_id)
        )
        return statement if include_evidence else statement.options(defer(RecordResult.door_evidence))

    async def _active_record(self, condition: ColumnElement[bool]) -> PublishedRecordResult | None:
        rows = (await self._session().execute(self._published_statement(include_evidence=True).where(condition))).all()
        records = await self._published(rows, include_evidence=True)
        return records[0] if records else None

    async def _published(
        self, rows: Sequence[Row[RecordResult, RecordDefinition, bool]], *, include_evidence: bool
    ) -> tuple[PublishedRecordResult, ...]:
        if not rows:
            return ()
        session = self._session()
        result_ids = [result.id for result, _definition, _pending in rows]
        holders = await _holders_of(session, result_ids)
        tied_rows = (
            await session.execute(
                select(RecordTiedBuild.result_id, RecordTiedBuild.build_id)
                .where(RecordTiedBuild.result_id.in_(result_ids))
                .order_by(RecordTiedBuild.result_id, RecordTiedBuild.build_id)
            )
        ).all()
        tied: dict[int, list[int]] = {}
        for tied_result_id, build_id in tied_rows:
            tied.setdefault(tied_result_id, []).append(build_id)
        # The first holder's snapshot, which co-holders match on the criterion.
        snapshots = dict(
            (
                await session.execute(
                    select(RecordHolder.result_id, RecordHolder.metric_snapshot)
                    .where(RecordHolder.result_id.in_(result_ids))
                    .distinct(RecordHolder.result_id)
                    .order_by(RecordHolder.result_id, RecordHolder.rank, RecordHolder.build_id)
                )
            )
            .tuples()
            .all()
        )
        since = dict(
            (
                await session.execute(
                    select(RecordHolderHistory.record_id, func.min(RecordHolderHistory.held_from))
                    .where(
                        RecordHolderHistory.record_id.in_([result.record_id for result, _definition, _pending in rows]),
                        RecordHolderHistory.held_until.is_(None),
                    )
                    .group_by(RecordHolderHistory.record_id)
                )
            )
            .tuples()
            .all()
        )
        return tuple(
            PublishedRecordResult(
                result_id=result.id,
                definition_id=definition.id,
                record_id=definition.record_id,
                title=definition.title,
                subtitle=definition.subtitle,
                criterion=definition.criterion,
                build_kind=definition.build_kind,
                version_scope=definition.version_scope,
                category_key=definition.category_key,
                holder_build_ids=holders.get(result.id, ()),
                tied_build_ids=tuple(tied.get(result.id, ())),
                gaps=_stored_gaps(result),
                computed_at=result.computed_at.to_stdlib(),
                last_checked_at=result.last_checked_at.to_stdlib(),
                refresh_pending=pending,
                version_id=definition.version_id,
                door_evidence=result.door_evidence if include_evidence else None,
                metrics=holder_metrics(definition.build_kind, snapshots.get(result.id, {})),
                holder_since=held_from.to_stdlib() if (held_from := since.get(result.record_id)) is not None else None,
            )
            for result, definition, pending in rows
        )

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]:
        """Return exact categories previously accepted through public lookup."""
        session = self._session()
        keys = (
            await session.execute(
                select(Record.category_key)
                .join(RecordDefinition, RecordDefinition.record_id == Record.public_id)
                .where(
                    Record.build_kind == kind,
                    RecordDefinition.materialization_source == MaterializationSource.PUBLIC_LOOKUP,
                )
                .distinct()
            )
        ).scalars()
        identities: list[CategoryIdentity] = []
        for key in keys:
            identity = CategoryIdentity.from_key(key)
            if identity is not None:
                identities.append(identity)
        return tuple(identities)

    async def definition_record_id(self, definition_id: int) -> UUID | None:
        return await self._session().scalar(
            select(RecordDefinition.record_id).where(RecordDefinition.id == definition_id)
        )

    async def save_requested_category(
        self,
        rules_revision_id: int,
        category: CategoryIdentity,
        titles: Mapping[RecordCriterion, FormattedTitle],
    ) -> int:
        """Persist an accepted category in every maintained scope and queue its records."""
        session = self._session()
        pin = await self.active_current_version_id()
        queued = 0
        for version_id in dict.fromkeys((None, pin)):
            definitions = await self._write_definitions(
                session,
                rules_revision_id=rules_revision_id,
                kind=category.kind,
                scope=VersionScope.ALL_TIME if version_id is None else VersionScope.UP_TO_DATE,
                version_id=version_id,
                writes=tuple(
                    _DefinitionWrite(
                        criterion,
                        category.key,
                        title,
                        MaterializationSource.PUBLIC_LOOKUP,
                        tuple((RecordTagRole.RESTRICTION, tag_id) for tag_id in category.restriction_ids),
                    )
                    for criterion, title in titles.items()
                ),
            )
            # One record item recomputes every criterion of its category.
            if definitions:
                await _enqueue(
                    session,
                    category.kind,
                    RecomputeTarget.RECORD,
                    "requested_category",
                    record_id=definitions[0].record_id,
                )
            queued += len(definitions)
        return queued

    async def request_record_recompute(self, record_id: UUID) -> bool:
        session = self._session()
        kind = await session.scalar(select(Record.build_kind).where(Record.public_id == record_id))
        if kind is None:
            return False
        await _enqueue(session, kind, RecomputeTarget.RECORD, "requested", record_id=record_id)
        return True

    async def request_full_recompute(self, kinds: Sequence[BuildKind], *, target: RecomputeTarget) -> None:
        for kind in kinds:
            await _enqueue(self._session(), kind, target, "requested")

    async def claim_recompute(self, *, limit: int) -> RecomputeLease:
        """Claim ready items of one kind that no other worker is processing.

        The caller must commit the claims before computing. Expired claims become
        eligible again through the shared queue visibility timeout.
        """
        session = self._session()
        # Serialize claimers so two cannot both find a kind idle and claim it.
        await _advisory_lock(session, "record-recompute-claim")
        queue = RecordRecomputeQueueItem
        busy = select(queue.build_kind).where(queue.claimed_at.is_not(None), not_(self._recompute_queue.reclaimable()))
        kind = await session.scalar(
            select(queue.build_kind)
            .where(self._recompute_queue.ready(), queue.build_kind.not_in(busy))
            .order_by(queue.available_at, queue.id)
            .limit(1)
        )
        if kind is None:
            return RecomputeLease(kind=None)
        rows = await self._recompute_queue.claim(limit=limit, where=(queue.build_kind == kind,), session=session)
        targets = {row.target for row in rows}
        return RecomputeLease(
            kind=kind,
            claim_tokens=tuple(self._recompute_queue.token_of(row) for row in rows),
            full=bool(targets & {RecomputeTarget.KIND, RecomputeTarget.BACKSTOP}),
            backstop=RecomputeTarget.BACKSTOP in targets,
            builds=frozenset(row.build_id for row in rows if row.build_id is not None),
            records=frozenset(row.record_id for row in rows if row.record_id is not None),
        )

    async def complete_recompute(self, lease: RecomputeLease) -> None:
        """Acknowledge only items whose claim tokens still match this lease.

        Enqueueing resets an item's claim; reclaiming gives it a new token. Either
        change prevents this acknowledgement from deleting the newer work.
        """
        await self._recompute_queue.complete_batch(lease.claim_tokens, session=self._session())

    async def fail_recompute(self, lease: RecomputeLease, error: str) -> None:
        """Release the failed leases this worker still owns for a later retry."""
        await self._recompute_queue.fail_batch(lease.claim_tokens, error=error, session=self._session())

    async def list_recompute_queue(self, *, selector: PageSelector, page_size: int) -> Page[RecomputeItem]:
        queue = RecordRecomputeQueueItem
        session = self._session()
        total = await session.scalar(select(func.count()).select_from(queue))
        page = await KeysetOrder("oldest", queue.enqueued_at, queue.id, descending=False).page(
            session, select(queue), selector, page_size=page_size, total=total or 0
        )
        return replace(
            page,
            items=tuple(
                RecomputeItem(
                    id=row.id,
                    kind=row.build_kind,
                    target=row.target,
                    build_id=row.build_id,
                    record_id=row.record_id,
                    reasons=tuple(row.reasons),
                    enqueued_at=row.enqueued_at.to_stdlib(),
                    attempts=row.attempts,
                    last_error=row.last_error,
                    claimed=row.claimed_at is not None,
                )
                for row in page.items
            ),
        )

    async def list_drift(self, *, selector: PageSelector = FIRST_PAGE, page_size: int) -> Page[RecordDrift]:
        title = (
            select(RecordDefinition.title)
            .where(RecordDefinition.record_id == RecordDriftRow.record_id)
            .order_by(RecordDefinition.id.desc())
            .limit(1)
            .correlate(RecordDriftRow)
            .scalar_subquery()
        )
        order = KeysetOrder("newest", RecordDriftRow.id, RecordDriftRow.id, descending=True)
        page = await order.page(
            self._session(),
            select(RecordDriftRow, title),
            selector,
            page_size=page_size,
            total=await self._session().scalar(select(func.count()).select_from(RecordDriftRow)) or 0,
        )
        return replace(
            page,
            items=tuple(
                RecordDrift(
                    id=row.id,
                    record_id=row.record_id,
                    title=row_title or "",
                    run_id=row.run_id,
                    detected_at=row.detected_at.to_stdlib(),
                    published=row.published,
                    computed=row.computed,
                )
                for row, row_title in page.items
            ),
        )

    async def _list_doors(self, session: AsyncSession) -> Sequence[CandidateBuild]:
        statement = (
            select(DoorBuild)
            .where(
                DoorBuild.submission_status == BuildStatus.APPROVED,
                DoorBuild.extra_info["unknown_patterns"][0].is_(None),
                or_(
                    DoorBuild.expansion.is_not(None),
                    and_(DoorBuild.door_width.is_not(None), DoorBuild.door_height.is_not(None)),
                ),
                DoorBuild.orientation.is_not(None),
            )
            .options(
                selectinload(DoorBuild.tags).options(*assignment_units()),
                selectinload(DoorBuild.build_versions),
            )
        )
        doors = tuple((await session.execute(statement)).unique().scalars())
        timing_by_build = await self._door_timings(tuple(door.id for door in doors if door.expansion is None))
        return tuple(
            door_candidate(
                door, timing_by_build.get(door.id, ()), size="variable" if door.expansion is not None else None
            )
            for door in doors
        )

    async def _list_extenders(self, session: AsyncSession) -> Sequence[CandidateBuild]:
        statement = (
            select(ExtenderBuild)
            .where(
                ExtenderBuild.submission_status == BuildStatus.APPROVED,
                ExtenderBuild.extra_info["unknown_patterns"][0].is_(None),
            )
            .options(
                selectinload(ExtenderBuild.tags).options(*assignment_units()),
                selectinload(ExtenderBuild.build_versions),
            )
        )
        extenders = tuple((await session.execute(statement)).unique().scalars())
        timing_by_build = await self._extender_timings(tuple(extender.id for extender in extenders))
        return tuple(
            candidate
            for extender in extenders
            if (candidate := extender_candidate(extender, timing_by_build.get(extender.id, ()))) is not None
        )

    async def _ensure_definitions(self, session: AsyncSession, batch: ComputationBatch) -> tuple[RecordDefinition, ...]:
        """Write the definitions of a batch's records, returned in record order."""
        by_scope: dict[int | None, list[int]] = defaultdict(list)
        for index, computed in enumerate(batch.records):
            by_scope[computed.version_id].append(index)
        definitions: list[RecordDefinition | None] = [None] * len(batch.records)
        for version_id, indexes in by_scope.items():
            written = await self._write_definitions(
                session,
                rules_revision_id=batch.rules_revision_id,
                kind=batch.kind,
                scope=VersionScope.ALL_TIME if version_id is None else VersionScope.UP_TO_DATE,
                version_id=version_id,
                writes=tuple(
                    _DefinitionWrite(
                        batch.records[index].criterion,
                        batch.records[index].category.identity.key,
                        batch.records[index].title,
                        MaterializationSource(batch.records[index].category.source),
                        tuple(
                            (RecordTagRole.RESTRICTION, tag_id)
                            for tag_id in batch.records[index].category.identity.restriction_ids
                        ),
                    )
                    for index in indexes
                ),
            )
            for index, definition in zip(indexes, written, strict=True):
                definitions[index] = definition
        return tuple(definition for definition in definitions if definition is not None)

    async def _write_definitions(
        self,
        session: AsyncSession,
        *,
        rules_revision_id: int,
        kind: BuildKind,
        scope: VersionScope,
        version_id: int | None,
        writes: Sequence[_DefinitionWrite],
    ) -> tuple[RecordDefinition, ...]:
        """Persist one scope in bulk, preserving stable IDs and explicit public requests."""
        if not writes:
            return ()
        # Sort conflict keys consistently so concurrent lookups acquire row locks in the same order.
        ordered = sorted({write.key: write for write in writes}.values(), key=lambda write: write.key)
        scope_values: dict[str, object] = {"build_kind": kind, "version_scope": scope, "version_id": version_id}
        identities = [
            dict(scope_values, criterion=write.criterion, category_key=write.category_key) for write in ordered
        ]
        # RETURNING enables SQLAlchemy's paged insertmanyvalues path instead of one statement
        # per definition. Passing parameter rows separately keeps large catalogues below bind limits.
        await session.execute(
            insert(Record).on_conflict_do_nothing(constraint="record_titles_identity_key").returning(Record.public_id),
            identities,
        )
        categories = await session.scalars(
            select(Record).where(
                Record.build_kind == kind,
                Record.version_scope == scope,
                Record.version_id.is_not_distinct_from(version_id),
                Record.criterion.in_({write.criterion for write in ordered}),
                Record.category_key.in_({write.category_key for write in ordered}),
            )
        )
        public_ids = {(row.criterion, row.category_key): row.public_id for row in categories}
        statement = insert(RecordDefinition)
        statement = statement.on_conflict_do_update(
            constraint="title_definitions_identity_key",
            set_={
                "title": statement.excluded.title,
                "subtitle": statement.excluded.subtitle,
                "title_diagnostics": statement.excluded.title_diagnostics,
                "materialization_source": case(
                    (
                        statement.excluded.materialization_source == MaterializationSource.PUBLIC_LOOKUP,
                        statement.excluded.materialization_source,
                    ),
                    else_=RecordDefinition.materialization_source,
                ),
            },
        ).returning(RecordDefinition)
        definitions = await session.scalars(
            statement,
            [
                dict(
                    rules_revision_id=rules_revision_id,
                    record_id=public_ids[write.key],
                    title=write.title.title,
                    subtitle=write.title.subtitle,
                    title_diagnostics=[diagnostic.as_dict() for diagnostic in write.title.diagnostics],
                    materialization_source=write.source,
                )
                for write in ordered
            ],
            execution_options={"render_nulls": True},
        )
        by_category = {row.record_id: row for row in definitions}
        by_key = {key: by_category[public_id] for key, public_id in public_ids.items() if public_id in by_category}
        tags = {
            (by_key[write.key].id, tag_role, tag_id): order
            for write in ordered
            for order, (tag_role, tag_id) in enumerate(write.tags)
        }
        if tags:
            await session.execute(
                insert(RecordDefinitionTag).on_conflict_do_nothing().returning(RecordDefinitionTag.definition_id),
                [
                    {
                        "definition_id": definition_id,
                        "tag_role": tag_role,
                        "tag_id": tag_id,
                        "display_order": order,
                    }
                    for (definition_id, tag_role, tag_id), order in sorted(tags.items())
                ],
            )
        return tuple(by_key[write.key] for write in writes)

    async def _door_timings(
        self,
        build_ids: tuple[int, ...],
    ) -> dict[int, tuple[TimingVariant, ...]]:
        session = self._session()
        if not build_ids:
            return {}
        rows = (
            await session.execute(select(DoorTimingVariant).where(DoorTimingVariant.build_id.in_(build_ids)))
        ).scalars()
        grouped: dict[int, list[TimingVariant]] = defaultdict(list)
        for row in rows:
            grouped[row.build_id].append(
                TimingVariant(
                    (
                        row.opening_time,
                        row.visible_opening_time,
                        row.closing_time,
                        row.visible_closing_time,
                        row.opening_reset_time,
                        row.closing_reset_time,
                    )
                )
            )
        return {build_id: tuple(variants) for build_id, variants in grouped.items()}

    async def _extender_timings(
        self,
        build_ids: tuple[int, ...],
    ) -> dict[int, tuple[TimingVariant, ...]]:
        session = self._session()
        if not build_ids:
            return {}
        rows = (
            await session.execute(select(ExtenderTimingVariant).where(ExtenderTimingVariant.build_id.in_(build_ids)))
        ).scalars()
        grouped: dict[int, list[TimingVariant]] = defaultdict(list)
        for row in rows:
            grouped[row.build_id].append(
                TimingVariant(
                    (
                        row.retraction_time,
                        row.extension_time,
                        row.retraction_reset_time,
                        row.extension_reset_time,
                    )
                )
            )
        return {build_id: tuple(variants) for build_id, variants in grouped.items()}


def door_candidate(door: DoorBuild, timing: tuple[TimingVariant, ...], *, size: str | None = None) -> CandidateBuild:
    # Incomplete builds can be saved, but cannot establish a comparable door category.
    assert door.orientation is not None
    assert size is not None or (door.door_width is not None and door.door_height is not None)
    fallback_timing = TimingVariant(
        (
            door.normal_opening_time,
            door.visible_opening_time,
            door.normal_closing_time,
            door.visible_closing_time,
            None,
            None,
        )
    )
    depth = door.depth or 1
    restrictions = _restriction_tags(door.tags)
    patterns = _pattern_tags(door.tags)
    is_expandable = any(tag.stable_key == "expandable" for tag in restrictions)
    return CandidateBuild(
        kind=BuildKind.DOOR,
        candidate=Candidate(
            build_id=door.id,
            completion_at=_as_datetime(door.completion_at) if door.expansion is None else None,
            fixed_volume=door.width * door.height * depth
            if door.width is not None and door.height is not None and not is_expandable and door.expansion is None
            else None,
            timing_variants=(timing or (fallback_timing,)) if door.expansion is None else (),
        ),
        expansion=door.expansion,
        door_opening=(door.door_width, door.door_height, door.door_depth or 1)
        if door.expansion is None and door.door_width is not None and door.door_height is not None
        else None,
        version_ids=frozenset(version.version_id for version in door.build_versions),
        restrictions=restrictions,
        types=patterns,
        door=DoorCategory(
            wiring_restrictions=(),
            animated_restrictions=(),
            size=size if size is not None else _door_size(door),
            types=tuple(pattern.category_name for pattern in patterns) or ("Regular",),
            orientation=door.orientation,
        ),
    )


def extender_candidate(
    extender: ExtenderBuild,
    timing: tuple[TimingVariant, ...],
) -> CandidateBuild | None:
    if extender.orientation is None or extender.extension_length is None:
        return None
    # As for doors, measured variants take precedence over the submitted timing.
    fallback_timing = TimingVariant((extender.retraction_time, extender.extension_time, None, None))
    depth = extender.depth or 1
    restrictions = _restriction_tags(extender.tags)
    patterns = _pattern_tags(extender.tags)
    is_expandable = any(tag.stable_key == "expandable" for tag in restrictions)
    type_names = tuple(pattern.category_name for pattern in patterns)
    if not type_names and extender.extender_type is not None:
        type_names = (extender.extender_type,)
    return CandidateBuild(
        kind=BuildKind.EXTENDER,
        candidate=Candidate(
            build_id=extender.id,
            completion_at=_as_datetime(extender.completion_at),
            fixed_volume=extender.width * extender.height * depth
            if extender.width is not None and extender.height is not None and not is_expandable
            else None,
            timing_variants=timing or (fallback_timing,),
        ),
        version_ids=frozenset(version.version_id for version in extender.build_versions),
        restrictions=restrictions,
        types=patterns,
        extender=ExtenderCategory(
            wiring_restrictions=(),
            orientation=extender.orientation,
            length=extender.extension_length,
            types=type_names or ("Regular",),
        ),
    )


def _restriction_tags(assignments: Sequence[TagAssignment]) -> tuple[CandidateTag, ...]:
    return tuple(
        CandidateTag(
            id=assignment.tag_id,
            kind=RecordTagRole.RESTRICTION,
            name=assignment.definition.display_name,
            restriction_type=assignment.definition.restriction_type,
            stable_key=assignment.definition.stable_key,
            value_type=assignment.value_type,
            assigned_value=_assignment_value(assignment),
            record_operator=assignment.definition.record_operator,
            render_template=assignment.definition.render_template,
            display_unit=assignment.presentation_unit.key if assignment.presentation_unit else None,
            display_scale=display_conversion_scale(
                assignment.definition.canonical_unit_definition, assignment.presentation_unit
            ),
        )
        for assignment in assignments
        if _is_record_tag(assignment.definition, "restriction")
    )


def _pattern_tags(assignments: Sequence[TagAssignment]) -> tuple[CandidateTag, ...]:
    return tuple(
        CandidateTag(
            id=assignment.tag_id,
            kind=RecordTagRole.PATTERN,
            name=assignment.definition.display_name,
            stable_key=assignment.definition.stable_key,
            value_type=assignment.value_type,
            assigned_value=_assignment_value(assignment),
            render_template=assignment.definition.render_template,
            display_unit=assignment.presentation_unit.key if assignment.presentation_unit else None,
            display_scale=display_conversion_scale(
                assignment.definition.canonical_unit_definition, assignment.presentation_unit
            ),
        )
        for assignment in assignments
        if _is_record_tag(assignment.definition, "pattern")
    )


def _is_record_tag(definition: TagDefinition, semantic_kind: str) -> bool:
    return (
        definition.authority == "official"
        and definition.moderation_status == "approved"
        and definition.semantic_kind == semantic_kind
    )


def _assignment_value(assignment: TagAssignment) -> Decimal | str | bool | None:
    if assignment.value_type == "numeric":
        return assignment.numeric_value
    if assignment.value_type == "text":
        return assignment.text_value
    if assignment.value_type == "boolean":
        return assignment.boolean_value
    return None


def _door_size(door: DoorBuild) -> str:
    if door.door_depth is not None and door.door_depth > 1:
        return f"{door.door_width}x{door.door_height}x{door.door_depth}"
    return f"{door.door_width}x{door.door_height}"


def _holder_configurations(computed: ComputedRecordResult, build_id: int) -> tuple[LiveDoorConfiguration, ...]:
    if computed.door_evidence is None:
        return ()
    evaluation = computed.door_evidence.evaluation
    ids = set(evaluation.holder_configuration_ids)
    return tuple(c for c in evaluation.configurations if c.id in ids and c.build_id == build_id)


def _holder_completion(computed: ComputedRecordResult, candidate: Candidate) -> Instant | None:
    if computed.door_evidence is None:
        return Instant(candidate.completion_at) if candidate.completion_at is not None else None
    dates = {c.completion_at for c in _holder_configurations(computed, candidate.build_id)}
    date = next(iter(dates)) if len(dates) == 1 else None
    return Instant(date) if date is not None else None


def _stored_gaps(result: RecordResult) -> tuple[CandidateGap, ...]:
    missing = result.gap_reasons.get("missing", [])
    entries = cast(list[object], missing) if isinstance(missing, list) else []
    return tuple(
        CandidateGap(build_id, RecordFact(field))
        for entry in entries
        if isinstance(entry, Mapping)
        and isinstance(build_id := entry.get("build_id"), int)
        and isinstance(field := entry.get("field"), str)
        and field in RecordFact
    )


def _gap_from_row(definition: RecordDefinition, result: RecordResult) -> RecordGap:
    gaps = _stored_gaps(result)
    return RecordGap(
        definition_id=definition.id,
        title=definition.title,
        subtitle=definition.subtitle,
        criterion=definition.criterion,
        build_ids=tuple(gap.build_id for gap in gaps),
        fields=tuple(gap.field for gap in gaps),
        build_kind=definition.build_kind,
    )


def _as_datetime(value: Instant | None) -> datetime | None:
    return value.to_stdlib() if value is not None else None


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


async def _enqueue(
    session: AsyncSession,
    kind: BuildKind,
    target: RecomputeTarget,
    reason: str,
    *,
    record_id: UUID | None = None,
) -> None:
    """Queue work, merging it into a pending item with the same scope and resetting its claim."""
    match target:
        case RecomputeTarget.KIND:
            scope_key = kind.value
        case RecomputeTarget.RECORD:
            scope_key = f"{kind.value}:record:{record_id}"
        case _:
            scope_key = f"{kind.value}:{target.value}"
    statement = insert(RecordRecomputeQueueItem).values(
        scope_key=scope_key, build_kind=kind, target=target, record_id=record_id, reasons=[reason]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[RecordRecomputeQueueItem.scope_key],
            set_={
                "reasons": func.jsonb_build_array(reason),
                "enqueued_at": func.now(),
                "available_at": func.now(),
                "attempts": 0,
                "claimed_at": None,
                "claim_token": None,
                "last_error": None,
            },
        )
    )


def _record_order(sort: RecordListSort) -> KeysetOrder:
    """Return the keyset order a record listing displays in."""
    match sort:
        case RecordListSort.ID:
            return KeysetOrder(sort, RecordResult.id, RecordResult.id, descending=True)
        case RecordListSort.RECENTLY_CHANGED:
            return KeysetOrder(sort, RecordResult.computed_at, RecordResult.id, descending=True)
        case RecordListSort.TITLE:
            # Many records share a title and differ only by subtitle.
            title = func.lower(
                func.concat(RecordDefinition.title, " ", func.coalesce(RecordDefinition.subtitle, "")), type_=String
            )
            return KeysetOrder(sort, title, RecordResult.id, descending=False)


def _refresh_pending() -> ColumnElement[bool]:
    """Whether the record's kind has queued work that could still change it."""
    return exists().where(RecordRecomputeQueueItem.build_kind == Record.build_kind).label("refresh_pending")


async def _holders_of(session: AsyncSession, result_ids: Sequence[int]) -> dict[int, tuple[int, ...]]:
    if not result_ids:
        return {}
    rows = await session.execute(
        select(RecordHolder.result_id, RecordHolder.build_id)
        .where(RecordHolder.result_id.in_(result_ids))
        .order_by(RecordHolder.result_id, RecordHolder.rank, RecordHolder.build_id)
    )
    holders: dict[int, list[int]] = defaultdict(list)
    for result_id, build_id in rows:
        holders[result_id].append(build_id)
    return {result_id: tuple(build_ids) for result_id, build_ids in holders.items()}


async def _members_of(session: AsyncSession, record_ids: Sequence[UUID]) -> dict[UUID, frozenset[int]]:
    members: dict[UUID, set[int]] = defaultdict(set)
    for chunk in batched(record_ids, _CATEGORY_FILTER_LIMIT, strict=False):
        rows = await session.execute(
            select(RecordMember.record_id, RecordMember.build_id).where(RecordMember.record_id.in_(chunk))
        )
        for record_id, build_id in rows:
            members[record_id].add(build_id)
    return {record_id: frozenset(build_ids) for record_id, build_ids in members.items()}


def _drift_facts(holder_ids: Sequence[int], gaps: Sequence[CandidateGap], members: frozenset[int]) -> dict[str, object]:
    return {
        "holder_build_ids": list(holder_ids),
        "gaps": [{"build_id": gap.build_id, "field": gap.field.value} for gap in gaps],
        "member_build_ids": sorted(members),
    }
