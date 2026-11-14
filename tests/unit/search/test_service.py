"""Search application service tests."""

from functools import partial
from types import TracebackType
from typing import Self

import pytest

from squid.core.errors import ValidationError
from squid.core.pagination import PageAnchor
from squid.search.application import SearchService, SearchSlice
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.search.application.ports import QueryEmbedding
from squid.search.domain import (
    BuildSearchHit,
    FieldDefinition,
    SearchMode,
    SearchQuery,
    SearchRequest,
    SearchScope,
    SearchSort,
)
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


class FakeSearchBackend:
    """Return one fixed hit with a configurable total while recording calls inside a UoW scope."""

    def __init__(self, total: int = 25) -> None:
        self.calls: list[tuple[SearchRequest, SearchQuery, FieldDefinition | None]] = []
        self.suggestions: tuple[str, ...] = ("door",)
        self.total = total
        self.active_scopes = 0
        self.embeddings: list[QueryEmbedding | None] = []

    async def search(
        self,
        request: SearchRequest,
        query: SearchQuery,
        *,
        sort_field: FieldDefinition | None = None,
        query_embedding: QueryEmbedding | None = None,
    ) -> SearchSlice:
        assert self.active_scopes == 1
        self.calls.append((request, query, sort_field))
        self.embeddings.append(query_embedding)
        hit = BuildSearchHit("1", "Door", "confirmed", score=0.5)
        return SearchSlice((hit,), self.total, warnings=("degraded",))

    async def suggest(self, query: SearchQuery, *, limit: int) -> tuple[str, ...]:
        return self.suggestions[:limit]


async def test_service_parses_query_and_reports_the_backend_total() -> None:
    backend = FakeSearchBackend()
    service = SearchService(uow_factory=partial(SearchUnitOfWork, backend))

    page = await service.search(SearchRequest("status:confirmed door", scope=SearchScope.BUILDS, page_size=5))

    assert page.hits[0].resource_kind == "build"
    assert page.total == 25
    assert page.warnings == ("degraded",)
    assert backend.calls[0][1].normalized == "status:confirmed AND door"
    assert backend.calls[0][2] is None


async def test_service_resolves_the_sort_field_before_searching() -> None:
    backend = FakeSearchBackend()
    service = SearchService(uow_factory=partial(SearchUnitOfWork, backend))

    await service.search(SearchRequest("door", sort=SearchSort("width")))

    assert backend.calls[0][2] == DEFAULT_FIELD_REGISTRY.resolve("width")


@pytest.mark.parametrize("field", ["volume", "nonexistent"])
async def test_service_rejects_unsortable_and_unknown_sort_fields(field: str) -> None:
    backend = FakeSearchBackend()
    service = SearchService(uow_factory=partial(SearchUnitOfWork, backend))

    with pytest.raises(ValidationError, match="cannot be sorted"):
        await service.search(SearchRequest("door", sort=SearchSort(field)))

    assert backend.calls == []


async def test_service_addresses_neighbouring_pages_by_offset() -> None:
    service = SearchService(uow_factory=partial(SearchUnitOfWork, FakeSearchBackend()))

    page = await service.search(SearchRequest("door", page_size=5, offset=10))

    assert (page.prev, page.next) == (PageAnchor(offset=5), PageAnchor(offset=15))


async def test_service_stops_paging_at_the_end_of_the_results() -> None:
    service = SearchService(uow_factory=partial(SearchUnitOfWork, FakeSearchBackend(total=12)))

    first = await service.search(SearchRequest("door", page_size=5))
    last = await service.search(SearchRequest("door", page_size=5, offset=10))

    assert (first.prev, first.next) == (None, PageAnchor(offset=5))
    assert (last.prev, last.next) == (PageAnchor(offset=5), None)


async def test_service_delegates_suggestions_and_bounds_limit() -> None:
    service = SearchService(uow_factory=partial(SearchUnitOfWork, FakeSearchBackend()))

    assert await service.suggest("dor", limit=1) == ("door",)

    with pytest.raises(ValidationError, match="between 1 and 25"):
        await service.suggest("dor", limit=0)


class StaticFields:
    async def registry(self) -> FieldRegistry:
        return DEFAULT_FIELD_REGISTRY


class SearchUnitOfWork(FakeUnitOfWork[FakeSearchBackend]):
    """Track scope entry on the shared backend; reads stay observable without a commit."""

    def __init__(self, backend: FakeSearchBackend) -> None:
        super().__init__(CommittedState(backend, clone=lambda value: value))

    async def __aenter__(self) -> Self:
        self.state.active_scopes += 1
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.state.active_scopes -= 1

    @property
    def search(self) -> FakeSearchBackend:
        return self.state

    @property
    def search_fields(self) -> StaticFields:
        return StaticFields()

    async def commit(self) -> None:
        raise AssertionError("Search must not commit.")


class FakeEmbeddingModel:
    model_name = "test-model"

    def __init__(self, backend: FakeSearchBackend, *, unavailable: bool = False, error: bool = False) -> None:
        self.backend = backend
        self.unavailable = unavailable
        self.error = error
        self.inputs: list[str] = []

    async def embed(self, text: str) -> list[float] | None:
        assert self.backend.active_scopes == 0, "Embedding requests must run outside database scopes."
        self.inputs.append(text)
        if self.error:
            raise RuntimeError("Embedding provider unavailable")
        return None if self.unavailable else [1.0, 0.0]


async def test_query_embedding_is_generated_outside_scopes_and_passed_to_search() -> None:
    backend = FakeSearchBackend()
    model = FakeEmbeddingModel(backend)
    service = SearchService(uow_factory=partial(SearchUnitOfWork, backend), embedding_model=model)

    await service.search(SearchRequest("door", mode=SearchMode.SEMANTIC))

    assert model.inputs == ["door"]
    assert backend.embeddings == [QueryEmbedding([1.0, 0.0], "test-model")]
    assert backend.active_scopes == 0


@pytest.mark.parametrize(
    "search_request",
    [
        SearchRequest("door"),
        SearchRequest("status:confirmed", mode=SearchMode.SEMANTIC),
        SearchRequest("door", mode=SearchMode.SEMANTIC, sort=SearchSort("width")),
    ],
)
async def test_search_skips_embedding_when_semantic_ranking_is_unused(search_request: SearchRequest) -> None:
    backend = FakeSearchBackend()
    model = FakeEmbeddingModel(backend)
    service = SearchService(uow_factory=partial(SearchUnitOfWork, backend), embedding_model=model)

    await service.search(search_request)

    assert model.inputs == []
    assert backend.embeddings == [None]


@pytest.mark.parametrize("raises", [False, True])
async def test_missing_or_failed_embedding_still_runs_the_result_query(raises: bool) -> None:
    backend = FakeSearchBackend()
    model = FakeEmbeddingModel(backend, unavailable=True, error=raises)
    service = SearchService(uow_factory=partial(SearchUnitOfWork, backend), embedding_model=model)

    page = await service.search(SearchRequest("door", mode=SearchMode.SEMANTIC))

    assert page.hits
    assert backend.embeddings == [None]
    assert backend.active_scopes == 0
