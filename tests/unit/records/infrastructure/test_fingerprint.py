from dataclasses import replace
from datetime import UTC, datetime, timedelta

from squid.records.application.live import DoorRecordLookup, evaluate_door_record
from squid.records.application.models import (
    CandidateBuild,
    CategoryCandidates,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecord,
)
from squid.records.domain import (
    BaseTitle,
    BuildKind,
    Candidate,
    FormattedTitle,
    ResolutionStatus,
    TitleResolution,
    VersionScope,
)
from squid.records.domain.door_evidence import DoorRecordEvidence
from squid.records.infrastructure.fingerprint import publication_fingerprint


def batch() -> ComputationBatch:
    category = CategoryCandidates(
        CategoryIdentity(BuildKind.DOOR, "door|2x2|t[]|Door", ()),
        (),
        FormattedTitle("2x2 Door"),
        (Candidate(1, fixed_volume=10), Candidate(2, fixed_volume=20)),
    )
    return ComputationBatch(
        1,
        BuildKind.DOOR,
        None,
        (
            ComputedRecord(
                BaseTitle.SMALLEST,
                VersionScope.ALL_TIME,
                None,
                category,
                FormattedTitle("Smallest 2x2 Door"),
                TitleResolution(ResolutionStatus.RESOLVED, holder_ids=(1,)),
            ),
        ),
    )


def test_losing_candidate_changes_do_not_republish() -> None:
    original = batch()
    record = original.records[0]
    changed = replace(record.category, candidates=(Candidate(2, fixed_volume=30), Candidate(1, fixed_volume=10)))
    assert publication_fingerprint(original) == publication_fingerprint(
        replace(original, records=(replace(record, category=changed),))
    )


def test_holder_metrics_and_rules_change_fingerprint() -> None:
    original = batch()
    record = original.records[0]
    changed = replace(record.category, candidates=(Candidate(1, fixed_volume=9), Candidate(2, fixed_volume=20)))
    assert publication_fingerprint(original) != publication_fingerprint(
        replace(original, records=(replace(record, category=changed),))
    )
    assert publication_fingerprint(original) != publication_fingerprint(replace(original, rules_revision_id=2))


def test_evaluation_timestamp_does_not_hide_substantive_evidence_changes() -> None:
    original = batch()
    record = original.records[0]
    evaluated = evaluate_door_record(
        DoorRecordLookup(opening=(2, 2, 1)),
        (CandidateBuild(BuildKind.DOOR, Candidate(1, fixed_volume=10), frozenset(), (), ()),),
        evaluated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    evidence = DoorRecordEvidence(evaluation=evaluated, sources=())
    first = replace(original, records=(replace(record, door_evidence=evidence),))
    later = evidence.model_copy(
        update={"evaluation": replace(evaluated, evaluated_at=evaluated.evaluated_at + timedelta(days=1))}
    )
    assert publication_fingerprint(first) == publication_fingerprint(
        replace(original, records=(replace(record, door_evidence=later),))
    )
    changed = evidence.model_copy(
        update={"evaluation": replace(evaluated, configurations=(replace(evaluated.configurations[0], volume=9),))}
    )
    assert publication_fingerprint(first) != publication_fingerprint(
        replace(original, records=(replace(record, door_evidence=changed),))
    )
