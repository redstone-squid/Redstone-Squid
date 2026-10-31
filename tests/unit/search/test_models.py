"""Search request and discriminated result model tests."""

import pytest

from squid.core.errors import ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET
from squid.search.domain import BuildSearchHit, MetadataSearchHit, RecordSearchHit, SearchPage, SearchRequest


def test_search_page_preserves_discriminated_hits() -> None:
    page = SearchPage(
        hits=(
            RecordSearchHit("r1", "Fastest Door", None, 1, "Door", "fastest", "all-time"),
            BuildSearchHit("b1", "Door", "confirmed"),
            MetadataSearchHit("m1", "Seamless", "restriction"),
        ),
        total=3,
        next=None,
        prev=None,
    )

    assert [hit.resource_kind for hit in page.hits] == ["record", "build", "metadata"]


@pytest.mark.parametrize("page_size", [0, 51])
def test_search_request_bounds_page_size(page_size: int) -> None:
    with pytest.raises(ValidationError, match="between 1 and 50"):
        SearchRequest("door", page_size=page_size)


@pytest.mark.parametrize("offset", [-1, MAX_PAGE_OFFSET + 1])
def test_search_request_bounds_offset(offset: int) -> None:
    with pytest.raises(ValidationError, match=f"between 0 and {MAX_PAGE_OFFSET}"):
        SearchRequest("door", offset=offset)
