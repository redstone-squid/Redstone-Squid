"""Search ranking tests."""

import pytest

from squid.search.application import (
    RankedCandidate,
    RankingBranch,
    reciprocal_rank_fusion,
)


def test_rrf_combines_branches_and_deduplicates_within_each_branch() -> None:
    door = RankedCandidate(1)
    extender = RankedCandidate(2)

    ranked = reciprocal_rank_fusion(
        {
            RankingBranch.EXACT: (door, door),
            RankingBranch.FULL_TEXT: (extender, door),
        }
    )

    assert [candidate.document_id for candidate in ranked] == [1, 2]
    assert ranked[0].score == pytest.approx(4 / 61 + 2 / 62)
    assert ranked[1].score == pytest.approx(2 / 61)
    assert ranked[0].branches == (RankingBranch.EXACT, RankingBranch.FULL_TEXT)
    assert ranked[1].branches == (RankingBranch.FULL_TEXT,)


def test_rrf_applies_branch_limit_before_fusion() -> None:
    ranked = reciprocal_rank_fusion(
        {RankingBranch.EXACT: (RankedCandidate(1), RankedCandidate(2))},
        branch_limit=1,
    )

    assert [candidate.document_id for candidate in ranked] == [1]


def test_rrf_orders_by_fused_score_across_branches() -> None:
    ranked = reciprocal_rank_fusion(
        {
            RankingBranch.TRIGRAM: (
                RankedCandidate(2),
                RankedCandidate(1),
            ),
            RankingBranch.FULL_TEXT: (
                RankedCandidate(1),
                RankedCandidate(2),
            ),
        }
    )

    assert [candidate.document_id for candidate in ranked] == [1, 2]


def test_rrf_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="positive"):
        reciprocal_rank_fusion({}, k=0)
    with pytest.raises(ValueError, match="cannot be negative"):
        reciprocal_rank_fusion(
            {RankingBranch.EXACT: (RankedCandidate(1),)},
            weights={RankingBranch.EXACT: -1},
        )
