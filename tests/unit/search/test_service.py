"""Search application service tests."""

from collections.abc import Sequence
from functools import partial
from types import TracebackType
from typing import Self, override
from uuid import UUID

import pytest

from squid.core.actors import ActingContext, AnonymousActor
from squid.core.builds.application.queries import (
    PublicBuildCreator,
    PublicBuildDimensions,
    PublicBuildStatus,
    PublicBuildSummary,
)
from squid.core.builds.domain import BuildCategory
from squid.core.errors import ValidationError
from squid.core.pagination import PageAnchor
from squid.core.records.application.metrics import RecordHolderSummary
from squid.core.search.application import SearchQueryParser, SearchService, SearchSlice
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.core.search.application.ports import QueryEmbedding
from squid.core.search.application.results import BuildSearchHit, RecordSearchHit
from squid.core.search.domain import (
    BuildQuery,
    FieldDefinition,
    SearchMode,
    SearchQuery,
    SearchRequest,
    SearchScope,
    SearchSort,
)
from tests.support.public_builds import NoPublicBuilds
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
        hit = BuildSearchHit("1", "Door", "approved", score=0.5)
        return SearchSlice((hit,), self.total, warnings=("degraded",))

    async def count(self, request: SearchRequest, query: SearchQuery) -> int:
        assert self.active_scopes == 1
        return len(request.scope.value)

    async def suggest(self, query: SearchQuery, *, limit: int) -> tuple[str, ...]:
        return self.suggestions[:limit]


class RecordHitBackend(FakeSearchBackend):
    @override
    async def search(
        self,
        request: SearchRequest,
        query: SearchQuery,
        *,
        sort_field: FieldDefinition | None = None,
        query_embedding: QueryEmbedding | None = None,
    ) -> SearchSlice:
        indexed = (RecordHolderSummary(7, "Indexed title", (PublicBuildCreator("Alice", None),), None),)
        record = RecordSearchHit(
            "record:1",
            "Smallest door",
            None,
            7,
            "Door",
            "smallest",
            "all_time",
            holder_build_ids=(7, 9),
            holders=indexed,
        )
        return SearchSlice((record, BuildSearchHit("7", "Door", "approved", records=(record,))), 2)


class OnePublicBuild:
    def __init__(self) -> None:
        self.requested: list[tuple[int, ...]] = []

    async def get_public_summaries(
        self, build_ids: Sequence[int], *, context: ActingContext
    ) -> Sequence[PublicBuildSummary]:
        self.requested.append(tuple(build_ids))
        return (
            PublicBuildSummary(
                id=7,
                revision=1,
                title="Current title",
                display_name=None,
                status=PublicBuildStatus.APPROVED,
                category=BuildCategory.DOOR,
                dimensions=PublicBuildDimensions(None, None, None),
                creators=(PublicBuildCreator("Alice", UUID(int=1)),),
                tags=(),
                preview=None,
                version_spec=None,
                versions=(),
                opening_time=None,
                closing_time=None,
                created_at=None,
                updated_at=None,
            ),
        )


async def test_record_holders_are_current_public_summaries_loaded_once() -> None:
    builds = OnePublicBuild()
    service = SearchService(uow_factory=partial(SearchUnitOfWork, RecordHitBackend()), builds=builds)

    page = await service.search(SearchRequest("door"), context=ActingContext.direct(AnonymousActor()))

    record, build = page.hits
    assert isinstance(record, RecordSearchHit)
    assert isinstance(build, BuildSearchHit)
    # Build 9 is not public, so it has no card.
    expected = (RecordHolderSummary(7, "Current title", (PublicBuildCreator("Alice", UUID(int=1)),), None),)
    assert record.holders == build.records[0].holders == expected
    assert builds.requested == [(7, 9)]


async def test_service_parses_query_and_reports_the_backend_total() -> None:
    backend = FakeSearchBackend()
    service = SearchService(uow_factory=partial(SearchUnitOfWork, backend), builds=NoPublicBuilds())

    page = await service.search(
        SearchRequest("status:approved door", scope=SearchScope.BUILDS, page_size=5),
        context=ActingContext.direct(AnonymousActor()),
    )

    assert page.hits[0].resource_kind == "build"
    assert page.total == 25
    assert page.warnings == ("degraded",)
    assert backend.calls[0][1].normalized == "status:approved AND door"
    assert backend.calls[0][2] is None


async def test_service_resolves_the_sort_field_before_searching() -> None:
    backend = FakeSearchBackend()
    service = SearchService(uow_factory=partial(SearchUnitOfWork, backend), builds=NoPublicBuilds())

    await service.search(
        SearchRequest("door", sort=SearchSort("width")), context=ActingContext.direct(AnonymousActor())
    )

    assert backend.calls[0][2] == DEFAULT_FIELD_REGISTRY.resolve("width")


async def test_service_uses_the_scoped_tree_without_reparsing_display_text() -> None:
    backend = FakeSearchBackend()
    service = SearchService(uow_factory=partial(SearchUnitOfWork, backend), builds=NoPublicBuilds())
    parsed = SearchQueryParser(scope=SearchScope.BUILDS).parse("door width:5")
    assert isinstance(parsed, BuildQuery)

    await service.search(
        SearchRequest("as typed", scope=SearchScope.BUILDS, parsed_query=parsed),
        context=ActingContext.direct(AnonymousActor()),
    )

    assert backend.calls[0][1] is parsed
    with pytest.raises(ValidationError, match="scope"):
        SearchRequest("as typed", scope=SearchScope.RECORDS, parsed_query=parsed)


@pytest.mark.parametrize("field", ["orientation", "nonexistent"])
async def test_service_rejects_unsortable_and_unknown_sort_fields(field: str) -> None:
    backend = FakeSearchBackend()
    service = SearchService(uow_factory=partial(SearchUnitOfWork, backend), builds=NoPublicBuilds())

    with pytest.raises(ValidationError, match="cannot be sorted"):
        await service.search(
            SearchRequest("door", sort=SearchSort(field)), context=ActingContext.direct(AnonymousActor())
        )

    assert backend.calls == []


async def test_service_addresses_neighbouring_pages_by_offset() -> None:
    service = SearchService(uow_factory=partial(SearchUnitOfWork, FakeSearchBackend()), builds=NoPublicBuilds())

    page = await service.search(
        SearchRequest("door", page_size=5, offset=10), context=ActingContext.direct(AnonymousActor())
    )

    assert (page.prev, page.next) == (PageAnchor(offset=5), PageAnchor(offset=15))


async def test_service_stops_paging_at_the_end_of_the_results() -> None:
    service = SearchService(uow_factory=partial(SearchUnitOfWork, FakeSearchBackend(total=12)), builds=NoPublicBuilds())

    first = await service.search(SearchRequest("door", page_size=5), context=ActingContext.direct(AnonymousActor()))
    last = await service.search(
        SearchRequest("door", page_size=5, offset=10), context=ActingContext.direct(AnonymousActor())
    )

    assert (first.prev, first.next) == (None, PageAnchor(offset=5))
    assert (last.prev, last.next) == (PageAnchor(offset=5), None)


async def test_service_delegates_suggestions_and_bounds_limit() -> None:
    service = SearchService(uow_factory=partial(SearchUnitOfWork, FakeSearchBackend()), builds=NoPublicBuilds())

    assert await service.suggest("dor", limit=1, context=ActingContext.direct(AnonymousActor())) == ("door",)

    with pytest.raises(ValidationError, match="between 1 and 25"):
        await service.suggest("dor", limit=0, context=ActingContext.direct(AnonymousActor()))


class StaticFields:
    async def registry(self) -> FieldRegistry:
        return DEFAULT_FIELD_REGISTRY


class SearchUnitOfWork(FakeUnitOfWork[FakeSearchBackend]):
    """Track scope entry on the shared backend; reads stay observable without a commit."""

    def __init__(self, backend: FakeSearchBackend) -> None:
        super().__init__(CommittedState(backend, clone=lambda value: value))

    @override
    async def __aenter__(self) -> Self:
        self.state.active_scopes += 1
        return self

    @override
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

    @override
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
    service = SearchService(
        uow_factory=partial(SearchUnitOfWork, backend), builds=NoPublicBuilds(), embedding_model=model
    )

    await service.search(
        SearchRequest("door", mode=SearchMode.SEMANTIC), context=ActingContext.direct(AnonymousActor())
    )

    assert model.inputs == ["door"]
    assert backend.embeddings == [QueryEmbedding([1.0, 0.0], "test-model")]
    assert backend.active_scopes == 0


@pytest.mark.parametrize(
    "search_request",
    [
        SearchRequest("door"),
        SearchRequest("criterion:fastest", mode=SearchMode.SEMANTIC),
        SearchRequest("door", mode=SearchMode.SEMANTIC, sort=SearchSort("width")),
    ],
)
async def test_search_skips_embedding_when_semantic_ranking_is_unused(search_request: SearchRequest) -> None:
    backend = FakeSearchBackend()
    model = FakeEmbeddingModel(backend)
    service = SearchService(
        uow_factory=partial(SearchUnitOfWork, backend), builds=NoPublicBuilds(), embedding_model=model
    )

    await service.search(search_request, context=ActingContext.direct(AnonymousActor()))

    assert model.inputs == []
    assert backend.embeddings == [None]


@pytest.mark.parametrize("raises", [False, True])
async def test_missing_or_failed_embedding_still_runs_the_result_query(raises: bool) -> None:
    backend = FakeSearchBackend()
    model = FakeEmbeddingModel(backend, unavailable=True, error=raises)
    service = SearchService(
        uow_factory=partial(SearchUnitOfWork, backend), builds=NoPublicBuilds(), embedding_model=model
    )

    page = await service.search(
        SearchRequest("door", mode=SearchMode.SEMANTIC), context=ActingContext.direct(AnonymousActor())
    )

    assert page.hits
    assert backend.embeddings == [None]
    assert backend.active_scopes == 0


async def test_service_lists_the_parsed_clauses_with_each_page() -> None:
    service = SearchService(uow_factory=partial(SearchUnitOfWork, FakeSearchBackend()), builds=NoPublicBuilds())

    page = await service.search(
        SearchRequest("door width<=3", scope=SearchScope.BUILDS), context=ActingContext.direct(AnonymousActor())
    )

    assert [(clause.text, clause.without) for clause in page.clauses] == [("door", "width<=3"), ("width<=3", "door")]


async def test_service_counts_each_scope_and_skips_scopes_that_cannot_run_the_query() -> None:
    service = SearchService(uow_factory=partial(SearchUnitOfWork, FakeSearchBackend()), builds=NoPublicBuilds())

    counts = await service.counts(SearchRequest("criterion:fastest"), context=ActingContext.direct(AnonymousActor()))

    assert counts == {
        SearchScope.RECORDS: len("records"),
        SearchScope.BUILDS: None,
        SearchScope.METADATA: len("metadata"),
        SearchScope.ALL: len("all"),
    }
