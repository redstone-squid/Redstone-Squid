"""PostgreSQL candidate queries and atomic record-run persistence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from whenever import Instant

from squid.builds.domain import Status
from squid.builds.infrastructure.models import BuildRestriction, BuildType, Door, Extender
from squid.records.application.models import (
    CandidateFacet,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecord,
    RecordGap,
    RecordSourceCandidate,
    TitleDiagnosticGap,
)
from squid.records.domain import (
    BuildKind,
    DoorCategory,
    ExtenderCategory,
    RecordCandidate,
    RecordClass,
    ResolutionStatus,
    TimingVariant,
    VersionScope,
    reduce_timing_variants,
)
from squid.records.infrastructure.models import (
    DoorTimingVariant,
    ExtenderTimingVariant,
    RecordComputationRun,
    RecordDefinition,
    RecordDefinitionFacet,
    RecordHolderHistory,
    RecordRecomputeQueueItem,
    RecordResult,
    RecordResultHolder,
    RecordRuleset,
)

RULESET_DOCUMENT_HASH = "312af53ee50a0cb0cee37673a763a6072321039777451997acc23cb26a6ba9ac"
CALCULATOR_VERSION = "1"
FORMATTER_VERSION = "2"


class PostgresRecordRepository:
    """Load record candidates and atomically publish computation runs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_confirmed(self, kind: BuildKind) -> Sequence[RecordSourceCandidate]:
        """Load confirmed fixed candidates of one supported kind."""
        async with self._session_factory() as session:
            if kind is BuildKind.DOOR:
                return await self._list_doors(session)
            if kind is BuildKind.EXTENDER:
                return await self._list_extenders(session)
            return ()

    async def active_ruleset_id(self) -> int:
        """Return the running ruleset, activating and queuing it when needed."""
        async with self._session_factory() as session, session.begin():
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
                        build_kind=kind.value,
                        reasons=["ruleset_activation"],
                    )
                    .on_conflict_do_update(
                        index_elements=[RecordRecomputeQueueItem.scope_key],
                        set_={
                            "reasons": ["ruleset_activation"],
                            "enqueued_at": func.now(),
                            "attempts": 0,
                            "locked_at": None,
                            "last_error": None,
                        },
                    )
                )
            return ruleset.id

    async def active_current_version_id(self) -> int | None:
        """Return the version pinned by the newest active current-scope run."""
        async with self._session_factory() as session:
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
        """Persist and activate a complete run in one transaction."""
        async with self._session_factory() as session, session.begin():
            await _advisory_lock(
                session,
                f"record-run:{batch.kind.value}:{batch.version_id}",
            )
            run = RecordComputationRun(
                ruleset_id=batch.ruleset_id,
                build_kind=batch.kind.value,
                version_id=batch.version_id,
                status="running",
            )
            session.add(run)
            await session.flush()

            for computed in batch.records:
                definition = await self._ensure_definition(session, batch.ruleset_id, computed)
                result = RecordResult(
                    run_id=run.id,
                    definition_id=definition.id,
                    status=computed.resolution.status.value,
                    gap_reasons=_serialize_gaps(computed),
                    provisional_build_id=_provisional_build_id(computed),
                    history_complete=computed.history_complete,
                )
                session.add(result)
                await session.flush()
                for build_id in computed.resolution.holder_ids:
                    candidate = next(
                        candidate for candidate in computed.competition.candidates if candidate.build_id == build_id
                    )
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

                predecessor_id: int | None = None
                for period in computed.history:
                    for build_id in period.build_ids:
                        candidate = next(
                            candidate for candidate in computed.competition.candidates if candidate.build_id == build_id
                        )
                        history = RecordHolderHistory(
                            run_id=run.id,
                            definition_id=definition.id,
                            build_id=build_id,
                            predecessor_id=predecessor_id,
                            held_from=Instant.from_py_datetime(period.held_from),
                            held_until=Instant.from_py_datetime(period.held_until)
                            if period.held_until is not None
                            else None,
                            metric_snapshot=_metric_snapshot(computed, candidate),
                        )
                        session.add(history)
                        await session.flush()
                        predecessor_id = history.id

            version_predicate = (
                RecordComputationRun.version_id.is_(None)
                if batch.version_id is None
                else RecordComputationRun.version_id == batch.version_id
            )
            await session.execute(
                update(RecordComputationRun)
                .where(
                    RecordComputationRun.build_kind == batch.kind.value,
                    version_predicate,
                    RecordComputationRun.is_active.is_(True),
                    RecordComputationRun.id != run.id,
                )
                .values(is_active=False)
            )
            await session.flush()
            run.status = "completed"
            run.completed_at = Instant.now()
            run.is_active = True
            await session.flush()
            return run.id

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        """List decisive gaps from currently active computation runs."""
        async with self._session_factory() as session:
            statement = (
                select(RecordDefinition, RecordResult)
                .join(RecordResult, RecordResult.definition_id == RecordDefinition.id)
                .join(RecordComputationRun, RecordComputationRun.id == RecordResult.run_id)
                .where(
                    RecordComputationRun.is_active.is_(True),
                    RecordResult.status == ResolutionStatus.UNRESOLVED.value,
                )
            )
            if kind is not None:
                statement = statement.where(RecordDefinition.build_kind == kind.value)
            rows = (await session.execute(statement)).all()
            return tuple(_gap_from_row(definition, result) for definition, result in rows)

    async def list_title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]:
        """List active canonical titles containing formatter diagnostics."""
        async with self._session_factory() as session:
            statement = (
                select(RecordDefinition)
                .join(RecordResult, RecordResult.definition_id == RecordDefinition.id)
                .join(RecordComputationRun, RecordComputationRun.id == RecordResult.run_id)
                .where(
                    RecordComputationRun.is_active.is_(True),
                    RecordDefinition.title_diagnostics != [],
                )
                .distinct()
                .order_by(RecordDefinition.id)
            )
            if kind is not None:
                statement = statement.where(RecordDefinition.build_kind == kind.value)
            definitions = (await session.execute(statement)).scalars()
            return tuple(
                TitleDiagnosticGap(
                    definition_id=definition.id,
                    title=definition.title,
                    diagnostics=tuple(definition.title_diagnostics),
                )
                for definition in definitions
            )

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]:
        """Return exact categories previously accepted through public lookup."""
        async with self._session_factory() as session:
            keys = (
                await session.execute(
                    select(RecordDefinition.category_key)
                    .where(
                        RecordDefinition.build_kind == kind.value,
                        RecordDefinition.materialization_source == "public_lookup",
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

    async def save_requested_category(self, ruleset_id: int, category: CategoryIdentity) -> None:
        """Persist an accepted exact category so future rebuilds retain it."""
        async with self._session_factory() as session, session.begin():
            for record_class in (RecordClass.SMALLEST, RecordClass.FASTEST):
                statement = select(RecordDefinition).where(
                    RecordDefinition.ruleset_id == ruleset_id,
                    RecordDefinition.record_class == record_class.value,
                    RecordDefinition.build_kind == category.kind.value,
                    RecordDefinition.version_scope == VersionScope.ALL_TIME.value,
                    RecordDefinition.version_id.is_(None),
                    RecordDefinition.category_key == category.key,
                )
                definition = (await session.execute(statement)).scalar_one_or_none()
                if definition is None:
                    definition = RecordDefinition(
                        ruleset_id=ruleset_id,
                        record_class=record_class.value,
                        build_kind=category.kind.value,
                        version_scope=VersionScope.ALL_TIME.value,
                        version_id=None,
                        category_key=category.key,
                        title=f"{record_class.value.replace('_', ' ').title()} {category.base_key}",
                        subtitle=None,
                        title_diagnostics=[],
                        materialization_source="public_lookup",
                    )
                    session.add(definition)
                    await session.flush()
                    for order, facet_id in enumerate(category.restriction_ids):
                        session.add(
                            RecordDefinitionFacet(
                                definition_id=definition.id,
                                facet_kind="restriction",
                                facet_id=facet_id,
                                display_order=order,
                            )
                        )

    async def enqueue(self, kind: BuildKind, *, build_id: int | None, reason: str) -> None:
        """Enqueue a durable full-kind rebuild hint."""
        scope_key = f"{kind.value}:{build_id if build_id is not None else '*'}"
        async with self._session_factory() as session, session.begin():
            statement = (
                insert(RecordRecomputeQueueItem)
                .values(
                    scope_key=scope_key,
                    build_kind=kind.value,
                    build_id=build_id,
                    reasons=[reason],
                )
                .on_conflict_do_update(
                    index_elements=[RecordRecomputeQueueItem.scope_key],
                    set_={
                        "reasons": [reason],
                        "enqueued_at": func.now(),
                        "locked_at": None,
                        "last_error": None,
                    },
                )
            )
            await session.execute(statement)

    async def claim_recompute_kinds(self, *, limit: int) -> Sequence[BuildKind]:
        """Lease queued scopes without holding locks during computation."""
        async with self._session_factory() as session, session.begin():
            statement = (
                select(RecordRecomputeQueueItem)
                .where(RecordRecomputeQueueItem.locked_at.is_(None))
                .order_by(RecordRecomputeQueueItem.enqueued_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            items = tuple((await session.execute(statement)).scalars())
            now = Instant.now()
            for item in items:
                item.locked_at = now
                item.attempts += 1
            return tuple(dict.fromkeys(BuildKind(item.build_kind) for item in items))

    async def complete_recompute(self, kinds: Sequence[BuildKind]) -> None:
        """Acknowledge successfully rebuilt leased scopes."""
        if not kinds:
            return
        async with self._session_factory() as session, session.begin():
            await session.execute(
                delete(RecordRecomputeQueueItem).where(
                    RecordRecomputeQueueItem.build_kind.in_(kind.value for kind in kinds),
                    RecordRecomputeQueueItem.locked_at.is_not(None),
                )
            )

    async def fail_recompute(self, kinds: Sequence[BuildKind], error: str) -> None:
        """Release failed leases for a later retry."""
        if not kinds:
            return
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(RecordRecomputeQueueItem)
                .where(
                    RecordRecomputeQueueItem.build_kind.in_(kind.value for kind in kinds),
                    RecordRecomputeQueueItem.locked_at.is_not(None),
                )
                .values(locked_at=None, last_error=error)
            )

    async def _list_doors(self, session: AsyncSession) -> Sequence[RecordSourceCandidate]:
        statement = (
            select(Door)
            .where(Door.submission_status == Status.CONFIRMED)
            .options(
                selectinload(Door.build_restrictions).selectinload(BuildRestriction.restriction),
                selectinload(Door.build_types).selectinload(BuildType.type),
                selectinload(Door.build_versions),
            )
        )
        doors = tuple((await session.execute(statement)).unique().scalars())
        timing_by_build = await _door_timings(session, tuple(door.id for door in doors))
        return tuple(_door_candidate(door, timing_by_build.get(door.id, ())) for door in doors)

    async def _list_extenders(self, session: AsyncSession) -> Sequence[RecordSourceCandidate]:
        statement = (
            select(Extender)
            .where(Extender.submission_status == Status.CONFIRMED)
            .options(
                selectinload(Extender.build_restrictions).selectinload(BuildRestriction.restriction),
                selectinload(Extender.build_types).selectinload(BuildType.type),
                selectinload(Extender.build_versions),
            )
        )
        extenders = tuple((await session.execute(statement)).unique().scalars())
        timing_by_build = await _extender_timings(session, tuple(extender.id for extender in extenders))
        return tuple(
            candidate
            for extender in extenders
            if (candidate := _extender_candidate(extender, timing_by_build.get(extender.id, ()))) is not None
        )

    async def _ensure_definition(
        self,
        session: AsyncSession,
        ruleset_id: int,
        computed: ComputedRecord,
    ) -> RecordDefinition:
        version_predicate = (
            RecordDefinition.version_id.is_(None)
            if computed.version_id is None
            else RecordDefinition.version_id == computed.version_id
        )
        statement = select(RecordDefinition).where(
            RecordDefinition.ruleset_id == ruleset_id,
            RecordDefinition.record_class == computed.record_class.value,
            RecordDefinition.build_kind == computed.competition.identity.kind.value,
            RecordDefinition.version_scope == computed.scope.value,
            version_predicate,
            RecordDefinition.category_key == computed.competition.identity.key,
        )
        definition = (await session.execute(statement)).scalar_one_or_none()
        if definition is not None:
            definition.title = computed.title.title
            definition.subtitle = computed.title.subtitle
            definition.title_diagnostics = [diagnostic.as_dict() for diagnostic in computed.title.diagnostics]
            await self._ensure_facets(session, definition.id, computed)
            return definition

        definition = RecordDefinition(
            ruleset_id=ruleset_id,
            record_class=computed.record_class.value,
            build_kind=computed.competition.identity.kind.value,
            version_scope=computed.scope.value,
            version_id=computed.version_id,
            category_key=computed.competition.identity.key,
            title=computed.title.title,
            subtitle=computed.title.subtitle,
            title_diagnostics=[diagnostic.as_dict() for diagnostic in computed.title.diagnostics],
            materialization_source=computed.competition.source,
        )
        session.add(definition)
        await session.flush()
        await self._ensure_facets(session, definition.id, computed)
        return definition

    async def _ensure_facets(
        self,
        session: AsyncSession,
        definition_id: int,
        computed: ComputedRecord,
    ) -> None:
        existing = set(
            (
                await session.execute(
                    select(RecordDefinitionFacet.facet_kind, RecordDefinitionFacet.facet_id).where(
                        RecordDefinitionFacet.definition_id == definition_id
                    )
                )
            ).tuples()
        )
        for order, facet in enumerate(computed.competition.facets):
            if (facet.kind, facet.id) not in existing:
                session.add(
                    RecordDefinitionFacet(
                        definition_id=definition_id,
                        facet_kind=facet.kind,
                        facet_id=facet.id,
                        display_order=order,
                    )
                )


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


def _door_candidate(door: Door, timing: tuple[TimingVariant, ...]) -> RecordSourceCandidate:
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
    is_expandable = any(
        association.restriction.name.casefold() == "expandable" for association in door.build_restrictions
    )
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
        restrictions=_restriction_facets(door.build_restrictions),
        types=_type_facets(door.build_types),
        door=DoorCategory(
            wiring_restrictions=(),
            animated_restrictions=(),
            size=_door_size(door),
            types=tuple(build_type.type.name for build_type in door.build_types) or ("Regular",),
            orientation=door.orientation,
        ),
    )


def _extender_candidate(
    extender: Extender,
    timing: tuple[TimingVariant, ...],
) -> RecordSourceCandidate | None:
    if extender.orientation is None or extender.extension_length is None:
        return None
    depth = extender.depth or 1
    is_expandable = any(
        association.restriction.name.casefold() == "expandable" for association in extender.build_restrictions
    )
    type_names = tuple(build_type.type.name for build_type in extender.build_types)
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
        restrictions=_restriction_facets(extender.build_restrictions),
        types=_type_facets(extender.build_types),
        extender=ExtenderCategory(
            wiring_restrictions=(),
            orientation=extender.orientation,
            length=extender.extension_length,
            types=type_names or ("Regular",),
        ),
    )


def _restriction_facets(restrictions: Sequence[BuildRestriction]) -> tuple[CandidateFacet, ...]:
    return tuple(
        CandidateFacet(
            id=association.restriction_id,
            kind="restriction",
            name=association.restriction.name,
            restriction_type=association.restriction.type,
        )
        for association in restrictions
    )


def _type_facets(types: Sequence[BuildType]) -> tuple[CandidateFacet, ...]:
    return tuple(
        CandidateFacet(id=association.type_id, kind="type", name=association.type.name) for association in types
    )


def _door_size(door: Door) -> str:
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
    if computed.record_class is RecordClass.SMALLEST:
        return {"volume": candidate.fixed_volume}
    reduction = reduce_timing_variants(candidate.timing_variants)
    return {"timing": list(reduction.timing.values) if reduction.timing is not None else None}


def _gap_from_row(definition: RecordDefinition, result: RecordResult) -> RecordGap:
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
        category_key=definition.category_key,
        record_class=RecordClass(definition.record_class),
        build_ids=tuple(build_ids),
        fields=tuple(fields),
    )


def parse_category_key(key: str) -> CategoryIdentity | None:
    try:
        kind_value, remainder = key.split(":", maxsplit=1)
        base_key, restriction_part = remainder.rsplit(":r[", maxsplit=1)
        raw_ids = restriction_part.removesuffix("]")
        restriction_ids = tuple(int(value) for value in raw_ids.split(",") if value)
        return CategoryIdentity(BuildKind(kind_value), base_key, restriction_ids)
    except (ValueError, TypeError):
        return None


def _as_datetime(value: Instant | None) -> datetime | None:
    return value.to_stdlib() if value is not None else None


def _as_instant(value: datetime | None) -> Instant | None:
    return Instant.from_py_datetime(value) if value is not None else None


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))
