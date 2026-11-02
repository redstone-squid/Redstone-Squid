"""Shared suggestion ranking tests."""

from squid.suggestions.application.matching import Candidate, MatchTier, candidate, rank
from squid.suggestions.domain import Suggestion

RESTRICTIONS = [
    candidate("seamless", "Seamless"),
    candidate("semi_seamless", "Semi-Seamless"),
    candidate("full_lamp", "Full Lamp"),
    candidate("flush", "Flush"),
    candidate("skydoor", "Skydoor"),
]


def labels(suggestions: tuple[Suggestion, ...]) -> list[str]:
    return [item.label for item in suggestions]


def test_prefix_outranks_word_prefix() -> None:
    assert labels(rank("sea", RESTRICTIONS, limit=5)) == ["Seamless", "Semi-Seamless"]


def test_word_prefix_matches_a_later_word() -> None:
    assert labels(rank("lamp", RESTRICTIONS, limit=5)) == ["Full Lamp"]


def test_fuzzy_matches_a_typo_that_matches_nothing_literally() -> None:
    assert labels(rank("flsh", RESTRICTIONS, limit=5)) == ["Flush"]


def test_exact_match_leads_even_when_another_candidate_shares_the_prefix() -> None:
    candidates = [candidate("door_frame", "Door Frame"), candidate("door", "Door")]
    assert labels(rank("door", candidates, limit=5)) == ["Door", "Door Frame"]


def test_unrelated_query_returns_nothing() -> None:
    assert rank("zzzzz", RESTRICTIONS, limit=5) == ()


def test_empty_query_preserves_provider_order_as_the_default_page() -> None:
    assert labels(rank("", RESTRICTIONS, limit=3)) == ["Seamless", "Semi-Seamless", "Full Lamp"]


def test_limit_is_respected() -> None:
    assert len(rank("s", RESTRICTIONS, limit=2)) == 2
    assert rank("s", RESTRICTIONS, limit=0) == ()


def test_aliases_match_but_the_canonical_label_is_returned() -> None:
    aliased = [Candidate(Suggestion("seamless", "Seamless"), terms=("Seamless", "flush lamp"))]
    assert labels(rank("flush lamp", aliased, limit=5)) == ["Seamless"]


def test_matching_is_case_and_whitespace_insensitive() -> None:
    assert labels(rank("  SEAM ", RESTRICTIONS, limit=5)) == labels(rank("seam", RESTRICTIONS, limit=5))
    assert labels(rank("  SEAM ", RESTRICTIONS, limit=5))[0] == "Seamless"


def test_value_is_matched_when_it_differs_from_the_label() -> None:
    assert labels(rank("semi_seam", RESTRICTIONS, limit=5)) == ["Semi-Seamless"]


def test_ties_break_on_label_so_ordering_is_stable() -> None:
    candidates = [candidate("b", "Beta Door"), candidate("a", "Alpha Door")]
    assert labels(rank("door", candidates, limit=5)) == ["Alpha Door", "Beta Door"]


def test_tiers_are_ordered_worst_to_best() -> None:
    assert MatchTier.FUZZY < MatchTier.SUBSTRING < MatchTier.WORD_PREFIX < MatchTier.PREFIX < MatchTier.EXACT
