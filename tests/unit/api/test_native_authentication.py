"""Native authentication establishes identity before quotas, guards, and reservations."""

import pytest
from litestar import Request, get
from litestar.datastructures import State
from litestar.testing import TestClient
from pytest_mock import MockerFixture

from squid.api.security import Caller, requires
from squid.idempotency import IdempotencyService
from tests.unit.api.fakes import TEST_CONFIG, TEST_SYNERGY_SECRET, TEST_UUID, build_app


def test_native_request_identity_is_available_to_guards_and_handlers() -> None:
    @get("/v1/identity-probe", guards=[requires("account.verify.relay")])
    async def probe(request: Request[Caller, str, State]) -> dict[str, str]:
        return {"subject": request.user.subject, "credential": request.auth}

    app, _ = build_app()
    app.register(probe)
    with TestClient(app) as client:
        response = client.get("/v1/identity-probe", headers={"Authorization": TEST_SYNERGY_SECRET})

    assert response.status_code == 200
    assert response.json() == {"subject": "legacy-bootstrap", "credential": "service"}


@pytest.mark.parametrize("authenticated", [False, True])
def test_guard_denial_does_not_reserve_an_idempotency_key(mocker: MockerFixture, authenticated: bool) -> None:
    idempotency = mocker.create_autospec(IdempotencyService, instance=True)
    config = TEST_CONFIG.model_copy(
        update={
            "api": type(TEST_CONFIG.api).model_validate(
                TEST_CONFIG.api.model_dump() | {"secret_nodes": ["settings.runtime.view"]}
            )
        }
    )
    app, _ = build_app(config=config, idempotency=idempotency)
    headers = {"Idempotency-Key": "denied-verification"}
    if authenticated:
        headers["Authorization"] = TEST_SYNERGY_SECRET
    with TestClient(app) as client:
        response = client.post("/v1/verify", json={"uuid": str(TEST_UUID)}, headers=headers)

    assert response.status_code == (403 if authenticated else 401)
    assert '"ip"' in response.headers["RateLimit-Policy"]
    idempotency.reserve.assert_not_awaited()
