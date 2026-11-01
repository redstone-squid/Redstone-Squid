"""Taxonomy completions retain names and aliases when commands submit numeric IDs."""

from collections.abc import Sequence

import pytest

from squid.suggestions.application import SuggestionRegistry, SuggestionService, SuggestionSource, content_revision
from squid.suggestions.domain import SourceKind, Suggestion, SuggestionRequest
from squid.suggestions.infrastructure.providers import TaxonomyProvider
from squid.suggestions.infrastructure.repository import TaxonomyEntry


class TaxonomyReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def taxonomy(
        self,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
    ) -> Sequence[TaxonomyEntry]:
        self.calls.append((semantic_kind, build_kind, authority))
        return (
            TaxonomyEntry(42, "seamless", "Seamless", "pattern", ("flush", "hidden", "smooth", "invisible")),
            TaxonomyEntry(7, "compact", "Compact", "pattern"),
        )


@pytest.mark.parametrize("by_id", [False, True], ids=["stable-keys", "numeric-ids"])
async def test_taxonomy_completions_match_names_and_aliases_without_reloading(by_id: bool) -> None:
    reader = TaxonomyReader()
    source = SuggestionSource(
        id="patterns",
        provider=TaxonomyProvider(reader, "pattern", build_kind="Door", authority=None, by_id=by_id),
        kind=SourceKind.ENUMERABLE,
    )
    service = SuggestionService(SuggestionRegistry.of((source,)))
    compact = Suggestion("7" if by_id else "compact", "Compact", kind="pattern")
    seamless = Suggestion(
        "42" if by_id else "seamless", "Seamless", description="also flush, hidden, smooth", kind="pattern"
    )

    result = await service.enumerate("patterns")
    assert result.items == (compact, seamless)
    assert result.revision == content_revision(result.items)
    # Even aliases omitted from the display hint remain searchable.
    for query in ("seamless", "flush", "invisible"):
        assert (await service.suggest(SuggestionRequest(source="patterns", query=query))).items[0] == seamless
    numbered = await service.suggest(SuggestionRequest(source="patterns", query="42"))
    assert numbered.items == ((seamless,) if by_id else ())
    assert reader.calls == [("pattern", "Door", None)]
