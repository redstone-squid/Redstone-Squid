from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from squid.records.application.models import (
    CandidateFacet,
    CategoryIdentity,
    ComputationBatch,
    RecordGap,
    RecordLookupRequest,
    RecordSourceCandidate,
)
from squid.records.application.services import RecordComputationService, RecordService
from squid.records.domain import (
    BuildKind,
    DoorCategory,
    RecordCandidate,
    RecordClass,
    ResolutionStatus,
    TimingVariant,
)


class FakeCandidates:
    def __init__(self, candidates: Sequence[RecordSourceCandidate]) -> None:
        self.candidates = tuple(candidates)

    async def list_confirmed(self, kind: BuildKind) -> Sequence[RecordSourceCandidate]:
        return tuple(candidate for candidate in self.candidates if candidate.kind is kind)


class FakeRuns:
    def __init__(self) -> None:
        self.batches: list[ComputationBatch] = []
        self.requested: dict[BuildKind, list[CategoryIdentity]] = {}
        self.gap_rows: tuple[RecordGap, ...] = ()
        self.queued: tuple[BuildKind, ...] = ()
        self.completed: tuple[BuildKind, ...] = ()
        self.failed: tuple[tuple[BuildKind, ...], str] | None = None

    async def active_ruleset_id(self) -> int:
        return 7

    async def activate(self, batch: ComputationBatch) -> int:
        self.batches.append(batch)
        return len(self.batches)

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        return self.gap_rows

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]:
        return tuple(self.requested.get(kind, ()))

    async def save_requested_category(self, ruleset_id: int, category: CategoryIdentity) -> None:
        assert ruleset_id == 7
        self.requested.setdefault(category.kind, []).append(category)

    async def enqueue(self, kind: BuildKind, *, build_id: int | None, reason: str) -> None:
        self.queued = (*self.queued, kind)

    async def claim_recompute_kinds(self, *, limit: int) -> Sequence[BuildKind]:
        return self.queued[:limit]

    async def complete_recompute(self, kinds: Sequence[BuildKind]) -> None:
        self.completed = tuple(kinds)

    async def fail_recompute(self, kinds: Sequence[BuildKind], error: str) -> None:
        self.failed = (tuple(kinds), error)


def _door(
    build_id: int,
    *,
    volume: int,
    opening: int,
    restrictions: tuple[CandidateFacet, ...],
    version_ids: frozenset[int] = frozenset({3}),
) -> RecordSourceCandidate:
    return RecordSourceCandidate(
        kind=BuildKind.DOOR,
        candidate=RecordCandidate(
            build_id=build_id,
            completion_at=datetime(2020, 1, build_id, tzinfo=UTC),
            fixed_volume=volume,
            timing_variants=(TimingVariant((opening, 2, 3, 2, 0, 0)),),
        ),
        version_ids=version_ids,
        restrictions=restrictions,
        types=(CandidateFacet(id=20, kind="type", name="Regular"),),
        door=DoorCategory(
            wiring_restrictions=(),
            animated_restrictions=(),
            size="2x2",
            types=("Regular",),
            orientation="Door",
        ),
    )


@pytest.mark.asyncio
async def test_rebuild_computes_eager_subsets_and_both_metrics() -> None:
    flush = CandidateFacet(id=1, kind="restriction", name="Flush", restriction_type="wiring-placement")
    observerless = CandidateFacet(id=2, kind="restriction", name="Observerless", restriction_type="component")
    candidates = FakeCandidates(
        (
            _door(1, volume=10, opening=5, restrictions=(flush, observerless)),
            _door(2, volume=12, opening=4, restrictions=(flush,)),
        )
    )
    runs = FakeRuns()
    service = RecordComputationService(candidates, runs)

    summary = await service.rebuild(kinds=(BuildKind.DOOR,))

    assert summary.run_ids == (1,)
    assert summary.definitions == 8
    assert summary.resolved == 8
    batch = runs.batches[0]
    shared = [record for record in batch.records if record.competition.identity.restriction_ids == (1,)]
    smallest = next(record for record in shared if record.record_class is RecordClass.SMALLEST)
    fastest = next(record for record in shared if record.record_class is RecordClass.FASTEST)
    assert smallest.resolution.holder_ids == (1,)
    assert fastest.resolution.holder_ids == (2,)
    assert smallest.title.title == "SMALLEST Flush 2x2 Regular Door"
    assert tuple(period.build_ids for period in fastest.history) == ((1,), (2,))
    assert fastest.history[0].held_until == datetime(2020, 1, 2, tzinfo=UTC)


@pytest.mark.asyncio
async def test_rebuild_creates_empty_extender_run() -> None:
    runs = FakeRuns()
    service = RecordComputationService(FakeCandidates(()), runs)

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
    service = RecordComputationService(candidates, runs)

    await service.rebuild(current_version_id=3, kinds=(BuildKind.DOOR,))

    assert len(runs.batches) == 2
    current = runs.batches[1]
    assert current.version_id == 3
    assert {
        record.resolution.holder_ids
        for record in current.records
        if record.resolution.status is ResolutionStatus.RESOLVED
    } == {(2,)}


@pytest.mark.asyncio
async def test_eager_categories_never_exceed_eight_restrictions() -> None:
    restrictions = tuple(
        CandidateFacet(
            id=index,
            kind="restriction",
            name=f"Restriction {index}",
            restriction_type="component",
        )
        for index in range(1, 10)
    )
    runs = FakeRuns()
    service = RecordComputationService(
        FakeCandidates((_door(1, volume=10, opening=5, restrictions=restrictions),)), runs
    )

    await service.rebuild(kinds=(BuildKind.DOOR,))

    assert max(len(record.competition.identity.restriction_ids) for record in runs.batches[0].records) == 8
    assert all(len(record.competition.identity.restriction_ids) <= 8 for record in runs.batches[0].records)


@pytest.mark.asyncio
async def test_lookup_materializes_large_exact_category_and_rebuilds_full_kind() -> None:
    restrictions = tuple(
        CandidateFacet(
            id=index,
            kind="restriction",
            name=f"Restriction {index}",
            restriction_type="component",
        )
        for index in range(1, 10)
    )
    source = _door(1, volume=10, opening=5, restrictions=restrictions)
    candidates = FakeCandidates((source,))
    runs = FakeRuns()
    computation = RecordComputationService(candidates, runs)
    service = RecordService(candidates, runs, computation)

    summary = await service.lookup_or_materialize(
        RecordLookupRequest(
            kind=BuildKind.DOOR,
            base_key="door|2x2|t[20]|Door",
            restriction_ids=frozenset(range(1, 10)),
        )
    )

    assert summary.run_ids == (1,)
    assert runs.requested[BuildKind.DOOR][0].restriction_ids == tuple(range(1, 10))
    exact = [record for record in runs.batches[0].records if len(record.competition.identity.restriction_ids) == 9]
    assert {record.record_class for record in exact} == {RecordClass.SMALLEST, RecordClass.FASTEST}
    assert all(record.competition.source == "public_lookup" for record in exact)


@pytest.mark.asyncio
async def test_lookup_rejects_category_without_confirmed_candidate() -> None:
    candidates = FakeCandidates((_door(1, volume=10, opening=5, restrictions=()),))
    runs = FakeRuns()
    service = RecordService(candidates, runs, RecordComputationService(candidates, runs))

    with pytest.raises(ValueError, match="No confirmed build"):
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
        category_key="door|2x2",
        record_class=RecordClass.FASTEST,
        build_ids=(4,),
        fields=("closing",),
    )
    runs.gap_rows = (gap,)
    candidates = FakeCandidates(())
    service = RecordService(candidates, runs, RecordComputationService(candidates, runs))

    assert await service.gaps(kind=BuildKind.DOOR) == (gap,)


@pytest.mark.asyncio
async def test_process_queue_rebuilds_and_acknowledges_claimed_kinds() -> None:
    runs = FakeRuns()
    runs.queued = (BuildKind.DOOR, BuildKind.EXTENDER)
    computation = RecordComputationService(FakeCandidates(()), runs)

    result = await computation.process_queue()

    assert result.kinds == (BuildKind.DOOR, BuildKind.EXTENDER)
    assert result.rebuild is not None
    assert result.rebuild.run_ids == (1, 2)
    assert runs.completed == (BuildKind.DOOR, BuildKind.EXTENDER)
