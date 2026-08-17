"""Ranking helpers that mention no table, column, or index."""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from squid.core.errors import ValidationError
from squid.core.i18n import _


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
    """A document identity and deterministic display-order fields."""

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
    """Fuse ranked candidate branches with deterministic tie-breaking."""
    if k <= 0:
        msg = _("k must be positive")
        raise ValidationError(msg)
    if branch_limit <= 0:
        msg = _("branch_limit must be positive")
        raise ValidationError(msg)
    scores: defaultdict[tuple[str, str], float] = defaultdict(float)
    documents: dict[tuple[str, str], RankedCandidate] = {}
    for branch, candidates in branches.items():
        weight = weights.get(branch, 0)
        if weight < 0:
            msg = _("weight for {branch} cannot be negative")
            raise ValidationError(msg, message_params={"branch": branch.value})
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
