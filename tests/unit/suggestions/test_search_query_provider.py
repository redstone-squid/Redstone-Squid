"""Query-language completion tests that need no database."""

from collections.abc import Iterator, Sequence

import pytest

from squid.core.actors import ActingContext, Actor
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldDefinition, FieldRegistry, FieldType
from squid.core.suggestions.application.queries import TagEntry
from squid.core.suggestions.domain import SuggestionRequest
from squid.core.suggestions.infrastructure.providers.search_fields import SearchFieldProvider
from squid.core.suggestions.infrastructure.providers.search_query import SearchQueryProvider


@pytest.fixture(autouse=True)
def public_query() -> Iterator[None]:
    with ActingContext.direct(Actor()).scope():
        yield


class StaticFields:
    def __init__(self, registry: FieldRegistry = DEFAULT_FIELD_REGISTRY) -> None:
        self.registry = registry

    async def fields(self, *, context: ActingContext) -> FieldRegistry:
        return self.registry


class FakeFilterValues:
    def __init__(self, values: dict[str, list[str]] | None = None) -> None:
        self.values = values or {}
        self.calls: list[tuple[str, str, int]] = []

    async def tag_entries(
        self, semantic_kind: str, *, build_kind: str | None = None, authority: str | None = "official"
    ) -> Sequence[TagEntry]:
        if semantic_kind != "pattern":
            return []
        assert authority is None
        return [
            TagEntry(1, "a", "Alpha", "pattern", aliases=("woven",), authority="user"),
            TagEntry(2, "z", "Zig Zag", "pattern", aliases=("woven",)),
        ]

    async def creator_profiles(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]:
        profiles = [("9d10eb8e-3bd0-44d6-a207-a6a4dc564e58", "Bob")]
        return [profile for profile in profiles if profile[1].casefold().startswith(query.casefold())][:limit]

    async def filter_values(self, field_name: str, prefix: str, *, limit: int) -> Sequence[str]:
        self.calls.append((field_name, prefix, limit))
        return [value for value in self.values.get(field_name, []) if value.casefold().startswith(prefix.casefold())]


def provider(values: dict[str, list[str]] | None = None) -> tuple[SearchQueryProvider, FakeFilterValues]:
    filters = FakeFilterValues(values)
    return SearchQueryProvider(StaticFields(), filters), filters


async def suggest(query: str, cursor: int | None = None, **values: list[str]):
    instance, filters = provider(values)
    result = await instance.suggest(SuggestionRequest(source="search_query", query=query, cursor=cursor, limit=10))
    return result, filters


async def test_freeform_alias_prefers_official_patterns_and_replaces_one_term() -> None:
    result, _ = await suggest("width:3 wove")
    assert [item.value for item in result.items[:2]] == ['pattern:"Zig Zag"', 'pattern:"Alpha"']
    assert result.replacement is not None
    assert (result.replacement.start, result.replacement.end) == (8, 12)


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
    result, filters = await suggest("restriction:seam", restriction=["Seamless", "Sealed"])
    assert [item.label for item in result.items] == ["Seamless"]
    assert filters.calls == [("restriction", "seam", 10)]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("criterion:", ["fastest", "fastest_smallest", "first", "smallest", "smallest_fastest"]),
        ("criterion:FAST", ["fastest", "fastest_smallest"]),
        ('criterion:"small', ['"smallest"', '"smallest_fastest"']),
        ("criterion:unknown", []),
    ],
)
async def test_record_criteria_are_suggested_without_indexed_records(query: str, expected: list[str]) -> None:
    result, filters = await suggest(query)

    assert [item.value for item in result.items] == expected
    assert filters.calls == []
    assert result.replacement is not None
    assert (result.replacement.start, result.replacement.end) == (len("criterion:"), len(query))


async def test_record_criterion_suggestions_respect_the_limit() -> None:
    instance, _ = provider()
    result = await instance.suggest(SuggestionRequest(source="search_query", query="criterion:", limit=2))

    assert [item.value for item in result.items] == ["fastest", "fastest_smallest"]


async def test_version_scopes_are_suggested_without_indexed_records() -> None:
    result, filters = await suggest("version_scope:")

    assert [item.value for item in result.items] == ["all_time", "up_to_date"]
    assert filters.calls == []


async def test_catalogue_completions_use_the_scoped_registry() -> None:
    instance, _ = provider()
    for scope, status in (("builds", "approved"), ("records", "resolved")):
        result = await instance.suggest(
            SuggestionRequest(source="search_query", query="status:", context={"scope": scope})
        )
        assert status in {item.value for item in result.items}
        assert len(result.items) == 3

    fields = await SearchFieldProvider(StaticFields()).candidates(
        SuggestionRequest(source="search_fields", context={"scope": "records"})
    )
    names = {item.suggestion.value for item in fields}
    assert {"kind", "criterion", "status"} <= names
    assert "width" not in names


@pytest.mark.parametrize("values", [("Full Lamp", "Flush", "Seamless"), ()])
async def test_any_field_can_declare_fixed_suggestions(values: tuple[str, ...]) -> None:
    registry = FieldRegistry((FieldDefinition("custom", FieldType.TEXT, suggested_values=values),))
    filters = FakeFilterValues({"custom": ["From index"]})
    instance = SearchQueryProvider(StaticFields(registry), filters)

    result = await instance.suggest(SuggestionRequest(source="search_query", query="custom:F", limit=10))

    assert [item.value for item in result.items] == (["Flush", '"Full Lamp"'] if values else [])
    assert filters.calls == []


async def test_the_replacement_span_covers_only_the_value() -> None:
    result, _ = await suggest("kind:door restriction:sea", restriction=["Seamless"])
    assert result.replacement is not None
    assert (result.replacement.start, result.replacement.end) == (22, 25)


async def test_a_numeric_field_offers_no_values_to_scroll_through() -> None:
    result, filters = await suggest("width:5")
    assert result.items == ()
    assert filters.calls == []
    # The empty result still identifies the token under the caret.
    assert result.replacement is not None


async def test_inside_a_range_nothing_is_suggested() -> None:
    result, filters = await suggest("width:[1 TO ")
    assert result.items == ()
    assert filters.calls == []


async def test_an_unknown_field_falls_back_to_completing_field_names() -> None:
    result, filters = await suggest("restrictoin:sea")
    assert filters.calls == []
    assert any(item.value.endswith(":") for item in result.items)


async def test_a_value_with_a_space_is_quoted_but_labelled_plainly() -> None:
    result, _ = await suggest("restriction:full", restriction=["Full Lamp"])
    assert [item.value for item in result.items] == ['"Full Lamp"']
    assert [item.label for item in result.items] == ["Full Lamp"]


async def test_an_open_quote_is_preserved_when_completing() -> None:
    result, _ = await suggest('creator:"Nu', creator=["Nuclear"])
    assert [item.value for item in result.items] == ['"Nuclear"']


async def test_database_order_is_preserved_rather_than_re_ranked() -> None:
    result, _ = await suggest("version:b", version=["bob", "Bea", "bill"])
    assert [item.label for item in result.items] == ["bob", "Bea", "bill"]


async def test_creator_profiles_come_before_credited_names_and_insert_the_id() -> None:
    result, _ = await suggest("creator:b", creator=["bob", "Bea"])

    assert [(item.value, item.kind) for item in result.items] == [
        ("9d10eb8e-3bd0-44d6-a207-a6a4dc564e58", "creator_profile"),
        ("bob", "creator"),
        ("Bea", "creator"),
    ]


async def test_an_explicit_cursor_completes_mid_query() -> None:
    result, filters = await suggest("restriction:sea AND width:3", cursor=15, restriction=["Seamless"])
    assert filters.calls == [("restriction", "sea", 10)]
    assert result.replacement is not None
    assert (result.replacement.start, result.replacement.end) == (12, 15)
