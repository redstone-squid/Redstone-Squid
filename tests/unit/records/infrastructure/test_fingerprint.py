from dataclasses import replace
from datetime import UTC, datetime, timedelta

from squid.core.records.application.live import DoorRecordLookup, evaluate_door_record
from squid.core.records.application.models import (
    CandidateBuild,
    CategoryCandidates,
    CategoryIdentity,
    ComputedRecordResult,
)
from squid.core.records.domain import (
    BuildKind,
    Candidate,
    FormattedTitle,
    RecordCriterion,
    RecordResolution,
    VersionScope,
)
from squid.core.records.domain.door_evidence import DoorRecordEvidence
from squid.core.records.infrastructure.fingerprint import record_fingerprint


def record() -> ComputedRecordResult:
    category = CategoryCandidates(
        CategoryIdentity(BuildKind.DOOR, "door|2x2|t[]|Door", ()),
        (),
        FormattedTitle("2x2 Door"),
        (Candidate(1, fixed_volume=10), Candidate(2, fixed_volume=20)),
    )
    return ComputedRecordResult(
        RecordCriterion.SMALLEST,
        VersionScope.ALL_TIME,
        None,
        category,
        FormattedTitle("Smallest 2x2 Door"),
        RecordResolution(holder_ids=(1,)),
    )


def test_losing_candidate_changes_do_not_republish() -> None:
    original = record()
    changed = replace(original.category, candidates=(Candidate(2, fixed_volume=30), Candidate(1, fixed_volume=10)))
    assert record_fingerprint(original) == record_fingerprint(replace(original, category=changed))


def test_holder_metrics_and_wording_change_fingerprint() -> None:
    original = record()
    changed = replace(original.category, candidates=(Candidate(1, fixed_volume=9), Candidate(2, fixed_volume=20)))
    assert record_fingerprint(original) != record_fingerprint(replace(original, category=changed))
    assert record_fingerprint(original) != record_fingerprint(
        replace(original, title=FormattedTitle("Smallest 2x2 Doors"))
    )


def test_evaluation_timestamp_does_not_hide_substantive_evidence_changes() -> None:
    original = record()
    evaluated = evaluate_door_record(
        DoorRecordLookup(opening=(2, 2, 1)),
        (CandidateBuild(BuildKind.DOOR, Candidate(1, fixed_volume=10), frozenset(), (), ()),),
        evaluated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    evidence = DoorRecordEvidence(evaluation=evaluated, sources=())
    first = replace(original, door_evidence=evidence)
    later = evidence.model_copy(
        update={"evaluation": replace(evaluated, evaluated_at=evaluated.evaluated_at + timedelta(days=1))}
    )
    assert record_fingerprint(first) == record_fingerprint(replace(original, door_evidence=later))
    changed = evidence.model_copy(
        update={"evaluation": replace(evaluated, configurations=(replace(evaluated.configurations[0], volume=9),))}
    )
    assert record_fingerprint(first) != record_fingerprint(replace(original, door_evidence=changed))
