"""Query-language completion tests that need no database."""

from collections.abc import Sequence

from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.suggestions.domain import SuggestionRequest
from squid.suggestions.infrastructure.providers.search_query import SearchQueryProvider


class StaticFields:
    async def fields(self) -> FieldRegistry:
        return DEFAULT_FIELD_REGISTRY


class FakeFacets:
    def __init__(self, values: dict[str, list[str]] | None = None) -> None:
        self.values = values or {}
        self.calls: list[tuple[str, str, int]] = []

    async def facet_values(self, field_name: str, prefix: str, *, limit: int) -> Sequence[str]:
        self.calls.append((field_name, prefix, limit))
        return [value for value in self.values.get(field_name, []) if value.casefold().startswith(prefix.casefold())]


def provider(values: dict[str, list[str]] | None = None) -> tuple[SearchQueryProvider, FakeFacets]:
    facets = FakeFacets(values)
    return SearchQueryProvider(StaticFields(), facets), facets


async def suggest(query: str, cursor: int | None = None, **values: list[str]):
    instance, facets = provider(values)
    result = await instance.suggest(SuggestionRequest(source="search_query", query=query, cursor=cursor, limit=10))
    return result, facets


async def test_a_bare_word_suggests_field_names_ready_for_a_value() -> None:
    result, _ = await suggest("restr")
    assert "restriction:" in [item.value for item in result.items]


async def test_a_field_alias_matches_its_canonical_name() -> None:
    result, _ = await suggest("completion_date")
    assert next(item.value for item in result.items) == "completion_at:"


async def test_boolean_keywords_are_offered_only_once_there_is_something_to_combine() -> None:
    with_prefix, _ = await suggest("an")
    assert "AND" in [item.value for item in with_prefix.items]
    empty, _ = await suggest("")
    assert "AND" not in [item.value for item in empty.items]


async def test_a_field_value_is_read_from_the_index() -> None:
    result, facets = await suggest("restriction:seam", restriction=["Seamless", "Sealed"])
    assert [item.label for item in result.items] == ["Seamless"]
    assert facets.calls == [("restriction", "seam", 10)]


async def test_facet_values_match_on_prefix_only() -> None:
    """The deliberate trade for an unbounded set.

    Small in-memory taxonomies go through the full matcher, which also does word-prefix, substring
    and fuzzy. Facet values cannot: there is no bound on how many a field has, so matching has to
    be something an index can serve, and that is a prefix.
    """
    result, _ = await suggest("restriction:seam", restriction=["Semi-Seamless"])
    assert result.items == ()


async def test_the_replacement_span_covers_only_the_value() -> None:
    result, _ = await suggest("kind:door restriction:sea", restriction=["Seamless"])
    assert result.replacement is not None
    assert (result.replacement.start, result.replacement.end) == (22, 25)


async def test_a_numeric_field_offers_no_values_to_scroll_through() -> None:
    result, facets = await suggest("width:5")
    assert result.items == ()
    assert facets.calls == []
    # The span is still reported, so a client knows nothing here is completable.
    assert result.replacement is not None


async def test_inside_a_range_nothing_is_suggested() -> None:
    result, facets = await suggest("width:[1 TO ")
    assert result.items == ()
    assert facets.calls == []


async def test_an_unknown_field_falls_back_to_completing_field_names() -> None:
    result, facets = await suggest("restrictoin:sea")
    assert facets.calls == []
    assert any(item.value.endswith(":") for item in result.items)


async def test_a_value_with_a_space_is_quoted_but_labelled_plainly() -> None:
    result, _ = await suggest("restriction:full", restriction=["Full Lamp"])
    assert [item.value for item in result.items] == ['"Full Lamp"']
    assert [item.label for item in result.items] == ["Full Lamp"]


async def test_an_open_quote_is_preserved_when_completing() -> None:
    result, _ = await suggest('creator:"Nu', creator=["Nuclear"])
    assert [item.value for item in result.items] == ['"Nuclear"']


async def test_database_order_is_preserved_rather_than_re_ranked() -> None:
    """Postgres already prefix-matched; re-ranking could only discard what it cannot re-derive."""
    result, _ = await suggest("creator:b", creator=["bob", "Bea", "bill"])
    assert [item.label for item in result.items] == ["bob", "Bea", "bill"]


async def test_an_explicit_cursor_completes_mid_query() -> None:
    result, facets = await suggest("restriction:sea AND width:3", cursor=15, restriction=["Seamless"])
    assert facets.calls == [("restriction", "sea", 10)]
    assert result.replacement is not None
    assert (result.replacement.start, result.replacement.end) == (12, 15)
