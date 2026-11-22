"""Readable search labels describe filters and sorts without query syntax."""

import pytest

from squid.bot.catalogue.search.clauses import query_leaves
from squid.bot.catalogue.search.labels import filter_label, sort_label
from squid.core.search.application.parser import SearchQueryParser
from squid_i18n import Text


def _filters(query: str) -> list[str]:
    expression = SearchQueryParser().parse(query).expression
    assert expression is not None
    return [Text(filter_label(leaf)).resolve() for _, leaf in query_leaves(expression)]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("width<=5", ["Width ≤ 5"]),
        ("width:[3 TO 5]", ["Width 3 to 5"]),
        ("door NOT restriction:observerless", ["Keywords: door", "Not: Restriction is observerless"]),
        ('title:"vault door"', ["Title has “vault door”"]),
        ("version_scope:all_time", ["Version scope is all time"]),
    ],
)
def test_filters_are_described_in_words(query: str, expected: list[str]) -> None:
    assert _filters(query) == expected


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("", "Default order"),
        ("width", "Width, smallest first"),
        ("-height", "Height, largest first"),
        ("-created_at", "Submitted, newest first"),
    ],
)
def test_sorts_are_described_in_words(sort: str, expected: str) -> None:
    assert Text(sort_label(sort)).resolve() == expected


def test_a_creator_id_is_described_by_its_creator_name() -> None:
    creator_id = "9d10eb8e-3bd0-44d6-a207-a6a4dc564e58"
    expression = SearchQueryParser().parse(f"creator:{creator_id.upper()}").expression
    assert expression is not None

    assert Text(filter_label(expression, {creator_id: "Red Stone"})).resolve() == "Creator is Red Stone"
