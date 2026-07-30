"""Search ranking tests."""

import pytest

from squid.search.application import (
    RankedCandidate,
    RankingBranch,
    SearchDocumentOrder,
    reciprocal_rank_fusion,
    sort_filter_only,
)


def _candidate(source_id: str, title: str) -> RankedCandidate:
    return RankedCandidate(source_id, "record", title)


def test_rrf_combines_branches_and_deduplicates_within_each_branch() -> None:
    door = _candidate("door", "Door")
    extender = _candidate("extender", "Extender")

    ranked = reciprocal_rank_fusion(
        {
            RankingBranch.EXACT: (door, door),
            RankingBranch.FULL_TEXT: (extender, door),
        }
    )

    assert [candidate.source_id for candidate in ranked] == ["door", "extender"]
    assert ranked[0].score == pytest.approx(4 / 61 + 2 / 62)
    assert ranked[1].score == pytest.approx(2 / 61)


def test_rrf_applies_branch_limit_before_fusion() -> None:
    ranked = reciprocal_rank_fusion(
        {RankingBranch.EXACT: (_candidate("1", "One"), _candidate("2", "Two"))},
        branch_limit=1,
    )

    assert [candidate.source_id for candidate in ranked] == ["1"]


def test_rrf_uses_deterministic_tie_order() -> None:
    ranked = reciprocal_rank_fusion(
        {
            RankingBranch.TRIGRAM: (
                _candidate("2", "Beta"),
                _candidate("1", "Alpha"),
            ),
            RankingBranch.FULL_TEXT: (
                _candidate("1", "Alpha"),
                _candidate("2", "Beta"),
            ),
        }
    )

    assert [candidate.source_id for candidate in ranked] == ["1", "2"]


def test_filter_only_order_is_title_kind_and_id() -> None:
    documents = (
        SearchDocumentOrder("2", "record", "alpha"),
        SearchDocumentOrder("1", "record", "Alpha"),
        SearchDocumentOrder("3", "build", "Beta"),
    )

    assert [document.source_id for document in sort_filter_only(documents)] == ["1", "2", "3"]


def test_rrf_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="positive"):
        reciprocal_rank_fusion({}, k=0)
    with pytest.raises(ValueError, match="cannot be negative"):
        reciprocal_rank_fusion(
            {RankingBranch.EXACT: (_candidate("1", "One"),)},
            weights={RankingBranch.EXACT: -1},
        )
