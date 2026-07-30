"""Search application service tests."""

from squid.search.application import CursorCodec, SearchQueryParser, SearchService, SearchSlice
from squid.search.domain import (
    BuildSearchHit,
    CursorPosition,
    SearchMode,
    SearchQuery,
    SearchRequest,
    SearchScope,
)


class FakeSearchBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[SearchRequest, SearchQuery, CursorPosition | None]] = []
        self.suggestions: tuple[str, ...] = ("door",)

    async def search(
        self,
        request: SearchRequest,
        query: SearchQuery,
        cursor: CursorPosition | None,
    ) -> SearchSlice:
        self.calls.append((request, query, cursor))
        hit = BuildSearchHit("1", "Door", "confirmed", score=0.5)
        position = CursorPosition(
            CursorCodec.request_hash(request),
            request.scope,
            request.mode,
            0.5,
            "build",
            "1",
        )
        return SearchSlice((hit,), has_more=True, last_position=position, warnings=("degraded",))

    async def suggest(self, query: SearchQuery, *, limit: int) -> tuple[str, ...]:
        return self.suggestions[:limit]


async def test_service_parses_query_and_encodes_backend_position() -> None:
    backend = FakeSearchBackend()
    codec = CursorCodec(b"a suitably long test secret")
    service = SearchService(backend, SearchQueryParser(), codec)
    request = SearchRequest("status:confirmed door", scope=SearchScope.BUILDS)

    page = await service.search(request)

    assert page.hits[0].resource_kind == "build"
    assert page.has_more
    assert page.warnings == ("degraded",)
    assert page.next_cursor is not None
    assert codec.decode(page.next_cursor, request=request).source_id == "1"
    assert backend.calls[0][1].normalized == "status:confirmed AND door"


async def test_service_validates_and_passes_existing_cursor() -> None:
    backend = FakeSearchBackend()
    codec = CursorCodec(b"a suitably long test secret")
    service = SearchService(backend, SearchQueryParser(), codec)
    original = SearchRequest("door", scope=SearchScope.ALL, mode=SearchMode.SEMANTIC)
    position = CursorPosition(codec.request_hash(original), original.scope, original.mode, 0.5, "build", "1")
    request = SearchRequest(
        original.query,
        scope=original.scope,
        mode=original.mode,
        cursor=codec.encode(position),
    )

    await service.search(request)

    assert backend.calls[0][2] == position


async def test_service_delegates_suggestions_and_bounds_limit() -> None:
    service = SearchService(FakeSearchBackend(), SearchQueryParser(), CursorCodec(b"a suitably long test secret"))

    assert await service.suggest("dor", limit=1) == ("door",)
