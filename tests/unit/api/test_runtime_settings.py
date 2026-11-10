"""Shared runtime-settings transport validation and account attribution."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from squid.api.security import ANONYMOUS, UNBOUNDED, Caller
from squid.api.v1.schemas.settings import RuntimeSettingsDetail, RuntimeSettingsPatch
from squid.api.v1.settings import update_runtime_settings
from squid.core.errors import AuthenticationError
from squid.settings.domain.runtime import RuntimeSettings
from tests.unit.api.fakes import TEST_CONFIG, TEST_SYNERGY_SECRET, build_app


@pytest.mark.parametrize("kind", ["account", "cli", "minecraft_player", "service"])
async def test_every_account_credential_audits_the_internal_account(kind: str) -> None:
    settings = SimpleNamespace(update_runtime=AsyncMock(return_value=RuntimeSettings()))
    services = cast(Any, SimpleNamespace(settings=settings))
    caller = Caller(kind=cast(Any, kind), subject="test", nodes=UNBOUNDED, account_id=7)
    await update_runtime_settings(RuntimeSettingsPatch(welcome_forward_chance=0.5), services, caller)
    settings.update_runtime.assert_awaited_once_with({"welcome_forward_chance": 0.5}, actor_account_id=7)


async def test_unowned_credentials_cannot_create_an_audit_entry() -> None:
    settings = SimpleNamespace(update_runtime=AsyncMock())
    with pytest.raises(AuthenticationError):
        await update_runtime_settings(
            RuntimeSettingsPatch(welcome_enabled=False),
            cast(Any, SimpleNamespace(settings=settings)),
            ANONYMOUS,
        )
    settings.update_runtime.assert_not_awaited()


@pytest.mark.parametrize("body", [{}, {"welcome_enabled": None}, {"welcome_forward_chance": 2}, {"typo": 1}])
def test_bad_patches_are_rejected(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RuntimeSettingsPatch.model_validate(body)


def test_discord_ids_cross_the_wire_without_javascript_rounding() -> None:
    settings = RuntimeSettings(welcome_channel_id=1356094722531393681)
    response = RuntimeSettingsDetail.from_domain(settings)
    assert response.welcome_channel_id == "1356094722531393681"
    patch = RuntimeSettingsPatch(welcome_channel_id=response.welcome_channel_id)
    assert RuntimeSettings.model_validate(settings.model_dump() | patch.model_dump(exclude_unset=True)) == settings


def test_http_read_requires_the_global_view_permission() -> None:

    config = TEST_CONFIG.model_copy(
        update={"api": TEST_CONFIG.api.model_copy(update={"secret_nodes": ["settings.runtime.view"]})},
    )
    app, _ = build_app(config=config)
    with TestClient(app) as client:
        assert client.get("/v1/settings/runtime").status_code == 401
        response = client.get("/v1/settings/runtime", headers={"Authorization": TEST_SYNERGY_SECRET})
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        denied = client.patch(
            "/v1/settings/runtime", json={"welcome_enabled": False}, headers={"Authorization": TEST_SYNERGY_SECRET}
        )
        assert denied.status_code == 403
