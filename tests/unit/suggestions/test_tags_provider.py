"""Tag completions retain names and aliases when commands submit numeric IDs."""

from collections.abc import Sequence

import pytest

from squid.core.actors import ActingContext, Actor
from squid.core.suggestions.application import SuggestionRegistry, SuggestionService, SuggestionSource, content_revision
from squid.core.suggestions.application.queries import TagEntry
from squid.core.suggestions.domain import SourceKind, Suggestion, SuggestionRequest
from squid.core.suggestions.infrastructure.providers import TagProvider


class TagReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def tag_entries(
        self,
        semantic_kind: str,
        *,
        build_kind: str | None = None,
        authority: str | None = "official",
    ) -> Sequence[TagEntry]:
        self.calls.append((semantic_kind, build_kind, authority))
        return (
            TagEntry(42, "seamless", "Seamless", "pattern", ("flush", "hidden", "smooth", "invisible")),
            TagEntry(7, "compact", "Compact", "pattern"),
        )


@pytest.mark.parametrize("by_id", [False, True], ids=["stable-keys", "numeric-ids"])
async def test_tags_completions_match_names_and_aliases_without_reloading(by_id: bool) -> None:
    reader = TagReader()
    source = SuggestionSource(
        id="patterns",
        provider=TagProvider(reader, "pattern", build_kind="Door", authority=None, by_id=by_id),
        kind=SourceKind.ENUMERABLE,
    )
    service = SuggestionService(SuggestionRegistry.of((source,)))
    compact = Suggestion("7" if by_id else "compact", "Compact", kind="pattern")
    seamless = Suggestion(
        "42" if by_id else "seamless", "Seamless", description="also flush, hidden, smooth", kind="pattern"
    )

    result = await service.enumerate("patterns", context=ActingContext.direct(Actor()))
    assert result.items == (compact, seamless)
    assert result.revision == content_revision(result.items)
    # Even aliases omitted from the display hint remain searchable.
    for query in ("seamless", "flush", "invisible"):
        assert (
            await service.suggest(
                SuggestionRequest(source="patterns", query=query), context=ActingContext.direct(Actor())
            )
        ).items[0] == seamless
    numbered = await service.suggest(
        SuggestionRequest(source="patterns", query="42"), context=ActingContext.direct(Actor())
    )
    assert numbered.items == ((seamless,) if by_id else ())
    assert reader.calls == [("pattern", "Door", None)]
