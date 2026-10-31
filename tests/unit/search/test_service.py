"""Search application service tests."""

import pytest

from squid.core.errors import ValidationError
from squid.core.pagination import PageAnchor
from squid.search.application import SearchQueryParser, SearchService, SearchSlice
from squid.search.domain import BuildSearchHit, SearchQuery, SearchRequest, SearchScope


class FakeSearchBackend:
    def __init__(self, total: int = 25) -> None:
        self.calls: list[tuple[SearchRequest, SearchQuery, int]] = []
        self.suggestions: tuple[str, ...] = ("door",)
        self.total = total

    async def search(self, request: SearchRequest, query: SearchQuery, *, offset: int) -> SearchSlice:
        self.calls.append((request, query, offset))
        hit = BuildSearchHit("1", "Door", "confirmed", score=0.5)
        return SearchSlice((hit,), self.total, warnings=("degraded",))

    async def suggest(self, query: SearchQuery, *, limit: int) -> tuple[str, ...]:
        return self.suggestions[:limit]


async def test_service_parses_query_and_reports_the_backend_total() -> None:
    backend = FakeSearchBackend()
    service = SearchService(backend, SearchQueryParser())

    page = await service.search(SearchRequest("status:confirmed door", scope=SearchScope.BUILDS, page_size=5))

    assert page.hits[0].resource_kind == "build"
    assert page.total == 25
    assert page.warnings == ("degraded",)
    assert backend.calls[0][1].normalized == "status:confirmed AND door"
    assert backend.calls[0][2] == 0


async def test_service_addresses_neighbouring_pages_by_offset() -> None:
    service = SearchService(FakeSearchBackend(), SearchQueryParser())

    page = await service.search(SearchRequest("door", page_size=5, offset=10))

    assert (page.prev, page.next) == (PageAnchor(offset=5), PageAnchor(offset=15))


async def test_service_stops_paging_at_the_end_of_the_results() -> None:
    service = SearchService(FakeSearchBackend(total=12), SearchQueryParser())

    first = await service.search(SearchRequest("door", page_size=5))
    last = await service.search(SearchRequest("door", page_size=5, offset=10))

    assert (first.prev, first.next) == (None, PageAnchor(offset=5))
    assert (last.prev, last.next) == (PageAnchor(offset=5), None)


async def test_service_delegates_suggestions_and_bounds_limit() -> None:
    service = SearchService(FakeSearchBackend(), SearchQueryParser())

    assert await service.suggest("dor", limit=1) == ("door",)

    with pytest.raises(ValidationError, match="between 1 and 25"):
        await service.suggest("dor", limit=0)
