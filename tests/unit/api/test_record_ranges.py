"""Published range reads and permission-gated registration controls."""

from unittest.mock import create_autospec

import pytest
from litestar.testing import TestClient

from squid.api.security import ANONYMOUS, Caller
from squid.core.idempotency import IdempotencyService
from squid.core.records.application.expansion_regions import DoorRangeQuery, analyze_door_widths
from squid.core.records.application.published_points import (
    PublishedDoorPoint,
)
from squid.core.records.application.range_publication import RangePublicationService
from squid.core.records.application.range_snapshots import RangePublication
from tests.unit.api.fakes import TEST_CONFIG, TEST_SYNERGY_SECRET, build_app
from tests.unit.api.test_idempotency import MemoryIdempotencyRepository, MemoryIdempotencyUnitOfWork
from tests.unit.records.application.test_expansion_regions import family
from tests.unit.records.application.test_published_points import publication

CONFIG = TEST_CONFIG.model_copy(
    update={"api": TEST_CONFIG.api.model_copy(update={"secret_nodes": ["record.entry.rebuild"]})}
)
AUTH = {"Authorization": TEST_SYNERGY_SECRET}
PUBLISHER = Caller(kind="service", subject="legacy-bootstrap", nodes=CONFIG.api.secret_patterns).context()
BASE = "/v1/records/ranges/door"


def test_registration_and_refresh_replay_without_repeating_queue_writes() -> None:
    publications = create_autospec(RangePublicationService, instance=True)
    publications.register.return_value = 7
    publications.request_refresh.return_value = True
    repository = MemoryIdempotencyRepository()
    idempotency = IdempotencyService(uow_factory=lambda: MemoryIdempotencyUnitOfWork(repository))
    app, _ = build_app(config=CONFIG, range_publication=publications, idempotency=idempotency)
    with TestClient(app) as client:
        registration_headers = AUTH | {"Idempotency-Key": "register-range"}
        first = client.post(BASE, json={"height": 3}, headers=registration_headers)
        replay = client.post(BASE, json={"height": 3}, headers=registration_headers)
        assert first.status_code == replay.status_code == 200
        assert first.json() == replay.json() == {"query_id": 7}
        publications.register.assert_awaited_once_with(DoorRangeQuery(height=3), context=PUBLISHER)
        conflict = client.post(BASE, json={"height": 4}, headers=registration_headers)
        assert conflict.status_code == 409
        refresh_headers = AUTH | {"Idempotency-Key": "refresh-range"}
        first = client.post(f"{BASE}/7/refresh", headers=refresh_headers)
        replay = client.post(f"{BASE}/7/refresh", headers=refresh_headers)
        assert first.status_code == replay.status_code == 202
        assert first.json() == replay.json() == {"query_id": 7}
        publications.request_refresh.assert_awaited_once_with(7, context=PUBLISHER)
        publications.publish.assert_not_called()


@pytest.mark.parametrize("path", [BASE, f"{BASE}/7/refresh"])
def test_registration_controls_require_record_rebuild_permission(path: str) -> None:
    publications = create_autospec(RangePublicationService, instance=True)
    read_only = TEST_CONFIG.model_copy(
        update={"api": TEST_CONFIG.api.model_copy(update={"secret_nodes": ["record.entry.inspect"]})}
    )
    app, _ = build_app(config=read_only, range_publication=publications)
    with TestClient(app) as client:
        assert client.post(path, json={"height": 3}).status_code == 401
        assert client.post(path, json={"height": 3}, headers=AUTH).status_code == 403
    publications.register.assert_not_called()
    publications.request_refresh.assert_not_called()


def test_invalid_queries_and_unknown_refresh_are_rejected() -> None:
    publications = create_autospec(RangePublicationService, instance=True)
    publications.request_refresh.return_value = False
    app, _ = build_app(config=CONFIG, range_publication=publications)
    with TestClient(app) as client:
        for body in ({"height": True}, {"height": 3, "axis": "height"}, {"height": 3, "minimum": 8, "maximum": 5}):
            response = client.post(BASE, json=body, headers=AUTH)
            assert response.status_code == 422
        assert client.post(f"{BASE}/99/refresh", headers=AUTH).status_code == 404
    publications.register.assert_not_called()
    publications.request_refresh.assert_awaited_once_with(99, context=PUBLISHER)


def test_published_point_is_public_and_retains_saved_origin() -> None:
    publications = create_autospec(RangePublicationService, instance=True)
    state = publication(analyze_door_widths((family(),), height=3))
    publications.get_point.return_value = PublishedDoorPoint.from_publication(state, 9999)
    app, _ = build_app(range_publication=publications)
    with TestClient(app) as client:
        response = client.get(f"{BASE}/7/at/9999")
        assert response.status_code == 200
        body = response.json()
        assert body["opening"] == [9999, 3, 1]
        assert body["status"] == "published"
        assert body["region"]["resolution"]["holder_ids"] == [1]
        assert body["publication"]["id"] == 11
        assert body["publication"]["snapshot_hash"] == "snapshot-one"
        assert "result" not in body["publication"]
        publications.get_point.assert_awaited_once_with(7, 9999, context=ANONYMOUS.context())
        publications.get_point.reset_mock()
        for width in (0, 2**31):
            assert client.get(f"{BASE}/7/at/{width}").status_code == 422
        publications.get_point.assert_not_called()
        publications.get_point.return_value = None
        assert client.get(f"{BASE}/99/at/6").status_code == 404
    publications.publish.assert_not_called()
    publications.register.assert_not_called()


@pytest.mark.parametrize(("width", "expected"), [(6, "not_published"), (9, "outside_query")])
def test_unavailable_point_is_not_misrepresented_as_no_candidate(width: int, expected: str) -> None:
    publications = create_autospec(RangePublicationService, instance=True)
    state = RangePublication(7, DoorRangeQuery(height=3, minimum=5, maximum=8), None, None)
    publications.get_point.return_value = PublishedDoorPoint.from_publication(state, width)
    app, _ = build_app(range_publication=publications)
    with TestClient(app) as client:
        response = client.get(f"{BASE}/7/at/{width}")
    assert response.status_code == 200
    assert response.json()["status"] == expected
    assert response.json()["region"] is None
