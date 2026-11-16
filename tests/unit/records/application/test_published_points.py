"""Published point views retain periodic domains, gaps, and snapshot provenance."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from squid.core.errors import DataIntegrityError, ValidationError
from squid.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery, analyze_door_widths
from squid.records.application.published_points import (
    PublishedDoorPoint,
    PublishedPointStatus,
)
from squid.records.application.range_snapshots import RangeAttempt, RangePublication
from squid.records.domain import ResolutionStatus
from squid.records.domain.regions import RegionAnalysisStatus, SmallestRegionAnalysis
from tests.unit.records.application.test_expansion_regions import branch, family, fixed, parameter

NOW = datetime(2026, 11, 14, tzinfo=UTC)


def publication(result: DoorRangeAnalysis, *, minimum: int = 1, maximum: int | None = None) -> RangePublication:
    attempt = RangeAttempt(
        id=11,
        query_id=7,
        generation=1,
        rules_revision_id=3,
        started_at=NOW,
        completed_at=NOW,
        is_active=True,
        result=result,
        error=None,
        snapshot_hash="snapshot-one",
        captured_at=NOW,
    )
    return RangePublication(7, DoorRangeQuery(height=3, minimum=minimum, maximum=maximum), attempt, attempt)


def test_periodic_holes_fixed_exceptions_and_large_widths() -> None:
    sources = (
        family(branch(volume="ceil(3n/2)"), parameters=(parameter(steps=(2,)),)),
        fixed(6),
    )
    state = publication(analyze_door_widths(sources, height=3))
    for width, expected in ((1, (1,)), (2, ()), (6, (10,)), (9999, (1,))):
        point = PublishedDoorPoint.from_publication(state, width)
        assert point.status is PublishedPointStatus.PUBLISHED
        assert point.opening == (width, 3, 1)
        assert point.region is not None
        assert point.region.resolution.holder_ids == expected
        assert point.region.domain.contains(width)
        assert point.publication is not None
        assert point.publication.snapshot_hash == "snapshot-one"
    hole = PublishedDoorPoint.from_publication(state, 2)
    assert hole.region is not None
    assert hole.region.resolution.status is ResolutionStatus.NO_CANDIDATE


def test_pending_failed_and_out_of_bounds_are_not_no_candidate_results() -> None:
    query = DoorRangeQuery(height=3, minimum=5, maximum=8)
    pending = RangePublication(7, query, None, None)
    assert PublishedDoorPoint.from_publication(pending, 6).status is PublishedPointStatus.NOT_PUBLISHED
    outside = PublishedDoorPoint.from_publication(pending, 9)
    assert outside.status is PublishedPointStatus.OUTSIDE_QUERY
    assert outside.region is None
    state = publication(analyze_door_widths((), height=3, minimum=5, maximum=8), minimum=5, maximum=8)
    assert state.active is not None
    failure = replace(
        state.active,
        id=12,
        generation=2,
        is_active=False,
        result=DoorRangeAnalysis(SmallestRegionAnalysis(RegionAnalysisStatus.TIMED_OUT), ()),
    )
    point = PublishedDoorPoint.from_publication(replace(state, latest=failure), 6)
    assert point.status is PublishedPointStatus.PUBLISHED
    assert point.publication is not None
    assert point.publication.id == 11
    assert point.latest is not None
    assert point.latest.id == 12
    assert point.latest.analysis_status is RegionAnalysisStatus.TIMED_OUT
    assert point.region is not None
    assert point.region.resolution.status is ResolutionStatus.NO_CANDIDATE


def test_unresolved_comparison_remains_published() -> None:
    state = publication(analyze_door_widths((family(build_id=1), family(build_id=2)), height=3))
    point = PublishedDoorPoint.from_publication(state, 6)
    assert point.status is PublishedPointStatus.PUBLISHED
    assert point.region is not None
    assert point.region.resolution.status is ResolutionStatus.UNRESOLVED


@pytest.mark.parametrize("duplicate", [False, True])
def test_corrupt_partition_does_not_silently_award_a_record(*, duplicate: bool) -> None:
    result = analyze_door_widths((family(),), height=3)
    regions = result.analysis.regions * 2 if duplicate else ()
    state = publication(replace(result, analysis=replace(result.analysis, regions=regions)))
    with pytest.raises(DataIntegrityError, match="exactly one"):
        PublishedDoorPoint.from_publication(state, 6)


@pytest.mark.parametrize("width", [0, -1, 2**31, True])
def test_invalid_widths_are_rejected(width: int) -> None:
    with pytest.raises(ValidationError):
        PublishedDoorPoint.from_publication(RangePublication(7, DoorRangeQuery(height=3), None, None), width)
