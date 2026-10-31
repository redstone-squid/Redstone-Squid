"""Record suggestion providers must never surface internal category keys."""

from collections.abc import Sequence

from squid.suggestions.domain import SuggestionRequest
from squid.suggestions.infrastructure.providers.records import CompetitionProvider, RecordDefinitionProvider


class FakeDefinitions:
    def __init__(self, rows: Sequence[tuple[int, str, str]]) -> None:
        self.rows = tuple(rows)
        self.calls: list[tuple[str, int]] = []

    async def record_definitions(self, query: str, *, limit: int) -> Sequence[tuple[int, str, str]]:
        self.calls.append((query, limit))
        return self.rows


async def test_definitions_are_offered_by_title_and_submit_the_id() -> None:
    reader = FakeDefinitions([(42, "Smallest Flush 2x2 Door", "door")])
    provider = RecordDefinitionProvider(reader)

    (item,) = await provider.candidates(SuggestionRequest(source="record_definitions", query="flush", limit=5))

    assert item.suggestion.value == "42"
    assert item.suggestion.label == "Smallest Flush 2x2 Door"
    assert item.suggestion.description == "door"
    assert item.match_terms() == ("Smallest Flush 2x2 Door", "42")
    assert reader.calls == [("flush", 5)]


class FakeCompetitions:
    def __init__(self, rows: Sequence[tuple[str, str, str | None]]) -> None:
        self.rows = tuple(rows)

    async def competitions(self, query: str, *, limit: int) -> Sequence[tuple[str, str, str | None]]:
        return self.rows


async def test_competitions_are_labelled_by_title_alone() -> None:
    uuid = "3e1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"
    provider = CompetitionProvider(FakeCompetitions([(uuid, "Fastest 2x2 Door", "All-time")]))

    (item,) = await provider.candidates(SuggestionRequest(source="competitions", query="fastest", limit=5))

    assert item.suggestion.value == uuid
    assert item.suggestion.label == "Fastest 2x2 Door"
    assert item.suggestion.description == "All-time"
    assert item.match_terms() == ("Fastest 2x2 Door", "All-time")
