import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from squid.builds.application.queries import PublicBuildSummary
from squid.core.errors import DataIntegrityError, ValidationError
from squid.records.application.models import (
    CandidateFacet,
    CategoryIdentity,
    ComputationBatch,
    PublishedRecord,
    RecordGap,
    RecordLookupRequest,
    RecordSourceCandidate,
    TitleDiagnosticGap,
)
from squid.records.application.ports import RecomputeLease
from squid.records.application.services import PublicRecordQueryService, RecordComputationService, RecordService
from squid.records.domain import (
    BuildKind,
    CategoryText,
    DoorCategory,
    RecordCandidate,
    RecordClass,
    ResolutionStatus,
    TimingVariant,
    VersionScope,
)
from squid.records.errors import NoMatchingRecordCategoryError, RecordDefinitionNotFoundError


class FakeCandidates:
    def __init__(self, candidates: Sequence[RecordSourceCandidate]) -> None:
        self.candidates = tuple(candidates)

    async def list_confirmed(self, kind: BuildKind) -> Sequence[RecordSourceCandidate]:
        return tuple(candidate for candidate in self.candidates if candidate.kind is kind)


class FakeRuns:
    def __init__(self) -> None:
        self.batches: list[ComputationBatch] = []
        self.requested: dict[BuildKind, list[CategoryIdentity]] = {}
        self.requested_titles: dict[str, dict[RecordClass, CategoryText]] = {}
        self.definition_identities: dict[int, CategoryIdentity] = {}
        self.gap_rows: tuple[RecordGap, ...] = ()
        self.title_gap_rows: tuple[TitleDiagnosticGap, ...] = ()
        self.queued: tuple[BuildKind, ...] = ()
        self.completed: tuple[BuildKind, ...] = ()
        self.failed: tuple[tuple[BuildKind, ...], str] | None = None
        self.current_version_id: int | None = None
        self.published_records: dict[int, PublishedRecord] = {}

    async def active_ruleset_id(self) -> int:
        return 7

    async def active_current_version_id(self) -> int | None:
        return self.current_version_id

    async def activate(self, batch: ComputationBatch) -> int:
        self.batches.append(batch)
        return len(self.batches)

    async def list_gaps(self, *, kind: BuildKind | None = None) -> Sequence[RecordGap]:
        return self.gap_rows

    async def list_title_gaps(self, *, kind: BuildKind | None = None) -> Sequence[TitleDiagnosticGap]:
        return self.title_gap_rows

    async def get_published_record(self, result_id: int) -> PublishedRecord | None:
        return self.published_records.get(result_id)

    async def list_published_records(
        self,
        *,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
        descending: bool = True,
        limit: int,
    ) -> Sequence[PublishedRecord]:
        ordered = sorted(self.published_records, reverse=descending)
        if before_id is not None:
            kept = [rid for rid in ordered if (rid > before_id if descending else rid < before_id)]
            return tuple(self.published_records[rid] for rid in kept[-limit:])
        if after_id is not None:
            ordered = [rid for rid in ordered if (rid < after_id if descending else rid > after_id)]
        return tuple(self.published_records[rid] for rid in ordered[offset : offset + limit])

    async def count_published_records(self) -> int:
        return len(self.published_records)

    async def list_requested_categories(self, kind: BuildKind) -> Sequence[CategoryIdentity]:
        return tuple(self.requested.get(kind, ()))

    async def get_definition_identity(self, definition_id: int) -> CategoryIdentity | None:
        return self.definition_identities.get(definition_id)

    async def save_requested_category(
        self,
        ruleset_id: int,
        category: CategoryIdentity,
        titles: Mapping[RecordClass, CategoryText],
    ) -> None:
        assert ruleset_id == 7
        self.requested.setdefault(category.kind, []).append(category)
        self.requested_titles[category.key] = dict(titles)

    async def enqueue(self, kind: BuildKind, *, build_id: int | None, reason: str) -> None:
        self.queued = (*self.queued, kind)

    async def claim_recompute_kinds(self, *, limit: int) -> RecomputeLease:
        kinds = self.queued[:limit]
        return RecomputeLease(kinds=kinds, claim_tokens=tuple(uuid.uuid4() for _ in kinds))

    async def complete_recompute(self, lease: RecomputeLease) -> None:
        self.completed = lease.kinds

    async def fail_recompute(self, lease: RecomputeLease, error: str) -> None:
        self.failed = (lease.kinds, error)


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
        status="confirmed",
        category="Door",
        dimensions=(2, 3, 4),
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


def _published_record(*holder_build_ids: int) -> PublishedRecord:
    return PublishedRecord(
        id=7,
        definition_id=3,
        competition_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        title="Fastest 2x3 door",
        subtitle=None,
        record_class=RecordClass.FASTEST,
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
async def test_rebuild_computes_eager_subsets_and_all_door_record_classes() -> None:
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
    assert summary.definitions == 20
    assert summary.resolved == 20
    batch = runs.batches[0]
    shared = [record for record in batch.records if record.competition.identity.restriction_ids == (1,)]
    first = next(record for record in shared if record.record_class is RecordClass.FIRST)
    smallest = next(record for record in shared if record.record_class is RecordClass.SMALLEST)
    fastest = next(record for record in shared if record.record_class is RecordClass.FASTEST)
    fastest_smallest = next(record for record in shared if record.record_class is RecordClass.FASTEST_SMALLEST)
    smallest_fastest = next(record for record in shared if record.record_class is RecordClass.SMALLEST_FASTEST)
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
    def m_wide(value: int) -> CandidateFacet:
        return CandidateFacet(
            id=7,
            kind="restriction",
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
        FakeCandidates(
            (
                _door(1, volume=10, opening=5, restrictions=(m_wide(3),)),
                _door(2, volume=8, opening=4, restrictions=(m_wide(4),)),
            )
        ),
        runs,
    )

    await service.rebuild(kinds=(BuildKind.DOOR,))

    threshold_three = [
        record
        for record in runs.batches[0].records
        if record.competition.identity.restriction_values == ((7, "at_most", "3"),)
    ]
    threshold_four = [
        record
        for record in runs.batches[0].records
        if record.competition.identity.restriction_values == ((7, "at_most", "4"),)
    ]
    assert {record.resolution.holder_ids for record in threshold_three} == {(1,)}
    assert {record.record_class: record.resolution.holder_ids for record in threshold_four} == {
        RecordClass.FIRST: (1,),
        RecordClass.FASTEST: (2,),
        RecordClass.SMALLEST: (2,),
        RecordClass.FASTEST_SMALLEST: (2,),
        RecordClass.SMALLEST_FASTEST: (2,),
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
    all_time = runs.batches[0]
    assert {
        record.broken_holder_ids for record in all_time.records if record.resolution.status is ResolutionStatus.RESOLVED
    } == {(1,), ()}


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
    identity = runs.requested[BuildKind.DOOR][0]
    assert identity.restriction_ids == tuple(range(1, 10))
    titles = runs.requested_titles[identity.key]
    assert set(titles) == set(RecordClass)
    assert all("2x2" in text.title for text in titles.values())
    assert all(identity.base_key not in text.title for text in titles.values())
    exact = [record for record in runs.batches[0].records if len(record.competition.identity.restriction_ids) == 9]
    assert {record.record_class for record in exact} == set(RecordClass)
    assert all(record.competition.source == "public_lookup" for record in exact)


@pytest.mark.asyncio
async def test_materialize_definition_round_trips_the_stored_identity() -> None:
    flush = CandidateFacet(id=1, kind="restriction", name="Flush", restriction_type="wiring-placement")
    candidates = FakeCandidates((_door(1, volume=10, opening=5, restrictions=(flush,)),))
    runs = FakeRuns()
    identity = CategoryIdentity(BuildKind.DOOR, "door|2x2|t[20]|Door", (1,))
    runs.definition_identities[42] = identity
    service = RecordService(candidates, runs, RecordComputationService(candidates, runs))

    summary = await service.materialize_definition(42, kind=BuildKind.DOOR)

    assert summary.run_ids == (1,)
    assert runs.requested[BuildKind.DOOR] == [identity]


@pytest.mark.asyncio
async def test_materialize_definition_rejects_unknown_id() -> None:
    candidates = FakeCandidates(())
    runs = FakeRuns()
    service = RecordService(candidates, runs, RecordComputationService(candidates, runs))

    with pytest.raises(RecordDefinitionNotFoundError):
        await service.materialize_definition(999)


@pytest.mark.asyncio
async def test_materialize_definition_rejects_kind_mismatch() -> None:
    candidates = FakeCandidates(())
    runs = FakeRuns()
    runs.definition_identities[42] = CategoryIdentity(BuildKind.DOOR, "door|2x2|t[20]|Door", ())
    service = RecordService(candidates, runs, RecordComputationService(candidates, runs))

    with pytest.raises(ValidationError):
        await service.materialize_definition(42, kind=BuildKind.EXTENDER)


@pytest.mark.asyncio
async def test_lookup_rejects_category_without_confirmed_candidate() -> None:
    candidates = FakeCandidates((_door(1, volume=10, opening=5, restrictions=()),))
    runs = FakeRuns()
    service = RecordService(candidates, runs, RecordComputationService(candidates, runs))

    with pytest.raises(NoMatchingRecordCategoryError, match="No confirmed build"):
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
        record_class=RecordClass.FASTEST,
        build_ids=(4,),
        fields=("closing",),
    )
    runs.gap_rows = (gap,)
    candidates = FakeCandidates(())
    service = RecordService(candidates, runs, RecordComputationService(candidates, runs))

    assert await service.gaps(kind=BuildKind.DOOR) == (gap,)


@pytest.mark.asyncio
async def test_public_record_query_preserves_holder_order() -> None:
    runs = FakeRuns()
    runs.published_records[7] = _published_record(41, 42)
    builds = FakePublicBuilds((_public_build(42), _public_build(41)))
    service = PublicRecordQueryService(runs, builds)

    detail = await service.get(7)

    assert detail is not None
    assert [build.id for build in detail.holder_builds] == [41, 42]
    assert builds.requests == [(41, 42)]


@pytest.mark.asyncio
async def test_public_record_query_rejects_unavailable_holder() -> None:
    runs = FakeRuns()
    runs.published_records[7] = _published_record(41, 42)
    service = PublicRecordQueryService(runs, FakePublicBuilds((_public_build(41),)))

    with pytest.raises(DataIntegrityError) as exc_info:
        await service.get(7)

    assert exc_info.value.context == {"record_id": 7, "unavailable_holder_build_ids": [42]}


@pytest.mark.asyncio
async def test_public_record_query_accepts_empty_nonresolved_standing() -> None:
    runs = FakeRuns()
    runs.published_records[7] = replace(
        _published_record(41),
        status=ResolutionStatus.NO_CANDIDATE,
        holder_build_ids=(),
    )
    builds = FakePublicBuilds(())
    service = PublicRecordQueryService(runs, builds)

    detail = await service.get(7)

    assert detail is not None
    assert detail.holder_builds == ()
    assert builds.requests == [()]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "standing",
    [
        _published_record(),
        replace(_published_record(41), status=ResolutionStatus.UNRESOLVED),
    ],
)
async def test_public_record_query_rejects_status_holder_corruption(standing: PublishedRecord) -> None:
    runs = FakeRuns()
    runs.published_records[7] = standing
    service = PublicRecordQueryService(runs, FakePublicBuilds((_public_build(41),)))

    with pytest.raises(DataIntegrityError, match="status contradicts") as exc_info:
        await service.get(7)

    assert exc_info.value.context == {
        "record_id": 7,
        "status": standing.status.value,
        "holder_build_ids": list(standing.holder_build_ids),
    }


@pytest.mark.asyncio
async def test_public_record_query_returns_none_for_unknown_standing() -> None:
    service = PublicRecordQueryService(FakeRuns(), FakePublicBuilds(()))

    assert await service.get(999) is None


@pytest.mark.asyncio
async def test_title_gaps_delegates_active_definition_query() -> None:
    runs = FakeRuns()
    gap = TitleDiagnosticGap(
        definition_id=1,
        title="Fastest Mystery 2x2 Door",
        diagnostics=({"code": "unknown_token", "message": "Unknown term.", "terms": ["Mystery"]},),
    )
    runs.title_gap_rows = (gap,)
    candidates = FakeCandidates(())
    service = RecordService(candidates, runs, RecordComputationService(candidates, runs))

    assert await service.title_gaps(kind=BuildKind.DOOR) == (gap,)


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


@pytest.mark.asyncio
async def test_process_queue_preserves_the_pinned_current_version() -> None:
    runs = FakeRuns()
    runs.queued = (BuildKind.DOOR,)
    runs.current_version_id = 3
    computation = RecordComputationService(FakeCandidates(()), runs)

    result = await computation.process_queue()

    assert result.rebuild is not None
    assert result.rebuild.run_ids == (1, 2)
    assert [batch.version_id for batch in runs.batches] == [None, 3]
