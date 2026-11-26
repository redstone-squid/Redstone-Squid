"""Tests for explaining which values made a document match."""

from decimal import Decimal

from squid.core.search.application import SearchQueryParser
from squid.core.search.application.explanation import explain
from squid.core.search.domain import FieldMatch, FieldValue, SearchEvidence
from squid.core.search.domain.text import TermExpansion

_RECORD = (
    FieldValue("title", "Fastest 2x2 Glass Door"),
    FieldValue("subtitle", "No Entities"),
    FieldValue("base_title", "fastest"),
    FieldValue("restriction", "No Entities"),
    FieldValue("restriction", "Entityless"),
    FieldValue("holder", "8329", 8329),
    FieldValue("holder_title", "No Furnaces Door", 8329),
    FieldValue("volume", Decimal(12), 8329),
)


_EXACT = TermExpansion()


def _explain(query: str, *, expansion: TermExpansion = _EXACT) -> SearchEvidence | None:
    return explain(SearchQueryParser().parse(query), _RECORD, record=True, expansion=expansion)


def test_a_holder_match_names_the_holder() -> None:
    evidence = _explain("no furnaces")

    assert evidence is not None
    assert evidence.highlight("No Furnaces Door", "holder_title").spans == ((0, 2), (3, 11))
    assert {match.holder_build_id for match in evidence.matches if match.field == "holder_title"} == {8329}


def test_every_satisfied_or_branch_is_highlighted() -> None:
    evidence = _explain("fastest 2x2 (glass OR door)")

    assert evidence is not None
    # A leading criterion is a filter on the record's criterion, not a word of its title.
    assert FieldMatch("base_title", "fastest", ((0, 7),)) in evidence.matches
    assert evidence.highlight("Fastest 2x2 Glass Door", "title").spans == ((8, 11), (12, 17), (18, 22))


def test_negated_terms_give_no_evidence() -> None:
    evidence = _explain("glass NOT slime")

    assert evidence is not None
    assert evidence.highlight("Fastest 2x2 Glass Door", "title").spans == ((12, 17),)


def test_a_bare_alias_matches_the_category_restriction() -> None:
    evidence = _explain("entityless")

    assert evidence is not None
    assert [match.field for match in evidence.matches] == ["restriction"]
    assert _explain("restriction:entityless") is not None


def test_close_spellings_are_marked() -> None:
    evidence = _explain("glas", expansion=TermExpansion({"glas": frozenset({"glass"})}))

    assert evidence is not None
    assert evidence.close
    assert evidence.highlight("Fastest 2x2 Glass Door", "title").spans == ((12, 17),)


def test_a_phrase_must_appear_in_order_within_one_value() -> None:
    assert _explain('"glass door"') is not None
    assert _explain('"door glass"') is None
    assert _explain('"entities no"') is None


def test_holder_measurements_match_through_one_holder() -> None:
    evidence = _explain("volume<20")

    assert evidence is not None
    assert evidence.matches == (FieldMatch("volume", "12", holder_build_id=8329),)
    assert _explain("volume>20") is None
    assert _explain("NOT volume>20") is not None
    assert _explain("NOT visible_opening_time>0") is None  # Unknown under NOT stays unknown.


def test_a_document_that_does_not_match_has_no_evidence() -> None:
    assert _explain("piston") is None
