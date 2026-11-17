from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

from squid.records.application.models import CandidateBuild, CandidateFacet
from squid.records.application.services import RecordComputationService
from squid.records.application.snapshots import RecordSnapshot
from squid.records.domain import BuildKind, Candidate, DoorCategory
from squid.records.domain.models import FacetKind


def test_captured_inputs_preserve_facet_types_and_compute_deterministically() -> None:
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

    assert isinstance(snapshot.sources[0].restrictions[0].assigned_value, Decimal)
    assert isinstance(snapshot.sources[0].restrictions[1].assigned_value, str)
    service = RecordComputationService(uow_factory=Mock())
    assert service.compute_snapshot(snapshot) == service.compute_snapshot(snapshot)
