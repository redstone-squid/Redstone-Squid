"""Combine ordered search results without depending on database score scales.

A ranking branch is one way of finding candidates: exact title, full-text,
trigram, or semantic matching. Each branch supplies its own best-first list.
Reciprocal rank fusion rewards documents near the top of those lists and adds
their contributions when several branches find the same document.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid_i18n import md_tr


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
    """A search document's ID with its ranking score and the branches that found it."""

    document_id: int
    score: float = 0
    branches: tuple[RankingBranch, ...] = ()


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
    documents within that window count once and do not consume a rank. The
    result is ordered best first, with ties in document ID order.

    Raises:
        ValidationError: If k or branch_limit is not positive, or a used weight
            is negative.
    """
    if k <= 0:
        raise ValidationError(tr(t"search-k-positive = k must be positive"))
    if branch_limit <= 0:
        raise ValidationError(tr(t"search-branch-limit-positive = branch_limit must be positive"))
    scores: defaultdict[int, float] = defaultdict(float)
    contributions: defaultdict[int, list[RankingBranch]] = defaultdict(list)
    for branch, candidates in branches.items():
        weight = weights.get(branch, 0)
        if weight < 0:
            branch_name = branch.value
            raise ValidationError(md_tr(t"search-weight-negative = weight for {branch_name} cannot be negative"))
        seen: set[int] = set()
        for candidate in candidates[:branch_limit]:
            key = candidate.document_id
            if key in seen:
                continue
            seen.add(key)
            contributions[key].append(branch)
            scores[key] += weight / (k + len(seen))
    fused = (RankedCandidate(key, scores[key], tuple(found_by)) for key, found_by in contributions.items())
    return tuple(sorted(fused, key=_ranked_sort_key))


def _ranked_sort_key(candidate: RankedCandidate) -> tuple[float, int]:
    return (-candidate.score, candidate.document_id)
