"""REST pagination parameters: what they accept, refuse, and serialize to."""

import pytest
from litestar import Litestar
from litestar.testing import TestClient

from squid.api.pagination import Page, PageAnchor, resolve_selector
from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET, PageCursor, PageSelector
from squid.core.pagination import Page as ResultPage
from squid.core.pagination import PageAnchor as ResultPageAnchor
from tests.unit.api.fakes import MockDatabaseManager


def test_a_request_may_address_its_page_only_one_way() -> None:
    with pytest.raises(ValidationError) as error:
        resolve_selector(offset=20, cursor=PageCursor("id", 9, 9).encode())

    assert error.value.code is ErrorCode.INVALID_QUERY


def test_an_unaddressed_request_resolves_to_the_first_page() -> None:
    cursor = PageCursor("id", 9, 9, backward=True)

    assert resolve_selector(offset=None) == PageSelector()
    assert resolve_selector(offset=None, cursor=cursor.encode()) == PageSelector(cursor=cursor)


def test_a_malformed_cursor_asks_the_client_to_start_over() -> None:
    with pytest.raises(ValidationError) as error:
        resolve_selector(offset=None, cursor="garbage")

    assert error.value.code is ErrorCode.INVALID_PAGE_CURSOR


def test_rendering_carries_the_totals_and_anchors_through() -> None:
    cursor = PageCursor("id", 2, 2)
    page = Page[str].from_domain(
        ResultPage(items=(1, 2), total=7, next=ResultPageAnchor(cursor=cursor), prev=ResultPageAnchor(offset=0)),
        str,
    )

    assert page == Page[str](
        items=["1", "2"], total=7, next=PageAnchor(cursor=cursor.encode()), prev=PageAnchor(offset=0)
    )


def test_an_offset_past_the_clamp_is_rejected_before_it_reaches_the_database(
    app_factory: tuple[Litestar, MockDatabaseManager],
) -> None:
    app, _database = app_factory

    with TestClient(app) as client:
        response = client.get("/v1/versions", params={"offset": MAX_PAGE_OFFSET + 1})

    assert response.status_code == 422
