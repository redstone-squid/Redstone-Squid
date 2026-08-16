"""Search request and discriminated result model tests."""

import pytest

from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET
from squid.search.domain import (
    BuildSearchHit,
    MetadataSearchHit,
    RecordSearchHit,
    SearchPage,
    SearchRequest,
    SearchSort,
    SortDirection,
)


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


class TestSortParsing:
    """One sort syntax, parsed in the domain, so the API and the bot agree."""

    def test_a_bare_field_ascends(self) -> None:
        assert SearchSort.parse("submission_time") == SearchSort("submission_time", SortDirection.ASCENDING)

    def test_a_leading_minus_descends(self) -> None:
        assert SearchSort.parse("-submission_time") == SearchSort("submission_time", SortDirection.DESCENDING)

    def test_no_sort_means_the_backend_default(self) -> None:
        assert SearchSort.parse(None) is None

    def test_a_bare_minus_is_a_bad_request(self) -> None:
        with pytest.raises(ValidationError) as raised:
            SearchSort.parse("-")

        assert raised.value.code is ErrorCode.INVALID_QUERY
