"""Combine ordered search results without depending on database score scales.

A ranking branch is one way of finding candidates: exact title, full-text,
trigram, or semantic matching. Each branch supplies its own best-first list.
Reciprocal rank fusion rewards documents near the top of those lists and adds
their contributions when several branches find the same document.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from squid.core.errors import ValidationError
from squid.core.i18n import tr


class RankingBranch(StrEnum):
    """Candidate sources fused into the final ranking."""

    EXACT = "exact"
    FULL_TEXT = "full_text"
    TRIGRAM = "trigram"
    SEMANTIC = "semantic"


DEFAULT_RRF_WEIGHTS: Mapping[RankingBranch, float] = {
    RankingBranch.EXACT: 4.0,
    RankingBranch.FULL_TEXT: 2.0,
    RankingBranch.TRIGRAM: 1.0,
    RankingBranch.SEMANTIC: 1.5,
}


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    """A result identity, title for breaking ties, and optional ranking score."""

    source_id: str
    resource_kind: Literal["record", "build", "metadata"]
    normalized_title: str
    score: float = 0


@dataclass(frozen=True, slots=True)
class SearchDocumentOrder:
    """Fields required to order an unranked, filter-only result."""

    source_id: str
    resource_kind: Literal["record", "build", "metadata"]
    normalized_title: str


def reciprocal_rank_fusion(
    branches: Mapping[RankingBranch, Sequence[RankedCandidate]],
    *,
    weights: Mapping[RankingBranch, float] = DEFAULT_RRF_WEIGHTS,
    k: int = 60,
    branch_limit: int = 200,
) -> tuple[RankedCandidate, ...]:
    """Combine result lists by weighted reciprocal rank.

    Each branch must already be ordered from best to worst. A document receives
    `weight / (k + rank)` from each branch that contains it, with ranks starting
    at one. Input scores are ignored because the branches use different scales.

    Only the first `branch_limit` entries of each branch are considered. Repeated
    identities within that window count once and do not consume a rank. Ties in
    the final score are ordered by case-folded title, resource kind, and source ID.

    Raises:
        ValidationError: If k or branch_limit is not positive, or a used weight
            is negative.
    """
    if k <= 0:
        raise ValidationError(tr(t"search-k-positive = k must be positive"))
    if branch_limit <= 0:
        raise ValidationError(tr(t"search-branch-limit-positive = branch_limit must be positive"))
    scores: defaultdict[tuple[str, str], float] = defaultdict(float)
    documents: dict[tuple[str, str], RankedCandidate] = {}
    for branch, candidates in branches.items():
        weight = weights.get(branch, 0)
        if weight < 0:
            branch_name = branch.value
            raise ValidationError(tr(t"search-weight-negative = weight for {branch_name} cannot be negative"))
        seen: set[tuple[str, str]] = set()
        rank = 0
        for candidate in candidates[:branch_limit]:
            key = (candidate.resource_kind, candidate.source_id)
            if key in seen:
                continue
            seen.add(key)
            rank += 1
            documents[key] = candidate
            scores[key] += weight / (k + rank)
    fused = (
        RankedCandidate(
            source_id=document.source_id,
            resource_kind=document.resource_kind,
            normalized_title=document.normalized_title,
            score=scores[key],
        )
        for key, document in documents.items()
    )
    return tuple(sorted(fused, key=_ranked_sort_key))


def sort_filter_only(documents: Iterable[SearchDocumentOrder]) -> tuple[SearchDocumentOrder, ...]:
    """Sort filter-only matches independently of database return order."""
    return tuple(
        sorted(
            documents,
            key=lambda item: (item.normalized_title.casefold(), item.resource_kind, item.source_id),
        )
    )


def _ranked_sort_key(candidate: RankedCandidate) -> tuple[float, str, str, str]:
    return (
        -candidate.score,
        candidate.normalized_title.casefold(),
        candidate.resource_kind,
        candidate.source_id,
    )
