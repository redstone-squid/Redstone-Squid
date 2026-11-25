import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import override
from unittest.mock import create_autospec

import anyio
import pytest

from squid.core.builds.application.queries import PublicBuildDimensions, PublicBuildSummary
from squid.core.builds.domain import BuildCategory
from squid.core.builds.domain.expansion import DoorExpansion, ExpansionBranch, ExpansionParameter, ExpansionTiming
from squid.core.builds.domain.expansion_evaluation import PointStatus
from squid.core.errors import DataIntegrityError, ServiceUnavailableError, ValidationError
from squid.core.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery, analyze_door_widths
from squid.core.records.application.live import DoorRecordLookup
from squid.core.records.application.models import (
    CandidateBuild,
    CandidateTag,
    CategoryIdentity,
    ComputationBatch,
    PublishedRecordResult,
    RecordGap,
    RecordLookupRequest,
    RecordNameDiagnostic,
)
from squid.core.records.application.ports import DoorRangeAnalyzer, RecomputeLease
from squid.core.records.application.services import PublicRecordQueryService, RecordComputationService, RecordService
from squid.core.records.application.snapshots import RecordSnapshot
from squid.core.records.domain import (
    BuildKind,
    Candidate,
    DoorCategory,
    FormattedTitle,
    RecordCriterion,
    ResolutionStatus,
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

    async def list_for_door_lookup(
        self, opening: tuple[int, int, int], orientation: str, version_id: int | None
    ) -> Sequence[CandidateBuild]:
        width, height, depth = opening
        size = f"{width}x{height}" + (f"x{depth}" if depth != 1 else "")
        return tuple(
            replace(candidate, door=replace(candidate.door, size=size))
            for candidate in self.candidates
            if candidate.door is not None
            and candidate.door.orientation == orientation
            and (candidate.expansion is not None or candidate.door.size == size)
            and (version_id is None or version_id in candidate.version_ids)
        )

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
    """Capture computed batches and supply the configured record queries.

    RecordScope copies this state and publishes it on commit. Failed publication
    leaves batches unchanged. Queue methods record
    calls without enforcing claim-token ownership.
    """

    def __init__(self) -> None:
        self.batches: list[ComputationBatch] = []
        self.requested: dict[BuildKind, list[CategoryIdentity]] = {}
        self.requested_titles: dict[str, dict[RecordCriterion, FormattedTitle]] = {}
        self.definition_identities: dict[int, CategoryIdentity] = {}
        self.gap_rows: tuple[RecordGap, ...] = ()
        self.title_gap_rows: tuple[RecordNameDiagnostic, ...] = ()
        self.queued: tuple[BuildKind, ...] = ()
        self.completed: tuple[BuildKind, ...] = ()
        self.failed: tuple[tuple[BuildKind, ...], str] | None = None
        self.current_version_id: int | None = None
        self.published_versions: tuple[int, ...] = ()
        self.refresh_active_versions = False
        self.published_records: dict[int, PublishedRecordResult] = {}

    async def active_rules_revision_id(self) -> int:
        return 7

    async def active_current_version_id(self) -> int | None:
        return self.current_version_id

    async def active_publication_versions(self, kind: BuildKind) -> Sequence[int]:
        return self.published_versions

    async def set_current_version(self, version_id: int | None) -> None:
        self.current_version_id = version_id

    async def input_generation(self, kind: BuildKind) -> int:
        return 1

    async def activate_snapshot(self, snapshot: RecordSnapshot, batch: ComputationBatch) -> int:
        return await self.activate(batch)

    async def activate(self, batch: ComputationBatch) -> int:
        self.batches.append(batch)
        return len(self.batches)

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        return self.gap_rows

    async def list_title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordNameDiagnostic]:
        return self.title_gap_rows

    async def get_published_record(self, result_id: int) -> PublishedRecordResult | None:
        return self.published_records.get(result_id)

    async def get_latest_record(self, record_id: uuid.UUID) -> PublishedRecordResult | None:
        return next((result for result in self.published_records.values() if result.record_id == record_id), None)

    async def list_published_records(
        self,
        *,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
        descending: bool = True,
        limit: int,
        resolved_only: bool = False,
        version_scope: VersionScope | None = None,
    ) -> Sequence[PublishedRecordResult]:
        ordered = sorted(
            (
                key
                for key, record in self.published_records.items()
                if (not resolved_only or record.status is ResolutionStatus.RESOLVED)
                and (version_scope is None or record.version_scope is version_scope)
            ),
            reverse=descending,
        )
        if before_id is not None:
            kept = [rid for rid in ordered if (rid > before_id if descending else rid < before_id)]
            return tuple(self.published_records[rid] for rid in kept[-limit:])
        if after_id is not None:
            ordered = [rid for rid in ordered if (rid < after_id if descending else rid > after_id)]
        return tuple(self.published_records[rid] for rid in ordered[offset : offset + limit])

    async def count_published_records(
        self, *, resolved_only: bool = False, version_scope: VersionScope | None = None
    ) -> int:
        return sum(
            (not resolved_only or record.status is ResolutionStatus.RESOLVED)
            and (version_scope is None or record.version_scope is version_scope)
            for record in self.published_records.values()
        )

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]:
        return tuple(self.requested.get(kind, ()))

    async def get_definition_identity(self, definition_id: int) -> CategoryIdentity | None:
        return self.definition_identities.get(definition_id)

    async def save_requested_category(
        self,
        rules_revision_id: int,
        category: CategoryIdentity,
        titles: Mapping[RecordCriterion, FormattedTitle],
    ) -> None:
        assert rules_revision_id == 7
        self.requested.setdefault(category.kind, []).append(category)
        self.requested_titles[category.key] = dict(titles)

    async def claim_recompute_kinds(self, *, limit: int) -> RecomputeLease:
        kinds = self.queued[:limit]
        return RecomputeLease(
            kinds=kinds,
            claim_tokens=tuple(uuid.uuid4() for _ in kinds),
            refresh_active_versions=self.refresh_active_versions,
        )

    async def complete_recompute(self, lease: RecomputeLease) -> None:
        self.completed += lease.kinds
        self.queued = tuple(kind for kind in self.queued if kind not in lease.kinds)

    async def fail_recompute(self, lease: RecomputeLease, error: str) -> None:
        self.failed = (lease.kinds, error)
        self.queued = tuple(kind for kind in self.queued if kind not in lease.kinds)


class FakePublicBuilds:
    def __init__(self, summaries: Sequence[PublicBuildSummary]) -> None:
        self.summaries = {summary.id: summary for summary in summaries}
        self.requests: list[tuple[int, ...]] = []

    async def get_public_summaries(self, build_ids: Sequence[int]) -> Sequence[PublicBuildSummary]:
        self.requests.append(tuple(build_ids))
        return tuple(self.summaries[build_id] for build_id in build_ids if build_id in self.summaries)


def _public_build(build_id: int) -> PublicBuildSummary:
    return PublicBuildSummary(
        id=build_id,
        revision=1,
        title=f"Build {build_id}",
        display_name=None,
        status="approved",
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
        status=ResolutionStatus.RESOLVED,
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

    summary = await service.rebuild(kinds=(BuildKind.DOOR,))

    assert summary.run_ids == (1,)
    assert summary.definitions == 20
    assert summary.resolved == 20
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

    await service.rebuild(kinds=(BuildKind.DOOR,))

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
async def test_rebuild_creates_empty_extender_run() -> None:
    runs = FakeRuns()
    service = RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs))

    summary = await service.rebuild(kinds=(BuildKind.EXTENDER,))

    assert summary.run_ids == (1,)
    assert summary.definitions == 0
    assert runs.batches[0].kind is BuildKind.EXTENDER
    assert runs.batches[0].records == ()


@pytest.mark.asyncio
async def test_current_version_run_filters_incompatible_candidates() -> None:
    candidates = FakeCandidates(
        (
            _door(1, volume=10, opening=5, restrictions=(), version_ids=frozenset({2})),
            _door(2, volume=12, opening=4, restrictions=(), version_ids=frozenset({3})),
        )
    )
    runs = FakeRuns()
    service = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))

    await service.rebuild(current_version_id=3, kinds=(BuildKind.DOOR,))

    assert len(runs.batches) == 2
    current = runs.batches[1]
    assert current.version_id == 3
    assert {
        record.resolution.holder_ids
        for record in current.records
        if record.resolution.status is ResolutionStatus.RESOLVED
    } == {(2,)}
    all_time = runs.batches[0]
    assert {
        record.resolution.holder_ids
        for record in all_time.records
        if record.resolution.status is ResolutionStatus.RESOLVED
    } == {(1,), (2,)}


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

    await service.rebuild(kinds=(BuildKind.DOOR,))

    assert max(len(record.category.identity.restriction_ids) for record in runs.batches[0].records) == 8
    assert all(len(record.category.identity.restriction_ids) <= 8 for record in runs.batches[0].records)


@pytest.mark.asyncio
async def test_lookup_materializes_large_exact_category_and_rebuilds_full_kind() -> None:
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
    service = RecordService(computation, uow_factory=lambda: RecordScope(candidates, runs))

    summary = await service.lookup_or_materialize(
        RecordLookupRequest(
            kind=BuildKind.DOOR,
            base_key="door|2x2|t[20]|Door",
            restriction_ids=frozenset(range(1, 10)),
        )
    )

    assert summary.run_ids == (1,)
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
async def test_materialize_definition_round_trips_the_stored_identity() -> None:
    flush = CandidateTag(id=1, kind=RecordTagRole.RESTRICTION, name="Flush", restriction_type="wiring-placement")
    candidates = FakeCandidates((_door(1, volume=10, opening=5, restrictions=(flush,)),))
    runs = FakeRuns()
    identity = CategoryIdentity(BuildKind.DOOR, "door|2x2|t[20]|Door", (1,))
    runs.definition_identities[42] = identity
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)),
        uow_factory=lambda: RecordScope(candidates, runs),
    )

    summary = await service.materialize_definition(42, kind=BuildKind.DOOR)

    assert summary.run_ids == (1,)
    assert runs.requested[BuildKind.DOOR] == [identity]


@pytest.mark.asyncio
async def test_materialize_definition_rejects_unknown_id() -> None:
    candidates = FakeCandidates(())
    runs = FakeRuns()
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)),
        uow_factory=lambda: RecordScope(candidates, runs),
    )

    with pytest.raises(RecordDefinitionNotFoundError):
        await service.materialize_definition(999)


@pytest.mark.asyncio
async def test_materialize_definition_rejects_kind_mismatch() -> None:
    candidates = FakeCandidates(())
    runs = FakeRuns()
    runs.definition_identities[42] = CategoryIdentity(BuildKind.DOOR, "door|2x2|t[20]|Door", ())
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)),
        uow_factory=lambda: RecordScope(candidates, runs),
    )

    with pytest.raises(ValidationError):
        await service.materialize_definition(42, kind=BuildKind.EXTENDER)


@pytest.mark.asyncio
async def test_lookup_rejects_category_without_confirmed_candidate() -> None:
    candidates = FakeCandidates((_door(1, volume=10, opening=5, restrictions=()),))
    runs = FakeRuns()
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)),
        uow_factory=lambda: RecordScope(candidates, runs),
    )

    with pytest.raises(NoMatchingRecordCategoryError, match="No approved build"):
        await service.lookup_or_materialize(
            RecordLookupRequest(
                kind=BuildKind.DOOR,
                base_key="door|3x3|t[20]|Door",
                restriction_ids=frozenset(),
            )
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
        fields=("closing",),
        build_kind=BuildKind.DOOR,
    )
    runs.gap_rows = (gap,)
    candidates = FakeCandidates(())
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)),
        uow_factory=lambda: RecordScope(candidates, runs),
    )

    assert await service.gaps(kind=BuildKind.DOOR) == (gap,)


@pytest.mark.asyncio
async def test_public_record_query_preserves_holder_order() -> None:
    runs = FakeRuns()
    runs.published_records[7] = _published_record(41, 42)
    builds = FakePublicBuilds((_public_build(42), _public_build(41)))
    service = PublicRecordQueryService(builds, uow_factory=lambda: RecordQueryScope(runs))

    detail = await service.get(runs.published_records[7].record_id)

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
        await service.get(runs.published_records[7].record_id)

    assert exc_info.value.context == {
        "record_id": str(runs.published_records[7].record_id),
        "unavailable_holder_build_ids": [42],
    }


@pytest.mark.asyncio
async def test_public_record_query_accepts_empty_nonresolved_result() -> None:
    runs = FakeRuns()
    runs.published_records[7] = replace(
        _published_record(41),
        status=ResolutionStatus.NO_CANDIDATE,
        holder_build_ids=(),
    )
    builds = FakePublicBuilds(())
    service = PublicRecordQueryService(builds, uow_factory=lambda: RecordQueryScope(runs))

    detail = await service.get(runs.published_records[7].record_id)

    assert detail is not None
    assert detail.holder_builds == ()
    assert builds.requests == [()]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        _published_record(),
        replace(_published_record(41), status=ResolutionStatus.UNRESOLVED),
    ],
)
async def test_public_record_query_rejects_status_holder_corruption(result: PublishedRecordResult) -> None:
    runs = FakeRuns()
    runs.published_records[7] = result
    service = PublicRecordQueryService(
        FakePublicBuilds((_public_build(41),)), uow_factory=lambda: RecordQueryScope(runs)
    )

    with pytest.raises(DataIntegrityError, match="status contradicts") as exc_info:
        await service.get(runs.published_records[7].record_id)

    assert exc_info.value.context == {
        "record_id": str(runs.published_records[7].record_id),
        "status": result.status.value,
        "holder_build_ids": list(result.holder_build_ids),
    }


@pytest.mark.asyncio
async def test_public_record_query_returns_none_for_unknown_record() -> None:
    service = PublicRecordQueryService(FakePublicBuilds(()), uow_factory=lambda: RecordQueryScope(FakeRuns()))

    assert await service.get(uuid.UUID(int=999)) is None


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
        uow_factory=lambda: RecordScope(candidates, runs),
    )

    assert await service.title_gaps(kind=BuildKind.DOOR) == (gap,)


@pytest.mark.asyncio
async def test_process_queue_rebuilds_and_acknowledges_claimed_kinds() -> None:
    runs = FakeRuns()
    runs.queued = (BuildKind.DOOR, BuildKind.EXTENDER)
    computation = RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs))

    result = await computation.process_queue()

    assert result.kinds == (BuildKind.DOOR, BuildKind.EXTENDER)
    assert result.rebuild is not None
    assert result.rebuild.run_ids == (1, 2)
    assert runs.completed == (BuildKind.DOOR, BuildKind.EXTENDER)


@pytest.mark.asyncio
async def test_process_queue_preserves_the_pinned_current_version() -> None:
    runs = FakeRuns()
    runs.queued = (BuildKind.DOOR,)
    runs.current_version_id = 3
    computation = RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs))

    result = await computation.process_queue()

    assert result.rebuild is not None
    assert result.rebuild.run_ids == (1, 2)
    assert [batch.version_id for batch in runs.batches] == [None, 3]


async def test_holder_measurement_backfill_refreshes_already_published_versions() -> None:
    runs = FakeRuns()
    runs.queued = (BuildKind.DOOR,)
    runs.current_version_id = 3
    runs.published_versions = (2, 3)
    runs.refresh_active_versions = True
    computation = RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs))

    await computation.process_queue()

    assert [batch.version_id for batch in runs.batches] == [None, 3, 2]
    assert runs.completed == (BuildKind.DOOR,)


async def test_rebuild_rolls_back_all_scopes_when_later_activation_fails() -> None:
    class FailingRuns(FakeRuns):
        @override
        async def activate(self, batch: ComputationBatch) -> int:
            if self.batches:
                raise RuntimeError("second activation failed")
            return await super().activate(batch)

    runs = FailingRuns()
    candidates = FakeCandidates((_door(1, volume=10, opening=5, restrictions=()),))
    computation = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))

    with pytest.raises(RuntimeError, match="second activation"):
        await computation.rebuild(kinds=(BuildKind.DOOR,), current_version_id=3)

    assert runs.batches == []


async def test_live_door_lookup_applies_tags_version_and_thresholds_without_writes() -> None:

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
    wrong_version = replace(family, candidate=replace(family.candidate, build_id=3), version_ids=frozenset({4}))
    wrong_types = replace(family, candidate=replace(family.candidate, build_id=4), types=())
    wrong_restrictions = replace(family, candidate=replace(family.candidate, build_id=5), restrictions=())
    candidates = FakeCandidates((source, family, wrong_version, wrong_types, wrong_restrictions))
    runs = FakeRuns()
    computation = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))
    service = RecordService(computation, uow_factory=lambda: RecordScope(candidates, runs))
    result = await service.lookup_door(
        DoorRecordLookup(
            opening=(2, 2, 1),
            type_ids=frozenset({20}),
            restriction_ids=frozenset({1, 7}),
            restriction_values=((7, "at_most", "4"),),
            version_id=3,
        )
    )
    assert result.resolution.holder_ids == (2,)
    assert {configuration.build_id for configuration in result.configurations} == {1, 2}
    assert runs.batches == []
    assert runs.requested == {}


@pytest.mark.parametrize("threshold", ["not a number", "NaN", "Infinity", "1e999999", "1e-999999", "2"])
async def test_live_lookup_rejects_incompatible_threshold_without_crashing(threshold: str) -> None:

    width = CandidateTag(
        id=7,
        kind=RecordTagRole.RESTRICTION,
        name="Width",
        value_type="numeric",
        assigned_value=Decimal(3),
        record_operator="at_most",
    )
    candidates = FakeCandidates((_door(1, volume=100, opening=10, restrictions=(width,)),))
    runs = FakeRuns()
    computation = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))
    service = RecordService(computation, uow_factory=lambda: RecordScope(candidates, runs))
    result = await service.lookup_door(
        DoorRecordLookup(
            opening=(2, 2, 1),
            type_ids=frozenset({20}),
            restriction_ids=frozenset({7}),
            restriction_values=((7, "at_most", threshold),),
        )
    )
    assert result.resolution.status is ResolutionStatus.NO_CANDIDATE


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
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)),
        uow_factory=lambda: RecordScope(candidates, runs),
        range_analyzer=SubprocessRangeAnalyzer(),
    )
    result = await service.lookup_door_ranges(DoorRangeQuery(height=2, type_ids=frozenset({20}), version_id=3))
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
    service = RecordService(
        RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)),
        uow_factory=lambda: RecordScope(candidates, runs),
        range_analyzer=analyzer,
        range_concurrency=1,
    )
    request = DoorRangeQuery(height=3)
    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(service.lookup_door_ranges, request)
            await started.wait()
            with pytest.raises(ServiceUnavailableError):
                await service.lookup_door_ranges(request)
            candidates.list_for_door_widths.assert_awaited_once()
            group.cancel_scope.cancel()
    analyzer.analyze.side_effect = None
    analyzer.analyze.return_value = analyze_door_widths((), height=3)
    result = await service.lookup_door_ranges(request)
    assert result.query == request
    assert result.analysis.status is RegionAnalysisStatus.EXACT
    assert candidates.list_for_door_widths.await_count == 2
    assert runs.batches == []


async def test_concrete_queue_completes_without_range_work() -> None:
    runs = FakeRuns()
    runs.queued = (BuildKind.DOOR,)
    computation = RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs))

    await computation.process_queue()

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
    await service.rebuild(kinds=(BuildKind.DOOR,))
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
    await computation.rebuild(kinds=(BuildKind.DOOR,))
    assert runs.batches[0].records == ()
    service = RecordService(computation, uow_factory=lambda: RecordScope(candidates, runs))
    await service.lookup_or_materialize(
        RecordLookupRequest(kind=BuildKind.DOOR, base_key="door|9999x2|t[20]|Door", restriction_ids=frozenset())
    )
    records = runs.batches[-1].records
    assert len(records) == 5
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
    await computation.rebuild(kinds=(BuildKind.DOOR,))
    smallest = next(r for r in runs.batches[0].records if r.criterion is RecordCriterion.SMALLEST)
    assert smallest.resolution.status is ResolutionStatus.RESOLVED
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
    computation = RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs))
    await computation.rebuild(kinds=(BuildKind.DOOR,), current_version_id=3)
    all_time = next(r for r in runs.batches[0].records if r.criterion is RecordCriterion.SMALLEST)
    assert all_time.resolution.holder_ids == (2,)
    assert all_time.door_evidence is not None
    assert {c.build_id for c in all_time.door_evidence.evaluation.configurations} == {1, 2}
    current = next(r for r in runs.batches[1].records if r.criterion is RecordCriterion.SMALLEST)
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
    await RecordComputationService(uow_factory=lambda: RecordScope(candidates, runs)).rebuild(kinds=(BuildKind.DOOR,))
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
        async def activate(self, batch: ComputationBatch) -> int:
            if batch.kind is BuildKind.DOOR:
                raise RuntimeError("door computation failed")
            return await super().activate(batch)

    runs = FailingRuns()
    runs.queued = (BuildKind.DOOR, BuildKind.EXTENDER)
    service = RecordComputationService(uow_factory=lambda: RecordScope(FakeCandidates(()), runs))

    with pytest.raises(RuntimeError, match="door computation failed"):
        await service.process_queue()

    assert runs.completed == (BuildKind.EXTENDER,)
    assert runs.failed == ((BuildKind.DOOR,), "door computation failed")
    assert [batch.kind for batch in runs.batches] == [BuildKind.EXTENDER]
