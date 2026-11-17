from datetime import UTC, datetime

from squid.records.application.services import _reconstruct_history
from squid.records.domain.models import BaseTitle, BuildKind, Candidate, TimingVariant


def test_unresolved_comparison_closes_previous_holder_period() -> None:
    early = datetime(2020, 1, 1, tzinfo=UTC)
    uncertain = datetime(2021, 1, 1, tzinfo=UTC)
    resolved = datetime(2022, 1, 1, tzinfo=UTC)
    candidates = (
        Candidate(1, completion_at=early, timing_variants=(TimingVariant((10, 2)),)),
        Candidate(2, completion_at=uncertain, timing_variants=(TimingVariant((10, None)),)),
        Candidate(3, completion_at=resolved, timing_variants=(TimingVariant((9, 2)),)),
    )

    history = _reconstruct_history(BaseTitle.FASTEST, BuildKind.DOOR, candidates)

    assert [period.build_ids for period in history] == [(1,), (3,)]
    assert history[0].held_until == uncertain
    assert history[1].held_from == resolved
    assert history[1].held_until is None


def test_unresolved_final_comparison_does_not_leave_a_current_holder() -> None:
    early = datetime(2020, 1, 1, tzinfo=UTC)
    uncertain = datetime(2021, 1, 1, tzinfo=UTC)
    history = _reconstruct_history(
        BaseTitle.FASTEST,
        BuildKind.DOOR,
        (
            Candidate(1, completion_at=early, timing_variants=(TimingVariant((10, 2)),)),
            Candidate(2, completion_at=uncertain, timing_variants=(TimingVariant((10, None)),)),
        ),
    )

    assert len(history) == 1
    assert history[0].held_until == uncertain
