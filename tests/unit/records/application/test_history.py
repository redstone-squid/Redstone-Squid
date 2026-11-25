from datetime import UTC, datetime

from squid.core.records.application.history import reconstruct_history
from squid.core.records.domain.models import DOOR_TIMING_METHODS, Candidate, RecordCriterion, TimingVariant


def test_missing_tie_breaker_does_not_interrupt_the_holder() -> None:
    early = datetime(2020, 1, 1, tzinfo=UTC)
    tied = datetime(2021, 1, 1, tzinfo=UTC)
    faster = datetime(2022, 1, 1, tzinfo=UTC)
    candidates = (
        Candidate(1, completion_at=early, timing_variants=(TimingVariant((10, 2)),)),
        Candidate(2, completion_at=tied, timing_variants=(TimingVariant((10, None)),)),
        Candidate(3, completion_at=faster, timing_variants=(TimingVariant((9, 2)),)),
    )

    history = reconstruct_history(RecordCriterion.FASTEST, DOOR_TIMING_METHODS, candidates)

    assert [period.build_ids for period in history] == [(1,), (3,)]
    assert history[0].held_until == faster
    assert history[1].held_until is None
