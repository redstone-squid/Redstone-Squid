"""PostgreSQL candidate queries and atomic record-run persistence."""

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from typing import cast

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from whenever import Instant

from squid.builds.domain import Status
from squid.builds.models import DoorBuild, ExtenderBuild
from squid.core.errors import DataIntegrityError
from squid.core.i18n import tr
from squid.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.records.application.models import (
    CandidateFacet,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecord,
    PublishedRecord,
    RecordGap,
    RecordSourceCandidate,
    TitleDiagnosticGap,
)
from squid.records.application.ports import RecomputeLease
from squid.records.domain import (
    BuildKind,
    DoorCategory,
    ExtenderCategory,
    FormattedTitle,
    RecordCandidate,
    RecordClass,
    ResolutionStatus,
    TimingVariant,
    VersionScope,
    reduce_timing_variants,
)
from squid.records.models import (
    DoorTimingVariant,
    ExtenderTimingVariant,
    RecordComputationRun,
    RecordComputationStatus,
    RecordDefinitionFacet,
    RecordFacetKind,
    RecordHolderHistory,
    RecordMaterializationSource,
    RecordRecomputeQueueItem,
    RecordResultHolder,
    RecordRule,
    RecordRuleset,
    RecordSeries,
    RecordStanding,
)
from squid.tags.models import TagAssignment, TagDefinition

RULESET_DOCUMENT_HASH = "312af53ee50a0cb0cee37673a763a6072321039777451997acc23cb26a6ba9ac"
CALCULATOR_VERSION = "3"
FORMATTER_VERSION = "2"

RECORD_RECOMPUTE_QUEUE_SPEC = QueueSpec(
    name="record_recomputation",
    model=RecordRecomputeQueueItem,
    key=(RecordRecomputeQueueItem.id,),
    available_at=RecordRecomputeQueueItem.available_at,
    enqueued_at=RecordRecomputeQueueItem.enqueued_at,
    claimed_at=RecordRecomputeQueueItem.locked_at,
    claim_token=RecordRecomputeQueueItem.claim_token,
    attempts=RecordRecomputeQueueItem.attempts,
    last_error=RecordRecomputeQueueItem.last_error,
    # Retry with backoff rather than abandoning a request: otherwise its published
    # records could stay stale indefinitely. A permanent error keeps retrying too.
)


@dataclass(frozen=True, slots=True)
class _DefinitionWrite:
    """A record rule's mutable presentation and canonical taxonomy membership."""

    record_class: RecordClass
    category_key: str
    title: FormattedTitle
    source: RecordMaterializationSource
    facets: tuple[tuple[RecordFacetKind, int], ...]

    @property
    def key(self) -> tuple[RecordClass, str]:
        return self.record_class, self.category_key


class PostgresRecordRepository:
    """Load candidates and stage record publication in the caller's transaction."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session
        self._recompute_queue = ClaimedRowQueue(RECORD_RECOMPUTE_QUEUE_SPEC)

    async def list_confirmed(self, kind: BuildKind) -> Sequence[RecordSourceCandidate]:
        """Load confirmed doors or extenders; return no candidates for unsupported kinds."""
        session = self._session()
        if kind is BuildKind.DOOR:
            return await self._list_doors(session)
        if kind is BuildKind.EXTENDER:
            return await self._list_extenders(session)
        return ()

    async def active_ruleset_id(self) -> int:
        """Return the running ruleset, activating and queuing it when needed."""
        session = self._session()
        await _advisory_lock(session, "record-ruleset-activation")
        active_id = (
            await session.execute(
                select(RecordRuleset.id)
                .where(RecordRuleset.activated_at.is_not(None))
                .order_by(RecordRuleset.activated_at.desc(), RecordRuleset.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        ruleset = (
            await session.execute(
                select(RecordRuleset).where(
                    RecordRuleset.document_hash == RULESET_DOCUMENT_HASH,
                    RecordRuleset.calculator_version == CALCULATOR_VERSION,
                    RecordRuleset.formatter_version == FORMATTER_VERSION,
                )
            )
        ).scalar_one_or_none()
        if ruleset is not None and ruleset.id == active_id:
            return ruleset.id

        now = Instant.now()
        if ruleset is None:
            ruleset = RecordRuleset(
                document_hash=RULESET_DOCUMENT_HASH,
                calculator_version=CALCULATOR_VERSION,
                formatter_version=FORMATTER_VERSION,
                activated_at=now,
            )
            session.add(ruleset)
            await session.flush()
        else:
            ruleset.activated_at = now
        await session.execute(
            update(RecordRuleset)
            .where(RecordRuleset.id != ruleset.id, RecordRuleset.activated_at.is_not(None))
            .values(activated_at=None)
        )
        for kind in (BuildKind.DOOR, BuildKind.EXTENDER):
            await session.execute(
                insert(RecordRecomputeQueueItem)
                .values(
                    scope_key=kind.value,
                    build_kind=kind,
                    reasons=["ruleset_activation"],
                )
                .on_conflict_do_update(
                    index_elements=[RecordRecomputeQueueItem.scope_key],
                    set_={
                        "reasons": ["ruleset_activation"],
                        "enqueued_at": func.now(),
                        "available_at": func.now(),
                        "attempts": 0,
                        "locked_at": None,
                        "claim_token": None,
                        "last_error": None,
                    },
                )
            )
        return ruleset.id

    async def active_current_version_id(self) -> int | None:
        """Return the version pinned by the newest active current-scope run."""
        session = self._session()
        statement = (
            select(RecordComputationRun.version_id)
            .where(
                RecordComputationRun.is_active,
                RecordComputationRun.version_id.is_not(None),
            )
            .order_by(RecordComputationRun.started_at.desc(), RecordComputationRun.id.desc())
            .limit(1)
        )
        return (await session.execute(statement)).scalar_one_or_none()

    async def activate(self, batch: ComputationBatch) -> int:
        """Stage a complete run, supersede the previous run, and enqueue its activation event.

        The advisory lock lets only one transaction replace the active run for
        this kind and version at a time. RecordComputationService commits the new
        results and activation event together, so readers cannot see a partial run.
        """
        session = self._session()
        await _advisory_lock(
            session,
            f"record-run:{batch.kind.value}:{batch.version_id}",
        )
        version_predicate = (
            RecordComputationRun.version_id.is_(None)
            if batch.version_id is None
            else RecordComputationRun.version_id == batch.version_id
        )
        previous_run_id = await session.scalar(
            select(RecordComputationRun.id).where(
                RecordComputationRun.build_kind == batch.kind,
                version_predicate,
                RecordComputationRun.is_active.is_(True),
            )
        )
        run = RecordComputationRun(
            ruleset_id=batch.ruleset_id,
            build_kind=batch.kind,
            version_id=batch.version_id,
            status=RecordComputationStatus.RUNNING,
        )
        session.add(run)
        await session.flush()

        definitions = await self._ensure_definitions(session, batch)
        results: list[tuple[ComputedRecord, RecordRule, RecordStanding]] = []
        for computed, definition in zip(batch.records, definitions, strict=True):
            result = RecordStanding(
                run_id=run.id,
                definition_id=definition.id,
                status=computed.resolution.status,
                gap_reasons=_serialize_gaps(computed),
                provisional_build_id=_provisional_build_id(computed),
                history_complete=computed.history_complete,
            )
            session.add(result)
            results.append((computed, definition, result))

        await session.flush()
        history_chains: list[list[RecordHolderHistory]] = []
        for computed, definition, result in results:
            candidates_by_id = {candidate.build_id: candidate for candidate in computed.competition.candidates}
            for build_id in computed.resolution.holder_ids:
                candidate = candidates_by_id[build_id]
                session.add(
                    RecordResultHolder(
                        result_id=result.id,
                        build_id=build_id,
                        rank=1,
                        metric_snapshot=_metric_snapshot(computed, candidate),
                        title=_holder_title(computed, build_id),
                        subtitle=computed.title.subtitle,
                        completion_at=_as_instant(candidate.completion_at),
                    )
                )

            history_chain: list[RecordHolderHistory] = []
            for period in computed.history:
                for build_id in period.build_ids:
                    candidate = candidates_by_id[build_id]
                    history = RecordHolderHistory(
                        run_id=run.id,
                        definition_id=definition.id,
                        build_id=build_id,
                        predecessor_id=None,
                        held_from=Instant(period.held_from),
                        held_until=Instant(period.held_until) if period.held_until is not None else None,
                        metric_snapshot=_metric_snapshot(computed, candidate),
                    )
                    session.add(history)
                    history_chain.append(history)
            if history_chain:
                history_chains.append(history_chain)

        if history_chains:
            await session.flush()
            for history_chain in history_chains:
                for predecessor, history in pairwise(history_chain):
                    history.predecessor_id = predecessor.id

        await session.execute(
            update(RecordComputationRun)
            .where(
                RecordComputationRun.build_kind == batch.kind,
                version_predicate,
                RecordComputationRun.is_active.is_(True),
                RecordComputationRun.id != run.id,
            )
            .values(is_active=False)
        )
        run.status = RecordComputationStatus.COMPLETED
        run.completed_at = Instant.now()
        run.is_active = True
        await session.flush()
        await session.execute(
            select(
                func.public.publish_domain_event(
                    "record_run.activated",
                    1,
                    "record_run",
                    run.id,
                    func.jsonb_build_object(
                        "previous_run_id",
                        previous_run_id,
                        "baseline",
                        previous_run_id is None,
                        "build_kind",
                        batch.kind.value,
                        "version_id",
                        batch.version_id,
                    ),
                )
            )
        )
        return run.id

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        """List decisive gaps from currently active computation runs."""
        session = self._session()
        statement = (
            select(RecordRule, RecordStanding)
            .join(RecordStanding, RecordStanding.definition_id == RecordRule.id)
            .join(RecordComputationRun, RecordComputationRun.id == RecordStanding.run_id)
            .where(
                RecordComputationRun.is_active.is_(True),
                RecordStanding.status == ResolutionStatus.UNRESOLVED,
            )
        )
        if kind is not None:
            statement = statement.where(RecordRule.build_kind == kind)
        rows = (await session.execute(statement)).all()
        return tuple(_gap_from_row(definition, result) for definition, result in rows)

    async def list_title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]:
        """List active canonical titles containing formatter diagnostics."""
        session = self._session()
        statement = (
            select(RecordRule)
            .join(RecordStanding, RecordStanding.definition_id == RecordRule.id)
            .join(RecordComputationRun, RecordComputationRun.id == RecordStanding.run_id)
            .where(
                RecordComputationRun.is_active.is_(True),
                RecordRule.title_diagnostics != [],
            )
            .distinct()
            .order_by(RecordRule.id)
        )
        if kind is not None:
            statement = statement.where(RecordRule.build_kind == kind)
        definitions = (await session.execute(statement)).scalars()
        return tuple(
            TitleDiagnosticGap(
                definition_id=definition.id,
                title=definition.title,
                diagnostics=tuple(definition.title_diagnostics),
            )
            for definition in definitions
        )

    async def get_published_record(self, result_id: int) -> PublishedRecord | None:
        """Return a published result by its public result identifier."""
        records = await self._active_records(result_id=result_id, limit=1)
        return records[0] if records else None

    async def list_published_records(
        self,
        *,
        offset: int,
        after_id: int | None,
        before_id: int | None,
        descending: bool,
        limit: int,
    ) -> Sequence[PublishedRecord]:
        """List one page of published results in display order.

        ID anchors page relative to the display order; a `before_id` page is fetched in reversed
        order and restored in memory, leaving its overfetched row at the front for the caller.
        """
        return await self._active_records(
            result_id=None,
            offset=offset,
            after_id=after_id,
            before_id=before_id,
            descending=descending,
            limit=limit,
        )

    async def count_published_records(self) -> int:
        """Count the results belonging to the currently active computation runs."""
        session = self._session()
        statement = (
            select(func.count())
            .select_from(RecordStanding)
            .join(RecordComputationRun, RecordComputationRun.id == RecordStanding.run_id)
            .where(RecordComputationRun.is_active.is_(True))
        )
        return await session.scalar(statement) or 0

    async def _active_records(
        self,
        *,
        result_id: int | None,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
        descending: bool = True,
        limit: int,
    ) -> tuple[PublishedRecord, ...]:
        session = self._session()
        statement = (
            select(RecordStanding, RecordRule)
            .join(RecordRule, RecordRule.id == RecordStanding.definition_id)
            .join(RecordComputationRun, RecordComputationRun.id == RecordStanding.run_id)
            .where(RecordComputationRun.is_active.is_(True))
        )
        if result_id is not None:
            statement = statement.where(RecordStanding.id == result_id)
        reverse = before_id is not None
        if before_id is not None:
            # Walk away from the anchor in reversed display order; the page is flipped back below.
            statement = statement.where(
                RecordStanding.id > before_id if descending else RecordStanding.id < before_id
            ).order_by(RecordStanding.id.asc() if descending else RecordStanding.id.desc())
        else:
            if after_id is not None:
                statement = statement.where(
                    RecordStanding.id < after_id if descending else RecordStanding.id > after_id
                )
            statement = statement.order_by(RecordStanding.id.desc() if descending else RecordStanding.id.asc())
        if offset:
            statement = statement.offset(offset)
        rows = (await session.execute(statement.limit(limit))).all()
        if reverse:
            rows = list(reversed(rows))
        if not rows:
            return ()
        result_ids = [result.id for result, _definition in rows]
        holder_rows = (
            await session.execute(
                select(RecordResultHolder.result_id, RecordResultHolder.build_id)
                .where(RecordResultHolder.result_id.in_(result_ids))
                .order_by(RecordResultHolder.result_id, RecordResultHolder.rank, RecordResultHolder.build_id)
            )
        ).all()
        holders: dict[int, list[int]] = {}
        for holder_result_id, build_id in holder_rows:
            holders.setdefault(holder_result_id, []).append(build_id)
        return tuple(
            PublishedRecord(
                id=result.id,
                definition_id=definition.id,
                competition_id=definition.competition_id,
                title=definition.title,
                subtitle=definition.subtitle,
                record_class=definition.record_class,
                build_kind=definition.build_kind,
                version_scope=definition.version_scope,
                status=result.status,
                holder_build_ids=tuple(holders.get(result.id, ())),
                computed_at=result.computed_at.to_stdlib(),
            )
            for result, definition in rows
        )

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]:
        """Return exact categories previously accepted through public lookup."""
        session = self._session()
        keys = (
            await session.execute(
                select(RecordRule.category_key)
                .where(
                    RecordRule.build_kind == kind,
                    RecordRule.materialization_source == RecordMaterializationSource.PUBLIC_LOOKUP,
                )
                .distinct()
            )
        ).scalars()
        identities: list[CategoryIdentity] = []
        for key in keys:
            identity = parse_category_key(key)
            if identity is not None:
                identities.append(identity)
        return tuple(identities)

    async def get_definition_identity(self, definition_id: int) -> CategoryIdentity | None:
        """Return the parsed category identity of one stored definition."""
        session = self._session()
        key = await session.scalar(select(RecordRule.category_key).where(RecordRule.id == definition_id))
        if key is None:
            return None
        identity = parse_category_key(key)
        if identity is None:
            raise DataIntegrityError(
                tr(t"Record definition {definition_id} stores a category key that does not parse.")
            )
        return identity

    async def save_requested_category(
        self,
        ruleset_id: int,
        category: CategoryIdentity,
        titles: Mapping[RecordClass, FormattedTitle],
    ) -> None:
        """Persist an accepted exact category so future rebuilds retain it."""
        session = self._session()
        await self._write_definitions(
            session,
            ruleset_id=ruleset_id,
            kind=category.kind,
            scope=VersionScope.ALL_TIME,
            version_id=None,
            writes=tuple(
                _DefinitionWrite(
                    record_class,
                    category.key,
                    title,
                    RecordMaterializationSource.PUBLIC_LOOKUP,
                    tuple((RecordFacetKind.RESTRICTION, facet_id) for facet_id in category.restriction_ids),
                )
                for record_class, title in titles.items()
            ),
        )

    async def enqueue(self, kind: BuildKind, *, build_id: int | None, reason: str) -> None:
        """Enqueue a durable full-kind rebuild hint."""
        scope_key = f"{kind.value}:{build_id if build_id is not None else '*'}"
        session = self._session()
        statement = (
            insert(RecordRecomputeQueueItem)
            .values(
                scope_key=scope_key,
                build_kind=kind,
                build_id=build_id,
                reasons=[reason],
            )
            .on_conflict_do_update(
                index_elements=[RecordRecomputeQueueItem.scope_key],
                set_={
                    "reasons": [reason],
                    "enqueued_at": func.now(),
                    "available_at": func.now(),
                    "locked_at": None,
                    "claim_token": None,
                    "last_error": None,
                },
            )
        )
        await session.execute(statement)

    async def claim_recompute_kinds(self, *, limit: int) -> RecomputeLease:
        """Lease queued scopes and return their distinct build kinds.

        The caller must commit the claims before computing. Expired claims become
        eligible again through the shared queue visibility timeout.
        """
        rows = await self._recompute_queue.claim(limit=limit, session=self._session())
        return RecomputeLease(
            kinds=tuple(dict.fromkeys(row.build_kind for row in rows)),
            claim_tokens=tuple(self._recompute_queue.token_of(row) for row in rows),
        )

    async def complete_recompute(self, lease: RecomputeLease) -> None:
        """Acknowledge only scopes whose claim tokens still match this lease.

        Enqueueing resets a scope's claim; reclaiming gives it a new token. Either
        change prevents this acknowledgement from deleting the newer work.
        """
        await self._recompute_queue.complete_batch(lease.claim_tokens, session=self._session())

    async def fail_recompute(self, lease: RecomputeLease, error: str) -> None:
        """Release the failed leases this worker still owns for a later retry."""
        await self._recompute_queue.fail_batch(lease.claim_tokens, error=error, session=self._session())

    async def _list_doors(self, session: AsyncSession) -> Sequence[RecordSourceCandidate]:
        statement = (
            select(DoorBuild)
            .where(DoorBuild.submission_status == Status.CONFIRMED)
            .options(
                selectinload(DoorBuild.tags).selectinload(TagAssignment.definition),
                selectinload(DoorBuild.build_versions),
            )
        )
        doors = tuple((await session.execute(statement)).unique().scalars())
        timing_by_build = await _door_timings(session, tuple(door.id for door in doors))
        return tuple(_door_candidate(door, timing_by_build.get(door.id, ())) for door in doors)

    async def _list_extenders(self, session: AsyncSession) -> Sequence[RecordSourceCandidate]:
        statement = (
            select(ExtenderBuild)
            .where(ExtenderBuild.submission_status == Status.CONFIRMED)
            .options(
                selectinload(ExtenderBuild.tags).selectinload(TagAssignment.definition),
                selectinload(ExtenderBuild.build_versions),
            )
        )
        extenders = tuple((await session.execute(statement)).unique().scalars())
        timing_by_build = await _extender_timings(session, tuple(extender.id for extender in extenders))
        return tuple(
            candidate
            for extender in extenders
            if (candidate := _extender_candidate(extender, timing_by_build.get(extender.id, ()))) is not None
        )

    async def _ensure_definitions(self, session: AsyncSession, batch: ComputationBatch) -> tuple[RecordRule, ...]:
        return await self._write_definitions(
            session,
            ruleset_id=batch.ruleset_id,
            kind=batch.kind,
            scope=VersionScope.ALL_TIME if batch.version_id is None else VersionScope.CURRENT,
            version_id=batch.version_id,
            writes=tuple(
                _DefinitionWrite(
                    computed.record_class,
                    computed.competition.identity.key,
                    computed.title,
                    RecordMaterializationSource(computed.competition.source),
                    tuple((RecordFacetKind(facet.kind), facet.id) for facet in computed.competition.facets),
                )
                for computed in batch.records
            ),
        )

    async def _write_definitions(
        self,
        session: AsyncSession,
        *,
        ruleset_id: int,
        kind: BuildKind,
        scope: VersionScope,
        version_id: int | None,
        writes: Sequence[_DefinitionWrite],
    ) -> tuple[RecordRule, ...]:
        """Persist one scope in bulk, preserving stable IDs and explicit public requests."""
        if not writes:
            return ()
        # Sort conflict keys consistently so concurrent lookups acquire row locks in the same order.
        ordered = sorted({write.key: write for write in writes}.values(), key=lambda write: write.key)
        scope_values: dict[str, object] = {"build_kind": kind, "version_scope": scope, "version_id": version_id}
        identities = [
            dict(scope_values, record_class=write.record_class, category_key=write.category_key) for write in ordered
        ]
        # RETURNING enables SQLAlchemy's paged insertmanyvalues path instead of one statement
        # per rule. Passing parameter rows separately keeps large catalogues below bind limits.
        await session.execute(
            insert(RecordSeries)
            .on_conflict_do_nothing(constraint="record_competitions_identity_key")
            .returning(RecordSeries.public_id),
            identities,
        )
        competitions = await session.scalars(
            select(RecordSeries).where(
                RecordSeries.build_kind == kind,
                RecordSeries.version_scope == scope,
                RecordSeries.version_id.is_not_distinct_from(version_id),
                RecordSeries.record_class.in_({write.record_class for write in ordered}),
                RecordSeries.category_key.in_({write.category_key for write in ordered}),
            )
        )
        public_ids = {(row.record_class, row.category_key): row.public_id for row in competitions}
        statement = insert(RecordRule)
        statement = statement.on_conflict_do_update(
            constraint="record_definitions_identity_key",
            set_={
                "title": statement.excluded.title,
                "subtitle": statement.excluded.subtitle,
                "title_diagnostics": statement.excluded.title_diagnostics,
                "materialization_source": case(
                    (
                        statement.excluded.materialization_source == RecordMaterializationSource.PUBLIC_LOOKUP,
                        statement.excluded.materialization_source,
                    ),
                    else_=RecordRule.materialization_source,
                ),
            },
        ).returning(RecordRule)
        definitions = await session.scalars(
            statement,
            [
                dict(
                    identity,
                    ruleset_id=ruleset_id,
                    competition_id=public_ids[write.key],
                    title=write.title.title,
                    subtitle=write.title.subtitle,
                    title_diagnostics=[diagnostic.as_dict() for diagnostic in write.title.diagnostics],
                    materialization_source=write.source,
                )
                for identity, write in zip(identities, ordered, strict=True)
            ],
            execution_options={"render_nulls": True},
        )
        by_key = {(row.record_class, row.category_key): row for row in definitions}
        facets = {
            (by_key[write.key].id, facet_kind, facet_id): order
            for write in ordered
            for order, (facet_kind, facet_id) in enumerate(write.facets)
        }
        if facets:
            await session.execute(
                insert(RecordDefinitionFacet).on_conflict_do_nothing().returning(RecordDefinitionFacet.definition_id),
                [
                    {
                        "definition_id": definition_id,
                        "facet_kind": facet_kind,
                        "facet_id": facet_id,
                        "display_order": order,
                    }
                    for (definition_id, facet_kind, facet_id), order in sorted(facets.items())
                ],
            )
        return tuple(by_key[write.key] for write in writes)


async def _door_timings(
    session: AsyncSession,
    build_ids: tuple[int, ...],
) -> dict[int, tuple[TimingVariant, ...]]:
    if not build_ids:
        return {}
    rows = (await session.execute(select(DoorTimingVariant).where(DoorTimingVariant.build_id.in_(build_ids)))).scalars()
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
    session: AsyncSession,
    build_ids: tuple[int, ...],
) -> dict[int, tuple[TimingVariant, ...]]:
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


def _door_candidate(door: DoorBuild, timing: tuple[TimingVariant, ...]) -> RecordSourceCandidate:
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
    restrictions = _restriction_facets(door.tags)
    patterns = _pattern_facets(door.tags)
    is_expandable = any(facet.stable_key == "expandable" for facet in restrictions)
    return RecordSourceCandidate(
        kind=BuildKind.DOOR,
        candidate=RecordCandidate(
            build_id=door.id,
            completion_at=_as_datetime(door.completion_at),
            fixed_volume=door.width * door.height * depth
            if door.width is not None and door.height is not None and not is_expandable
            else None,
            timing_variants=timing or (fallback_timing,),
        ),
        version_ids=frozenset(version.version_id for version in door.build_versions),
        restrictions=restrictions,
        types=patterns,
        door=DoorCategory(
            wiring_restrictions=(),
            animated_restrictions=(),
            size=_door_size(door),
            types=tuple(pattern.category_name for pattern in patterns) or ("Regular",),
            orientation=door.orientation,
        ),
    )


def _extender_candidate(
    extender: ExtenderBuild,
    timing: tuple[TimingVariant, ...],
) -> RecordSourceCandidate | None:
    if extender.orientation is None or extender.extension_length is None:
        return None
    depth = extender.depth or 1
    restrictions = _restriction_facets(extender.tags)
    patterns = _pattern_facets(extender.tags)
    is_expandable = any(facet.stable_key == "expandable" for facet in restrictions)
    type_names = tuple(pattern.category_name for pattern in patterns)
    if not type_names and extender.extender_type is not None:
        type_names = (extender.extender_type,)
    return RecordSourceCandidate(
        kind=BuildKind.EXTENDER,
        candidate=RecordCandidate(
            build_id=extender.id,
            completion_at=_as_datetime(extender.completion_at),
            fixed_volume=extender.width * extender.height * depth
            if extender.width is not None and extender.height is not None and not is_expandable
            else None,
            timing_variants=timing,
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


def _restriction_facets(assignments: Sequence[TagAssignment]) -> tuple[CandidateFacet, ...]:
    return tuple(
        CandidateFacet(
            id=assignment.tag_id,
            kind="restriction",
            name=assignment.definition.display_name,
            restriction_type=assignment.definition.restriction_type,
            stable_key=assignment.definition.stable_key,
            value_type=assignment.value_type,
            assigned_value=_assignment_value(assignment),
            record_operator=assignment.definition.record_operator,
            render_template=assignment.definition.render_template,
            display_unit=assignment.display_unit or assignment.definition.default_display_unit,
        )
        for assignment in assignments
        if _is_record_tag(assignment.definition, "restriction")
    )


def _pattern_facets(assignments: Sequence[TagAssignment]) -> tuple[CandidateFacet, ...]:
    return tuple(
        CandidateFacet(
            id=assignment.tag_id,
            kind="pattern",
            name=assignment.definition.display_name,
            stable_key=assignment.definition.stable_key,
            value_type=assignment.value_type,
            assigned_value=_assignment_value(assignment),
            render_template=assignment.definition.render_template,
            display_unit=assignment.display_unit or assignment.definition.default_display_unit,
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


def _serialize_gaps(computed: ComputedRecord) -> dict[str, object]:
    return {"missing": [{"build_id": gap.build_id, "field": gap.field} for gap in computed.resolution.gaps]}


def _holder_title(computed: ComputedRecord, build_id: int) -> str:
    suffix = " [BROKEN]" if build_id in computed.broken_holder_ids else ""
    return f"{computed.title.title}{suffix}"


def _provisional_build_id(computed: ComputedRecord) -> int | None:
    return next(iter(computed.resolution.provisional_holder_ids), None)


def _metric_snapshot(computed: ComputedRecord, candidate: RecordCandidate) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    if computed.record_class in (
        RecordClass.FASTEST_SMALLEST,
        RecordClass.SMALLEST,
        RecordClass.SMALLEST_FASTEST,
    ):
        snapshot["volume"] = candidate.fixed_volume
    if computed.record_class is RecordClass.FIRST:
        snapshot["completion_at"] = candidate.completion_at.isoformat() if candidate.completion_at is not None else None
        return snapshot
    reduction = reduce_timing_variants(candidate.timing_variants)
    snapshot["timing"] = list(reduction.timing.values) if reduction.timing is not None else None
    return snapshot


def _gap_from_row(definition: RecordRule, result: RecordStanding) -> RecordGap:
    missing = result.gap_reasons.get("missing", [])
    entries = cast(list[object], missing) if isinstance(missing, list) else []
    build_ids: list[int] = []
    fields: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        build_id = entry.get("build_id")
        field = entry.get("field")
        if isinstance(build_id, int):
            build_ids.append(build_id)
        if isinstance(field, str):
            fields.append(field)
    return RecordGap(
        definition_id=definition.id,
        title=definition.title,
        subtitle=definition.subtitle,
        record_class=definition.record_class,
        build_ids=tuple(build_ids),
        fields=tuple(fields),
    )


def parse_category_key(key: str) -> CategoryIdentity | None:
    """Parse a stored category key, returning `None` for unrecognized syntax.

    This checks the serialized structure, not whether the referenced tags exist
    or their operators and values form a valid competition.
    """
    try:
        kind_value, remainder = key.split(":", maxsplit=1)
        base_key, restriction_part = remainder.rsplit(":r[", maxsplit=1)
        raw_ids, separator, raw_values = restriction_part.partition("]:p[")
        if not separator or not raw_values.endswith("]"):
            return None
        restriction_ids = tuple(int(value) for value in raw_ids.split(",") if value)
        values = tuple(
            (int(tag_id), operator, value)
            for item in raw_values.removesuffix("]").split(",")
            if item
            for tag_id, operator, value in (item.split(":", maxsplit=2),)
        )
        return CategoryIdentity(BuildKind(kind_value), base_key, restriction_ids, values)
    except ValueError, TypeError:
        return None


def _as_datetime(value: Instant | None) -> datetime | None:
    return value.to_stdlib() if value is not None else None


def _as_instant(value: datetime | None) -> Instant | None:
    return Instant(value) if value is not None else None


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))
