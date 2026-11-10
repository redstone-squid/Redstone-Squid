"""Rank literal matches ahead of fuzzy matches.

For example, `sea` ranks `Seamless` (a prefix) ahead of `Super Seamless`
(a word prefix). Fuzzy scores only order candidates with no literal match.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from itertools import islice

from rapidfuzz import fuzz

from squid.suggestions.domain import Suggestion

FUZZY_SCORE_CUTOFF = 60.0
"""Minimum WRatio score accepted when no literal match exists."""

_WORD_BOUNDARY = re.compile(r"[^0-9a-z]+")


class MatchTier(IntEnum):
    """How literally a candidate matched, ordered worst to best."""

    FUZZY = 0
    SUBSTRING = 1
    WORD_PREFIX = 2
    PREFIX = 3
    EXACT = 4


@dataclass(frozen=True, slots=True)
class Candidate:
    """A suggestion together with every string a query may match it on."""

    suggestion: Suggestion
    terms: tuple[str, ...] = field(default=())

    @classmethod
    def create(
        cls,
        value: str,
        label: str | None = None,
        *,
        description: str | None = None,
        kind: str = "",
        terms: tuple[str, ...] = (),
    ) -> Candidate:
        """Build a candidate, matched on `terms` when given and on its label and value otherwise."""
        suggestion = Suggestion(value=value, label=label or value, description=description, kind=kind)
        return cls(suggestion, terms)

    def match_terms(self) -> tuple[str, ...]:
        """Return the strings to match, defaulting to the label and value."""
        if self.terms:
            return self.terms
        if self.suggestion.value == self.suggestion.label:
            return (self.suggestion.label,)
        return (self.suggestion.label, self.suggestion.value)


def rank(query: str, candidates: Iterable[Candidate], *, limit: int) -> tuple[Suggestion, ...]:
    """Order candidates by how well they complete `query` and take the best `limit`.

    Match tiers, from strongest to weakest, are exact, prefix, word prefix,
    substring, and fuzzy. Comparisons strip whitespace and ignore case. Within
    a tier, higher scores come first, followed by labels in casefolded order.
    Remaining ties preserve the input order.

    An empty query keeps the provider's order. Nonpositive limits return no items.
    """
    if limit <= 0:
        return ()
    normalized = _fold(query)
    if not normalized:
        return tuple(item.suggestion for item in islice(candidates, limit))

    scored: list[tuple[int, float, str, Suggestion]] = []
    for item in candidates:
        best = _best_match(normalized, item.match_terms())
        if best is None:
            continue
        tier, score = best
        # Negated so a plain ascending sort puts the strongest match first, while the label
        # tie-break stays ascending and keeps equal-scoring results in a stable, readable order.
        scored.append((-tier, -score, item.suggestion.label.casefold(), item.suggestion))
    scored.sort(key=lambda entry: entry[:3])
    return tuple(entry[3] for entry in scored[:limit])


def _best_match(query: str, terms: Sequence[str]) -> tuple[MatchTier, float] | None:
    best: tuple[MatchTier, float] | None = None
    for term in terms:
        scored = _score(query, _fold(term))
        if scored is not None and (best is None or scored > best):
            best = scored
    return best


def _score(query: str, term: str) -> tuple[MatchTier, float] | None:
    if not term:
        return None
    if term == query:
        return MatchTier.EXACT, 100.0
    if term.startswith(query):
        return MatchTier.PREFIX, 100.0
    if any(word.startswith(query) for word in _WORD_BOUNDARY.split(term) if word):
        return MatchTier.WORD_PREFIX, 100.0
    if query in term:
        return MatchTier.SUBSTRING, 100.0
    score = fuzz.WRatio(query, term)
    if score >= FUZZY_SCORE_CUTOFF:
        return MatchTier.FUZZY, score
    return None


def _fold(value: str) -> str:
    return value.strip().casefold()
