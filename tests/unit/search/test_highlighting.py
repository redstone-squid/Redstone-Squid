"""Title emphasis is optional and never depends on output markup."""

import pytest

from squid.search.application import SearchQueryParser
from squid.search.application.highlighting import highlight_title


@pytest.mark.parametrize(
    ("title", "query", "emphasized"),
    [
        ("Smallest 2\N{MULTIPLICATION SIGN}2 piston door", "2x2", ["2\N{MULTIPLICATION SIGN}2"]),
        ("Café DOOR", "cafe door", ["Café", "DOOR"]),
        ("Straße door", "strasse", ["Straße"]),
        ("Piston door", "pistn", []),
        ("Doorbell", "door", []),
        ("Piston door", "description:piston", []),
        ("Piston door", "title:piston", ["Piston"]),
        ("Piston door", "NOT piston", []),
        ("Piston door", "piston OR description:door", []),
        ("Piston door", "piston (door OR slimeless)", ["Piston"]),
        ("Piston door", '"piston door"', ["Piston door"]),
        ("Piston door", "width:2", []),
    ],
)
def test_title_highlighting_respects_query_fields_and_structure(title: str, query: str, emphasized: list[str]) -> None:
    result = highlight_title(title, SearchQueryParser().parse(query))

    assert result.text == title
    assert [result.text[start:end] for start, end in result.spans] == emphasized
