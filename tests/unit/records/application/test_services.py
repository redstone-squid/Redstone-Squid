import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import cast, override
from unittest.mock import Mock, create_autospec

import anyio
import pytest

from squid.core.actors import ActingContext, AnonymousActor, UserActor
from squid.core.builds.application.queries import PublicBuildDimensions, PublicBuildStatus, PublicBuildSummary
from squid.core.builds.domain import BuildCategory
from squid.core.builds.domain.expansion import DoorExpansion, ExpansionBranch, ExpansionParameter, ExpansionTiming
from squid.core.builds.domain.expansion_evaluation import PointStatus
from squid.core.errors import DataIntegrityError, ServiceUnavailableError
from squid.core.pagination import FIRST_PAGE, PageSelector
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.errors import PermissionDeniedError
from squid.core.records.application.expansion_regions import (
    DoorRangeAnalysis,
    DoorRangeQuery,
    RangeLabels,
    analyze_door_widths,
)
from squid.core.records.application.live import DoorRecordLookup
from squid.core.records.application.models import (
    ALL_RECORDS,
    CandidateBuild,
    CandidateTag,
    CategoryIdentity,
    ComputationBatch,
    PriorPublication,
    PublishedRecordResult,
    RebuildSummary,
    RecomputeItem,
    RecomputeTarget,
    RecordDrift,
    RecordGap,
    RecordHolderPeriod,
    RecordListFilters,
    RecordListSort,
    RecordLookupRequest,
    RecordNameDiagnostic,
    RecordSibling,
    ScopedCategory,
)
from squid.core.records.application.ports import DoorRangeAnalyzer, RecomputeLease, StoredRecord
from squid.core.records.application.range_ports import RangePublicationRepository, RangePublicationUnitOfWork
from squid.core.records.application.range_publication import RangePublicationService
from squid.core.records.application.services import PublicRecordQueryService, RecordComputationService, RecordService
from squid.core.records.domain import (
    BuildKind,
    Candidate,
    DoorCategory,
    FormattedTitle,
    RecordCriterion,
    RecordFact,
    TimingVariant,
    VersionScope,
)
from squid.core.records.domain.models import RecordTagRole
from squid.core.records.domain.regions import RegionAnalysisStatus
from squid.core.records.errors import NoMatchingRecordCategoryError, RecordDefinitionNotFoundError
from squid.core.records.infrastructure.range_analyzer import SubprocessRangeAnalyzer
from tests.support.record_uow import RecordQueryScope, RecordScope


class FakeCandidates:
    def __init__(self, candidates: Sequence[CandidateBuild]) -> None:
        self.candidates = tuple(candidates)

    async def begin_computation(self) -> None:
        pass

    async def list_confirmed(self, kind: BuildKind) -> Sequence[CandidateBuild]:
        return tuple(candidate for candidate in self.candidates if candidate.kind is kind)

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
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.door is not None
            and candidate.door.orientation == orientation
            and (version_id is None or version_id in candidate.version_ids)
            and (
                candidate.expansion is not None
                or (
                    candidate.door_opening is not None
                    and candidate.door_opening[1:] == (height, depth)
                    and candidate.door_opening[0] >= minimum
                    and (maximum is None or candidate.door_opening[0] <= maximum)
                )
            )
        )


class FakeRuns:
    """Capture published batches and supply the configured record queries.

    RecordScope copies this state and publishes it on commit, so a failed
    publication leaves batches unchanged. `queued` holds leases to hand out;
    queue methods record calls without enforcing claim-token ownership.
    """

    def __init__(self) -> None:
        self.batches: list[ComputationBatch] = []
        self.requested: dict[BuildKind, list[CategoryIdentity]] = {}
        self.requested_titles: dict[str, dict[RecordCriterion, FormattedTitle]] = {}
        self.definition_records: dict[int, uuid.UUID] = {}
        self.record_requests: list[uuid.UUID] = []
        self.gap_rows: tuple[RecordGap, ...] = ()
        self.title_gap_rows: tuple[RecordNameDiagnostic, ...] = ()
        self.queued: tuple[RecomputeLease, ...] = ()
        self.completed: tuple[BuildKind, ...] = ()
        self.failed: tuple[BuildKind | None, str] | None = None
        self.current_version_id: int | None = None
        self.published_records: dict[int, PublishedRecordResult] = {}
        self.history: dict[uuid.UUID, tuple[RecordHolderPeriod, ...]] = {}

    async def active_rules_revision_id(self) -> int:
        return 7

    async def active_current_version_id(self) -> int | None:
        return self.current_version_id

    async def set_current_version(self, version_id: int | None) -> None:
        self.current_version_id = version_id

    async def input_generation(self, kind: BuildKind) -> int:
        return 1

    async def prior_publication(self, kind: BuildKind, build_ids: frozenset[int]) -> PriorPublication:
        return PriorPublication()

    async def pending_builds(self, kind: BuildKind) -> frozenset[int]:
        return frozenset()

    async def record_categories(self, record_ids: frozenset[uuid.UUID]) -> frozenset[ScopedCategory]:
        return frozenset()

    async def publish(self, batch: ComputationBatch) -> RebuildSummary:
        self.batches.append(batch)
        return RebuildSummary(run_ids=(len(self.batches),), records=len(batch.records))

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        return self.gap_rows

    async def list_title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordNameDiagnostic]:
        return self.title_gap_rows

    async def get_published_record(self, result_id: int) -> PublishedRecordResult | None:
        return self.published_records.get(result_id)

    async def get_latest_record(self, record_id: uuid.UUID) -> PublishedRecordResult | None:
        return next((result for result in self.published_records.values() if result.record_id == record_id), None)

    async def get_record_history(self, record_id: uuid.UUID) -> tuple[RecordHolderPeriod, ...]:
        return self.history.get(record_id, ())

    async def list_siblings(self, record_id: uuid.UUID) -> tuple[RecordSibling, ...]:
        return ()

    async def stored_record(self, record_id: uuid.UUID) -> StoredRecord | None:
        return None

    async def list_published_records(
        self,
        *,
        selector: PageSelector = FIRST_PAGE,
        sort: RecordListSort = RecordListSort.ID,
        filters: RecordListFilters = ALL_RECORDS,
        limit: int,
    ) -> Sequence[PublishedRecordResult]:
        assert sort is RecordListSort.ID
        ordered = sorted(
            (
                key
                for key, record in self.published_records.items()
                if filters.version_scope is None or record.version_scope is filters.version_scope
            ),
            reverse=True,
        )
        if selector.before_id is not None:
            kept = [rid for rid in ordered if rid > selector.before_id]
            return tuple(self.published_records[rid] for rid in kept[-limit:])
        if selector.after_id is not None:
            ordered = [rid for rid in ordered if rid < selector.after_id]
        return tuple(self.published_records[rid] for rid in ordered[selector.offset : selector.offset + limit])

    async def count_published_records(self, *, filters: RecordListFilters = ALL_RECORDS) -> int:
        return sum(
            filters.version_scope is None or record.version_scope is filters.version_scope
            for record in self.published_records.values()
        )

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]:
        return tuple(self.requested.get(kind, ()))

    async def definition_record_id(self, definition_id: int) -> uuid.UUID | None:
        return self.definition_records.get(definition_id)

    async def save_requested_category(
        self,
        rules_revision_id: int,
        category: CategoryIdentity,
        titles: Mapping[RecordCriterion, FormattedTitle],
    ) -> int:
        assert rules_revision_id == 7
        self.requested.setdefault(category.kind, []).append(category)
        self.requested_titles[category.key] = dict(titles)
        return len(titles)

    async def request_record_recompute(self, record_id: uuid.UUID) -> bool:
        self.record_requests.append(record_id)
        return True

    async def request_full_recompute(self, kinds: Sequence[BuildKind], *, target: RecomputeTarget) -> None:
        self.queued += tuple(
            RecomputeLease(kind=kind, full=True, backstop=target is RecomputeTarget.BACKSTOP) for kind in kinds
        )

    async def claim_recompute(self, *, limit: int) -> RecomputeLease:
        if not self.queued:
            return RecomputeLease(kind=None)
        lease, *rest = self.queued
        self.queued = tuple(rest)
        return lease

    async def complete_recompute(self, lease: RecomputeLease) -> None:
        assert lease.kind is not None
        self.completed += (lease.kind,)

    async def fail_recompute(self, lease: RecomputeLease, error: str) -> None:
        self.failed = (lease.kind, error)

    async def list_recompute_queue(self) -> Sequence[RecomputeItem]:
        return ()

    async def list_drift(
        self, *, offset: int, after_id: int | None, before_id: int | None, limit: int
    ) -> Sequence[RecordDrift]:
        return ()

    async def count_drift(self) -> int:
        return 0


def _full(*kinds: BuildKind) -> tuple[RecomputeLease, ...]:
    return tuple(RecomputeLease(kind=kind, full=True) for kind in kinds)


class FakePublicBuilds:
    def __init__(self, summaries: Sequence[PublicBuildSummary]) -> None:
        self.summaries = {summary.id: summary for summary in summaries}
        self.requests: list[tuple[int, ...]] = []

    async def get_public_summaries(
        self, build_ids: Sequence[int], *, context: ActingContext
    ) -> Sequence[PublicBuildSummary]:
        self.requests.append(tuple(build_ids))
        return tuple(self.summaries[build_id] for build_id in build_ids if build_id in self.summaries)


def _public_build(build_id: int) -> PublicBuildSummary:
    return PublicBuildSummary(
        id=build_id,
        revision=1,
        title=f"Build {build_id}",
        display_name=None,
        status=PublicBuildStatus.APPROVED,
        category=BuildCategory.DOOR,
        dimensions=PublicBuildDimensions(width=2, height=3, depth=4),
        creators=(),
        tags=(),
        preview=None,
        version_spec=None,
        versions=(),
        opening_time=None,
        closing_time=None,
        created_at=None,
        updated_at=None,
    )


def _published_record(*holder_build_ids: int) -> PublishedRecordResult:
    return PublishedRecordResult(
        result_id=7,
        definition_id=3,
        record_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        title="Fastest 2x3 door",
        subtitle=None,
        criterion=RecordCriterion.FASTEST,
        build_kind=BuildKind.DOOR,
        version_scope=VersionScope.ALL_TIME,
        category_key="door:door|2x3|t[]|Door:r[]:p[]",
        holder_build_ids=holder_build_ids,
        computed_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
    )


def _door(
    build_id: int,
    *,
    volume: int,
    opening: int,
    restrictions: tuple[CandidateTag, ...],
    version_ids: frozenset[int] = frozenset({3}),
) -> CandidateBuild:
    return CandidateBuild(
        kind=BuildKind.DOOR,
        candidate=Candidate(
            build_id=build_id,
            completion_at=datetime(2020, 1, build_id, tzinfo=UTC),
            fixed_volume=volume,
            timing_variants=(TimingVariant((opening, 2, 3, 2, 0, 0)),),
        ),
        version_ids=version_ids,
        restrictions=restrictions,
        types=(CandidateTag(id=20, kind=RecordTagRole.TYPE, name="Regular"),),
        door_opening=(2, 2, 1),
        door=DoorCategory(
            wiring_restrictions=(),
            animated_restrictions=(),
            size="2x2",
            types=("Regular",),
            orientation="Door",
        ),
    )


@pytest.mark.asyncio
async def test_rebuild_computes_eager_subsets_and_all_door_base_titles() -> None:
    flush = CandidateTag(id=1, kind=RecordTagRole.RESTRICTION, name="Flush", restriction_type="wiring-placement")
    observerless = CandidateTag(id=2, kind=RecordTagRole.RESTRICTION, name="Observerless", restriction_type="component")
    candidates = FakeCandidates(
        (
            _door(1, volume=10, opening=5, restrictions=(flush, observerless)),
            _door(2, volume=12, opening=4, restrictions=(flush,)),
        )
    )
    runs = FakeRuns()
    service = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))

    summary = await service.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))

    assert summary.run_ids == (1,)
    assert summary.records == 20
    batch = runs.batches[0]
    shared = [record for record in batch.records if record.category.identity.restriction_ids == (1,)]
    first = next(record for record in shared if record.criterion is RecordCriterion.FIRST)
    smallest = next(record for record in shared if record.criterion is RecordCriterion.SMALLEST)
    fastest = next(record for record in shared if record.criterion is RecordCriterion.FASTEST)
    fastest_smallest = next(record for record in shared if record.criterion is RecordCriterion.FASTEST_SMALLEST)
    smallest_fastest = next(record for record in shared if record.criterion is RecordCriterion.SMALLEST_FASTEST)
    assert first.resolution.holder_ids == (1,)
    assert smallest.resolution.holder_ids == (1,)
    assert fastest.resolution.holder_ids == (2,)
    assert fastest_smallest.resolution.holder_ids == (1,)
    assert smallest_fastest.resolution.holder_ids == (2,)
    assert smallest.title.title == "Smallest Flush 2x2 Door"
    assert tuple(period.build_ids for period in fastest.history) == ((1,), (2,))
    assert fastest.history[0].held_until == datetime(2020, 1, 2, tzinfo=UTC)


@pytest.mark.asyncio
async def test_parameterized_at_most_restriction_derives_weaker_thresholds() -> None:
    def m_wide(value: int) -> CandidateTag:
        return CandidateTag(
            id=7,
            kind=RecordTagRole.RESTRICTION,
            name="M Wide",
            restriction_type="miscellaneous",
            stable_key="m_wide",
            value_type="numeric",
            assigned_value=Decimal(value),
            record_operator="at_most",
            render_template="{value} Wide",
        )

    runs = FakeRuns()
    service = RecordComputationService(
        uow_factory=lambda: RecordScope(
            FakeCandidates(
                (
                    _door(1, volume=10, opening=5, restrictions=(m_wide(3),)),
                    _door(2, volume=8, opening=4, restrictions=(m_wide(4),)),
                )
            ),
            runs,
        )
    )

    await service.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))

    threshold_three = [
        record
        for record in runs.batches[0].records
        if record.category.identity.restriction_values == ((7, "at_most", "3"),)
    ]
    threshold_four = [
        record
        for record in runs.batches[0].records
        if record.category.identity.restriction_values == ((7, "at_most", "4"),)
    ]
    assert {record.resolution.holder_ids for record in threshold_three} == {(1,)}
    assert {record.criterion: record.resolution.holder_ids for record in threshold_four} == {
        RecordCriterion.FIRST: (1,),
        RecordCriterion.FASTEST: (2,),
        RecordCriterion.SMALLEST: (2,),
        RecordCriterion.FASTEST_SMALLEST: (2,),
        RecordCriterion.SMALLEST_FASTEST: (2,),
    }
    assert {record.title.title for record in threshold_four} == {
        "First 2x2 Door",
        "Fastest 2x2 Door",
        "Fastest Smallest 2x2 Door",
        "Smallest 2x2 Door",
        "Smallest Fastest 2x2 Door",
    }
    assert {record.title.subtitle for record in threshold_four} == {"4 Wide"}


@pytest.mark.asyncio
async def test_rebuild_publishes_an_empty_extender_batch() -> None:
    runs = FakeRuns()
    service = RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs))

    summary = await service.rebuild(kinds=(BuildKind.EXTENDER,), context=ActingContext.direct(AnonymousActor()))

    assert summary.run_ids == (1,)
    assert summary.records == 0
    assert runs.batches[0].kind is BuildKind.EXTENDER
    assert runs.batches[0].records == ()


@pytest.mark.asyncio
async def test_current_version_scope_filters_incompatible_candidates() -> None:
    candidates = FakeCandidates(
        (
            _door(1, volume=10, opening=5, restrictions=(), version_ids=frozenset({2})),
            _door(2, volume=12, opening=4, restrictions=(), version_ids=frozenset({3})),
        )
    )
    runs = FakeRuns()
    runs.current_version_id = 3
    service = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))

    await service.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))

    (batch,) = runs.batches
    assert {record.resolution.holder_ids for record in batch.records if record.version_id == 3} == {(2,)}
    assert {record.resolution.holder_ids for record in batch.records if record.version_id is None} == {(1,), (2,)}


@pytest.mark.asyncio
async def test_eager_categories_never_exceed_eight_restrictions() -> None:
    restrictions = tuple(
        CandidateTag(
            id=index,
            kind=RecordTagRole.RESTRICTION,
            name=f"Restriction {index}",
            restriction_type="component",
        )
        for index in range(1, 10)
    )
    runs = FakeRuns()
    service = RecordComputationService(
        uow_factory=lambda: RecordScope(
            FakeCandidates((_door(1, volume=10, opening=5, restrictions=restrictions),)), runs
        )
    )

    await service.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))

    assert max(len(record.category.identity.restriction_ids) for record in runs.batches[0].records) == 8
    assert all(len(record.category.identity.restriction_ids) <= 8 for record in runs.batches[0].records)


@pytest.mark.asyncio
async def test_lookup_saves_a_large_exact_category_that_rebuilds_keep() -> None:
    restrictions = tuple(
        CandidateTag(
            id=index,
            kind=RecordTagRole.RESTRICTION,
            name=f"Restriction {index}",
            restriction_type="component",
        )
        for index in range(1, 10)
    )
    source = _door(1, volume=10, opening=5, restrictions=restrictions)
    candidates = FakeCandidates((source,))
    runs = FakeRuns()
    computation = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))
    service = RecordService(
        computation,
        permissions=create_autospec(PermissionService, instance=True),
        uow_factory=lambda: RecordScope(candidates, runs),
    )

    queued = await service.lookup_or_materialize(
        RecordLookupRequest(
            kind=BuildKind.DOOR,
            base_key="door|2x2|t[20]|Door",
            restriction_ids=frozenset(range(1, 10)),
        ),
        context=ActingContext.direct(AnonymousActor()),
    )
    await computation.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))

    assert queued == len(RecordCriterion)
    identity = runs.requested[BuildKind.DOOR][0]
    assert identity.restriction_ids == tuple(range(1, 10))
    titles = runs.requested_titles[identity.key]
    assert set(titles) == set(RecordCriterion)
    assert all("2x2" in text.title for text in titles.values())
    assert all(identity.base_key not in text.title for text in titles.values())
    exact = [record for record in runs.batches[0].records if len(record.category.identity.restriction_ids) == 9]
    assert {record.criterion for record in exact} == set(RecordCriterion)
    assert all(record.category.source == "public_lookup" for record in exact)


@pytest.mark.asyncio
async def test_definition_recompute_queues_the_record_it_names() -> None:
    runs = FakeRuns()
    record_id = uuid.UUID(int=42)
    runs.definition_records[42] = record_id
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs)),
        permissions=create_autospec(PermissionService, instance=True),
        uow_factory=lambda: RecordScope(FakeCandidates(()), runs),
    )

    await service.request_definition_recompute(42, context=ActingContext.direct(AnonymousActor()))

    assert runs.record_requests == [record_id]


@pytest.mark.asyncio
async def test_definition_recompute_rejects_unknown_id() -> None:
    runs = FakeRuns()
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs)),
        permissions=create_autospec(PermissionService, instance=True),
        uow_factory=lambda: RecordScope(FakeCandidates(()), runs),
    )

    with pytest.raises(RecordDefinitionNotFoundError):
        await service.request_definition_recompute(999, context=ActingContext.direct(AnonymousActor()))


@pytest.mark.asyncio
async def test_lookup_rejects_category_without_confirmed_candidate() -> None:
    candidates = FakeCandidates((_door(1, volume=10, opening=5, restrictions=()),))
    runs = FakeRuns()
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)),
        permissions=create_autospec(PermissionService, instance=True),
        uow_factory=lambda: RecordScope(candidates, runs),
    )

    with pytest.raises(NoMatchingRecordCategoryError, match="No approved build"):
        await service.lookup_or_materialize(
            RecordLookupRequest(
                kind=BuildKind.DOOR,
                base_key="door|3x3|t[20]|Door",
                restriction_ids=frozenset(),
            ),
            context=ActingContext.direct(AnonymousActor()),
        )


@pytest.mark.asyncio
async def test_gaps_delegates_active_result_query() -> None:
    runs = FakeRuns()
    gap = RecordGap(
        definition_id=1,
        title="Fastest 2x2 Door",
        subtitle=None,
        criterion=RecordCriterion.FASTEST,
        build_ids=(4,),
        fields=(RecordFact.CLOSING,),
        build_kind=BuildKind.DOOR,
    )
    runs.gap_rows = (gap,)
    candidates = FakeCandidates(())
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)),
        permissions=create_autospec(PermissionService, instance=True),
        uow_factory=lambda: RecordScope(candidates, runs),
    )

    assert await service.gaps(kind=BuildKind.DOOR, context=ActingContext.direct(AnonymousActor())) == (gap,)


@pytest.mark.asyncio
async def test_public_record_query_preserves_holder_order() -> None:
    runs = FakeRuns()
    runs.published_records[7] = _published_record(41, 42)
    builds = FakePublicBuilds((_public_build(42), _public_build(41)))
    service = PublicRecordQueryService(builds, uow_factory=lambda: RecordQueryScope(runs))

    detail = await service.get(runs.published_records[7].record_id, context=ActingContext.direct(AnonymousActor()))

    assert detail is not None
    assert [build.id for build in detail.holder_builds] == [41, 42]
    assert builds.requests == [(41, 42)]


@pytest.mark.asyncio
async def test_public_record_query_rejects_unavailable_holder() -> None:
    runs = FakeRuns()
    runs.published_records[7] = _published_record(41, 42)
    service = PublicRecordQueryService(
        FakePublicBuilds((_public_build(41),)), uow_factory=lambda: RecordQueryScope(runs)
    )

    with pytest.raises(DataIntegrityError) as exc_info:
        await service.get(runs.published_records[7].record_id, context=ActingContext.direct(AnonymousActor()))

    assert exc_info.value.context == {
        "record_id": str(runs.published_records[7].record_id),
        "unavailable_holder_build_ids": [42],
    }


@pytest.mark.asyncio
async def test_public_record_query_shows_a_retired_record_with_its_history() -> None:
    runs = FakeRuns()
    retired_at = datetime(2026, 9, 1, tzinfo=UTC)
    record = replace(_published_record(), result_id=None, retired_at=retired_at, computed_at=retired_at)
    runs.published_records[7] = record
    runs.history[record.record_id] = (RecordHolderPeriod((41,), datetime(2020, 1, 1, tzinfo=UTC), retired_at),)
    builds = FakePublicBuilds((_public_build(41),))
    service = PublicRecordQueryService(builds, uow_factory=lambda: RecordQueryScope(runs))

    detail = await service.get(record.record_id, context=ActingContext.direct(AnonymousActor()))

    assert detail is not None
    assert detail.retired_at == retired_at
    assert detail.holder_builds == ()
    assert [period.build_ids for period in detail.history] == [(41,)]
    assert builds.requests == [(41,)]


@pytest.mark.asyncio
async def test_public_record_query_rejects_a_current_result_without_holders() -> None:
    runs = FakeRuns()
    runs.published_records[7] = _published_record()
    service = PublicRecordQueryService(FakePublicBuilds(()), uow_factory=lambda: RecordQueryScope(runs))

    with pytest.raises(DataIntegrityError, match="no holders"):
        await service.get(runs.published_records[7].record_id, context=ActingContext.direct(AnonymousActor()))


@pytest.mark.asyncio
async def test_public_record_query_returns_none_for_unknown_record() -> None:
    service = PublicRecordQueryService(FakePublicBuilds(()), uow_factory=lambda: RecordQueryScope(FakeRuns()))

    assert await service.get(uuid.UUID(int=999), context=ActingContext.direct(AnonymousActor())) is None


@pytest.mark.asyncio
async def test_title_gaps_delegates_active_definition_query() -> None:
    runs = FakeRuns()
    gap = RecordNameDiagnostic(
        definition_id=1,
        title="Fastest Mystery 2x2 Door",
        diagnostics=({"code": "unknown_token", "message": "Unknown term.", "terms": ["Mystery"]},),
        build_kind=BuildKind.DOOR,
    )
    runs.title_gap_rows = (gap,)
    candidates = FakeCandidates(())
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)),
        permissions=create_autospec(PermissionService, instance=True),
        uow_factory=lambda: RecordScope(candidates, runs),
    )

    assert await service.title_gaps(kind=BuildKind.DOOR, context=ActingContext.direct(AnonymousActor())) == (gap,)


@pytest.mark.asyncio
async def test_process_queue_publishes_and_acknowledges_one_kind_per_batch() -> None:
    runs = FakeRuns()
    runs.queued = _full(BuildKind.DOOR, BuildKind.EXTENDER)
    computation = RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs))

    result = await computation.process_queue(context=ActingContext.worker("test-worker"))

    assert result.kinds == (BuildKind.DOOR, BuildKind.EXTENDER)
    assert result.rebuild is not None
    assert result.rebuild.run_ids == (1, 2)
    assert runs.completed == (BuildKind.DOOR, BuildKind.EXTENDER)


@pytest.mark.asyncio
async def test_process_queue_computes_all_time_and_the_pinned_version() -> None:
    runs = FakeRuns()
    runs.queued = _full(BuildKind.DOOR)
    runs.current_version_id = 3
    candidates = FakeCandidates((_door(1, volume=10, opening=5, restrictions=()),))
    computation = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))

    await computation.process_queue(context=ActingContext.worker("test-worker"))

    (batch,) = runs.batches
    assert {record.version_id for record in batch.records} == {None, 3}


async def test_a_build_lease_recomputes_only_the_categories_of_that_build() -> None:
    flush = CandidateTag(id=1, kind=RecordTagRole.RESTRICTION, name="Flush", restriction_type="wiring-placement")
    three = replace(_door(2, volume=12, opening=4, restrictions=()), door_opening=(3, 2, 1))
    assert three.door is not None
    three = replace(three, door=replace(three.door, size="3x2"))
    runs = FakeRuns()
    runs.queued = (RecomputeLease(kind=BuildKind.DOOR, builds=frozenset({1})),)
    candidates = FakeCandidates((_door(1, volume=10, opening=5, restrictions=(flush,)), three))
    computation = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))

    await computation.process_queue(context=ActingContext.worker("test-worker"))

    (batch,) = runs.batches
    assert {identity.base_key for identity in (record.category.identity for record in batch.records)} == {
        "door|2x2|t[20]|Door"
    }
    assert {key for _version, key in batch.categories} == {
        "door:door|2x2|t[20]|Door:r[]:p[]",
        "door:door|2x2|t[20]|Door:r[1]:p[]",
    }


def test_requested_door_category_applies_types_restrictions_and_thresholds() -> None:
    flush = CandidateTag(id=1, kind=RecordTagRole.RESTRICTION, name="Flush", restriction_type="wiring-placement")
    width = CandidateTag(
        id=7,
        kind=RecordTagRole.RESTRICTION,
        name="Width",
        value_type="numeric",
        assigned_value=Decimal(3),
        record_operator="at_most",
    )
    source = _door(1, volume=100, opening=10, restrictions=(flush, width))
    family = replace(
        source,
        candidate=replace(source.candidate, build_id=2),
        expansion=DoorExpansion(
            parameters=(
                ExpansionParameter(
                    name="n", minimum=1, maximum_kind="unbounded", initial_steps=(), repeating_steps=(1,)
                ),
            ),
            branches=(ExpansionBranch(opening_width="n", opening_height="2", opening_depth="1", volume="10n"),),
        ),
    )
    wrong_types = replace(family, candidate=replace(family.candidate, build_id=4), types=())
    wrong_restrictions = replace(family, candidate=replace(family.candidate, build_id=5), restrictions=())
    lookup = DoorRecordLookup(
        opening=(2, 2, 1),
        type_ids=frozenset({20}),
        restriction_ids=frozenset({1, 7}),
        restriction_values=((7, "at_most", "4"),),
    )
    category = RecordComputationService(uow_factory=Mock()).requested_category(
        lookup.identity, (source, family, wrong_types, wrong_restrictions)
    )
    assert category is not None
    assert {candidate.build_id for candidate in category.candidates} == {1, 2}


@pytest.mark.parametrize("threshold", ["not a number", "NaN", "Infinity", "1e999999", "1e-999999", "2"])
def test_requested_door_category_rejects_incompatible_threshold_without_crashing(threshold: str) -> None:
    width = CandidateTag(
        id=7,
        kind=RecordTagRole.RESTRICTION,
        name="Width",
        value_type="numeric",
        assigned_value=Decimal(3),
        record_operator="at_most",
    )
    lookup = DoorRecordLookup(
        opening=(2, 2, 1),
        type_ids=frozenset({20}),
        restriction_ids=frozenset({7}),
        restriction_values=((7, "at_most", threshold),),
    )
    category = RecordComputationService(uow_factory=Mock()).requested_category(
        lookup.identity, (_door(1, volume=100, opening=10, restrictions=(width,)),)
    )
    assert category is None or not category.candidates


class RangeScope[CandidatesT, RunsT](RecordScope[CandidatesT, RunsT]):
    def __init__(self, candidates: CandidatesT, runs: RunsT) -> None:
        super().__init__(candidates, runs)
        self.record_ranges = create_autospec(RangePublicationRepository, instance=True)
        self.record_ranges.labels.return_value = (RangeLabels(),)


def _range_service(
    candidates: object, runs: FakeRuns, analyzer: DoorRangeAnalyzer, *, live_concurrency: int = 2
) -> RangePublicationService:
    def scope() -> RangePublicationUnitOfWork:
        return cast(RangePublicationUnitOfWork, RangeScope(candidates, runs))

    return RangePublicationService(
        RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs)),
        create_autospec(PermissionService, instance=True),
        uow_factory=scope,
        analyzer=analyzer,
        builds=FakePublicBuilds(()),
        live_concurrency=live_concurrency,
    )


async def test_width_lookup_filters_tags_and_versions_without_writes() -> None:
    fixed = _door(1, volume=1, opening=10, restrictions=())
    family = replace(
        fixed,
        candidate=replace(fixed.candidate, build_id=2),
        expansion=DoorExpansion(
            parameters=(
                ExpansionParameter(
                    name="n", minimum=1, maximum_kind="unbounded", initial_steps=(), repeating_steps=(1,)
                ),
            ),
            branches=(ExpansionBranch(opening_width="n", opening_height="2", opening_depth="1", volume="10n"),),
        ),
    )
    wrong_version = replace(family, candidate=replace(family.candidate, build_id=3), version_ids=frozenset({4}))
    wrong_type = replace(family, candidate=replace(family.candidate, build_id=4), types=())
    candidates = FakeCandidates((fixed, family, wrong_version, wrong_type))
    runs = FakeRuns()
    service = _range_service(candidates, runs, SubprocessRangeAnalyzer())
    result = await service.lookup(
        DoorRangeQuery(height=2, type_ids=frozenset({20}), version_id=3), context=ActingContext.direct(AnonymousActor())
    )
    assert result.analysis.status is RegionAnalysisStatus.EXACT
    assert [(r.domain.minimum, r.domain.maximum, r.resolution.holder_ids) for r in result.analysis.regions] == [
        (1, 1, (2,)),
        (2, 2, (1,)),
        (3, None, (2,)),
    ]
    assert {c.build_id for c in result.configurations} == {1, 2}
    assert runs.batches == []
    assert runs.requested == {}


async def test_range_admission_precedes_loading_and_releases_on_cancellation() -> None:
    candidates = create_autospec(FakeCandidates, instance=True)
    candidates.list_for_door_widths.return_value = ()
    runs = FakeRuns()
    analyzer = create_autospec(DoorRangeAnalyzer, instance=True)
    started = anyio.Event()

    async def blocked(sources: Sequence[CandidateBuild], query: DoorRangeQuery) -> DoorRangeAnalysis:
        started.set()
        await anyio.sleep_forever()
        raise AssertionError("Cancelled analysis returned")

    analyzer.analyze.side_effect = blocked
    service = _range_service(candidates, runs, analyzer, live_concurrency=1)
    request = DoorRangeQuery(height=3)
    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(partial(service.lookup, context=ActingContext.direct(AnonymousActor())), request)
            await started.wait()
            with pytest.raises(ServiceUnavailableError):
                await service.lookup(request, context=ActingContext.direct(AnonymousActor()))
            candidates.list_for_door_widths.assert_awaited_once()
            group.cancel_scope.cancel()
    analyzer.analyze.side_effect = None
    analyzer.analyze.return_value = analyze_door_widths((), height=3)
    result = await service.lookup(request, context=ActingContext.direct(AnonymousActor()))
    assert result.query == request
    assert result.analysis.status is RegionAnalysisStatus.EXACT
    assert candidates.list_for_door_widths.await_count == 2
    assert runs.batches == []


async def test_concrete_queue_completes_without_range_work() -> None:
    runs = FakeRuns()
    runs.queued = _full(BuildKind.DOOR)
    computation = RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs))

    await computation.process_queue(context=ActingContext.worker("test-worker"))

    assert runs.completed == (BuildKind.DOOR,)
    assert runs.failed is None


def _expanding_door(*, steps: tuple[int, ...] = (1,), maximum_kind: str = "unbounded") -> CandidateBuild:
    return replace(
        _door(2, volume=1, opening=0, restrictions=()),
        expansion=DoorExpansion.model_validate(
            {
                "parameters": [
                    {
                        "name": "n",
                        "minimum": 1,
                        "maximum_kind": maximum_kind,
                        "initial_steps": [],
                        "repeating_steps": steps,
                    }
                ],
                "branches": [
                    ExpansionBranch(
                        opening_width="n",
                        opening_height="2",
                        opening_depth="1",
                        volume="5n",
                        timings=(ExpansionTiming(method="opening", expression="n+1", unit="game_tick"),),
                    )
                ],
            }
        ),
    )


async def test_ordinary_records_evaluate_families_and_keep_the_source_evidence() -> None:
    fixed = _door(1, volume=100, opening=10, restrictions=())
    family = _expanding_door()
    candidates = FakeCandidates((fixed, family))
    runs = FakeRuns()
    service = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))
    await service.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))
    assert len(runs.batches[0].records) == 5
    for record in runs.batches[0].records:
        assert record.door_evidence is not None
        assert record.door_evidence.sources[0].expansion == family.expansion
        configuration = next(c for c in record.door_evidence.evaluation.configurations if c.build_id == 2)
        assert configuration.parameters == (("n", 2),)
        assert configuration.volume == 10
        assert configuration.timing_variants == ((3, None, None, None, None, None),)
        assert configuration.completion_at is None
        assert record.resolution.holder_ids == ((1,) if record.criterion is RecordCriterion.FIRST else (2,))
        assert not record.history_complete
        assert record.history == ()


async def test_family_sample_never_seeds_categories_but_explicit_large_request_does() -> None:
    candidates = FakeCandidates((_expanding_door(),))
    runs = FakeRuns()
    computation = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))
    await computation.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))
    assert runs.batches[0].records == ()
    service = RecordService(
        computation,
        permissions=create_autospec(PermissionService, instance=True),
        uow_factory=lambda: RecordScope(candidates, runs),
    )
    await service.lookup_or_materialize(
        RecordLookupRequest(kind=BuildKind.DOOR, base_key="door|9999x2|t[20]|Door", restriction_ids=frozenset()),
        context=ActingContext.direct(AnonymousActor()),
    )
    await computation.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))
    records = runs.batches[-1].records
    # A family has no completion date, so nothing competes for "first" and it has no result.
    assert {r.criterion for r in records} == set(RecordCriterion) - {RecordCriterion.FIRST}
    assert {r.category.identity.base_key for r in records} == {"door|9999x2|t[20]|Door"}
    smallest = next(r for r in records if r.criterion is RecordCriterion.SMALLEST)
    assert smallest.resolution.holder_ids == (2,)
    assert smallest.door_evidence is not None
    assert smallest.door_evidence.evaluation.configurations[0].volume == 49995


@pytest.mark.parametrize("unknown", [False, True])
async def test_ordinary_records_exclude_unproven_expansion_sizes(unknown: bool) -> None:
    candidates = FakeCandidates(
        (
            _door(1, volume=100, opening=10, restrictions=()),
            _expanding_door(steps=(1,) if unknown else (2,), maximum_kind="unknown" if unknown else "unbounded"),
        )
    )
    runs = FakeRuns()
    computation = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))
    await computation.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))
    smallest = next(r for r in runs.batches[0].records if r.criterion is RecordCriterion.SMALLEST)
    assert smallest.resolution
    assert smallest.resolution.holder_ids == (1,)
    assert smallest.resolution.gaps == ()
    if unknown:
        assert smallest.door_evidence is not None
        assert smallest.door_evidence.evaluation.configurations[1].membership is PointStatus.UNRESOLVED
    else:
        assert smallest.door_evidence is None


async def test_family_population_respects_version_and_category_identity() -> None:
    fixed = _door(1, volume=100, opening=10, restrictions=())
    family = replace(_expanding_door(), version_ids=frozenset({4}))
    assert family.door is not None
    wrong_orientation = replace(
        family, candidate=replace(family.candidate, build_id=3), door=replace(family.door, orientation="Skydoor")
    )
    wrong_type = replace(family, candidate=replace(family.candidate, build_id=4), types=())
    candidates = FakeCandidates((fixed, family, wrong_orientation, wrong_type))
    runs = FakeRuns()
    runs.current_version_id = 3
    computation = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))
    await computation.rebuild(kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor()))
    records = runs.batches[0].records
    all_time = next(r for r in records if r.criterion is RecordCriterion.SMALLEST and r.version_id is None)
    assert all_time.resolution.holder_ids == (2,)
    assert all_time.door_evidence is not None
    assert {c.build_id for c in all_time.door_evidence.evaluation.configurations} == {1, 2}
    current = next(r for r in records if r.criterion is RecordCriterion.SMALLEST and r.version_id == 3)
    assert current.resolution.holder_ids == (1,)
    assert current.door_evidence is None


async def test_family_absent_from_a_size_leaves_that_category_alone() -> None:
    flush = CandidateTag(id=1, kind=RecordTagRole.RESTRICTION, name="Flush", restriction_type="wiring-placement")
    # Widths start at six, so this family cannot be a 2x2 door.
    family = replace(
        _door(2, volume=1, opening=0, restrictions=(flush,)),
        expansion=DoorExpansion.model_validate(
            {
                "parameters": [
                    {
                        "name": "n",
                        "minimum": 6,
                        "maximum_kind": "unbounded",
                        "initial_steps": [],
                        "repeating_steps": [1],
                    }
                ],
                "branches": [{"opening_width": "n", "opening_height": "2", "volume": "5n"}],
            }
        ),
    )
    candidates = FakeCandidates((_door(1, volume=100, opening=10, restrictions=()), family))
    runs = FakeRuns()
    await RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)).rebuild(
        kinds=(BuildKind.DOOR,), context=ActingContext.direct(AnonymousActor())
    )
    records = runs.batches[0].records
    # No empty "Flush 2x2" category appears, and the fixed-only category keeps its history.
    assert {record.category.identity.restriction_ids for record in records} == {()}
    smallest = next(record for record in records if record.criterion is RecordCriterion.SMALLEST)
    assert smallest.door_evidence is None
    assert smallest.history_complete
    assert [period.build_ids for period in smallest.history] == [(1,)]


async def test_failed_kind_does_not_roll_back_other_kind_publication() -> None:
    class FailingRuns(FakeRuns):
        @override
        async def publish(self, batch: ComputationBatch) -> RebuildSummary:
            if batch.kind is BuildKind.DOOR:
                raise RuntimeError("door computation failed")
            return await super().publish(batch)

    runs = FailingRuns()
    runs.queued = _full(BuildKind.DOOR, BuildKind.EXTENDER)
    service = RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs))

    with pytest.raises(RuntimeError, match="door computation failed"):
        await service.process_queue(context=ActingContext.worker("test-worker"))

    assert runs.completed == (BuildKind.EXTENDER,)
    assert runs.failed == (BuildKind.DOOR, "door computation failed")
    assert [batch.kind for batch in runs.batches] == [BuildKind.EXTENDER]


async def test_record_inspection_rejects_callers_outside_the_credential_ceiling_before_loading_records() -> None:
    uow_factory = Mock()
    permissions = PermissionService(uow_factory=uow_factory)
    service = RecordService(
        RecordComputationService(uow_factory=uow_factory), permissions=permissions, uow_factory=uow_factory
    )
    context = ActingContext.direct(UserActor(account_id=1, is_bot_owner=True, credential_scopes=frozenset()))
    with pytest.raises(PermissionDeniedError):
        await service.gaps(context=context)
    with pytest.raises(PermissionDeniedError):
        await service.title_gaps(context=context)
    with pytest.raises(PermissionDeniedError):
        await service.request_definition_recompute(1, context=context)
    with pytest.raises(PermissionDeniedError):
        await service.inspect(uuid.UUID(int=1), context=context)
    with pytest.raises(PermissionDeniedError):
        await service.lookup_or_materialize(
            RecordLookupRequest(kind=BuildKind.DOOR, base_key="door", restriction_ids=frozenset()), context=context
        )
    uow_factory.assert_not_called()
