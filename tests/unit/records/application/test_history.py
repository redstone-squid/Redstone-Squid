from datetime import UTC, datetime

from squid.core.records.application.history import reconstruct_history
from squid.core.records.domain.models import DOOR_TIMING_METHODS, BaseTitle, Candidate, TimingVariant


def test_unresolved_comparison_closes_previous_holder_period() -> None:
    early = datetime(2020, 1, 1, tzinfo=UTC)
    uncertain = datetime(2021, 1, 1, tzinfo=UTC)
    resolved = datetime(2022, 1, 1, tzinfo=UTC)
    candidates = (
        Candidate(1, completion_at=early, timing_variants=(TimingVariant((10, 2)),)),
        Candidate(2, completion_at=uncertain, timing_variants=(TimingVariant((10, None)),)),
        Candidate(3, completion_at=resolved, timing_variants=(TimingVariant((9, 2)),)),
    )

    history = reconstruct_history(BaseTitle.FASTEST, DOOR_TIMING_METHODS, candidates)

    assert [period.build_ids for period in history] == [(1,), (3,)]
    assert history[0].held_until == uncertain
    assert history[1].held_from == resolved
    assert history[1].held_until is None


def test_unresolved_final_comparison_does_not_leave_a_current_holder() -> None:
    early = datetime(2020, 1, 1, tzinfo=UTC)
    uncertain = datetime(2021, 1, 1, tzinfo=UTC)
    history = reconstruct_history(
        BaseTitle.FASTEST,
        DOOR_TIMING_METHODS,
        (
            Candidate(1, completion_at=early, timing_variants=(TimingVariant((10, 2)),)),
            Candidate(2, completion_at=uncertain, timing_variants=(TimingVariant((10, None)),)),
        ),
    )

    assert len(history) == 1
    assert history[0].held_until == uncertain
