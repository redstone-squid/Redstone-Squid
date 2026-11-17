from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

from squid.records.application.models import CandidateBuild, CandidateFacet
from squid.records.application.services import RecordComputationService
from squid.records.application.snapshots import RecordSnapshot
from squid.records.domain import BuildKind, Candidate, DoorCategory
from squid.records.domain.models import FacetKind


def test_snapshot_json_preserves_numeric_and_text_facets_and_results() -> None:
    numeric = CandidateFacet(
        1,
        FacetKind.RESTRICTION,
        "Height",
        value_type="numeric",
        assigned_value=Decimal("1.25"),
        category_value=Decimal("2"),
        record_operator="at_most",
    )
    text = CandidateFacet(2, FacetKind.RESTRICTION, "Material", value_type="text", assigned_value="1.25")
    snapshot = RecordSnapshot(
        rules_revision_id=1,
        kind=BuildKind.DOOR,
        version_id=None,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        requested=(),
        sources=(
            CandidateBuild(
                BuildKind.DOOR,
                Candidate(1, fixed_volume=10),
                frozenset({2, 1}),
                (numeric, text),
                (),
                door=DoorCategory((), (), "2x2", ("Regular",), "Door"),
            ),
        ),
    )

    replay = RecordSnapshot.model_validate_json(snapshot.model_dump_json())

    assert replay == snapshot
    assert isinstance(replay.sources[0].restrictions[0].assigned_value, Decimal)
    assert isinstance(replay.sources[0].restrictions[1].assigned_value, str)
    service = RecordComputationService(uow_factory=Mock())
    assert service.compute_snapshot(replay) == service.compute_snapshot(snapshot)
