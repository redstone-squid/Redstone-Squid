"""REST pagination parameters: what they accept, refuse, and serialize to."""

import pytest
from litestar import Litestar
from litestar.testing import TestClient

from squid.api.pagination import Page, PageAnchor, resolve_selector
from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET, PageSelector
from squid.core.pagination import Page as ResultPage
from squid.core.pagination import PageAnchor as ResultPageAnchor
from tests.unit.api.fakes import MockDatabaseManager


def test_a_request_may_address_its_page_only_one_way() -> None:
    with pytest.raises(ValidationError) as error:
        resolve_selector(offset=20, after_id=9)

    assert error.value.code is ErrorCode.INVALID_QUERY
    assert "offset, after_id cannot be combined" in str(error.value)


def test_an_unaddressed_request_resolves_to_the_first_page() -> None:
    assert resolve_selector(offset=None) == PageSelector()
    assert resolve_selector(offset=None, before_id=9) == PageSelector(before_id=9)


def test_rendering_carries_the_totals_and_anchors_through() -> None:
    page = Page[str].from_domain(
        ResultPage(items=(1, 2), total=7, next=ResultPageAnchor(after_id=2), prev=ResultPageAnchor(offset=0)),
        str,
    )

    assert page == Page[str](items=["1", "2"], total=7, next=PageAnchor(after_id=2), prev=PageAnchor(offset=0))


def test_an_offset_past_the_clamp_is_rejected_before_it_reaches_the_database(
    app_factory: tuple[Litestar, MockDatabaseManager],
) -> None:
    app, _database = app_factory

    with TestClient(app) as client:
        response = client.get("/v1/versions", params={"offset": MAX_PAGE_OFFSET + 1})

    assert response.status_code == 422
