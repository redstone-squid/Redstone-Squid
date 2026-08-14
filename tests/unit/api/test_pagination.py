"""REST pagination parameters: what they accept, refuse, and serialize to."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from squid.api.pagination import Page, PageAnchor, parse_page_sort, render_page, resolve_selector
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


def test_identifier_anchors_are_refused_for_an_order_they_do_not_address() -> None:
    with pytest.raises(ValidationError, match="require ordering by id"):
        resolve_selector(offset=None, after_id=9, keyset_allowed=False)


def test_an_unaddressed_request_resolves_to_the_first_page() -> None:
    assert resolve_selector(offset=None) == PageSelector()
    assert resolve_selector(offset=None, before_id=9) == PageSelector(before_id=9)


def test_sort_defaults_and_direction_come_from_the_parameter() -> None:
    allowed = frozenset({"id", "submission_time"})

    assert parse_page_sort(None, allowed=allowed, default="-id") == ("id", True)
    assert parse_page_sort("submission_time", allowed=allowed, default="-id") == ("submission_time", False)
    assert parse_page_sort("-submission_time", allowed=allowed, default="-id") == ("submission_time", True)


def test_only_allowlisted_sort_fields_are_accepted() -> None:
    with pytest.raises(ValidationError) as error:
        parse_page_sort("title", allowed=frozenset({"id"}), default="-id")

    assert error.value.code is ErrorCode.INVALID_QUERY


def test_rendering_carries_the_totals_and_anchors_through() -> None:
    page = render_page(
        ResultPage(items=(1, 2), total=7, next=ResultPageAnchor(after_id=2), prev=ResultPageAnchor(offset=0)),
        str,
    )

    assert page == Page[str](items=["1", "2"], total=7, next=PageAnchor(after_id=2), prev=PageAnchor(offset=0))


def test_an_offset_past_the_clamp_is_rejected_before_it_reaches_the_database(
    app_factory: tuple[FastAPI, MockDatabaseManager],
) -> None:
    app, _database = app_factory

    with TestClient(app) as client:
        response = client.get("/v1/versions", params={"offset": MAX_PAGE_OFFSET + 1})

    assert response.status_code == 422
