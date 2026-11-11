"""Shared runtime-settings transport validation and account attribution."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from squid.api.revisions import InvalidRevisionError, RevisionRequiredError, expected_revision
from squid.api.security import ANONYMOUS, UNBOUNDED, Caller
from squid.api.v1.schemas.settings import RuntimeSettingsDetail, RuntimeSettingsPatch
from squid.api.v1.settings import update_runtime_settings
from squid.core.errors import AuthenticationError
from squid.edits.domain import EditValues, Revision
from squid.settings.application.edits import RUNTIME_TARGET
from squid.settings.domain.runtime import RuntimeSettings
from tests.unit.api.fakes import TEST_CONFIG, TEST_SYNERGY_SECRET, build_app


@pytest.mark.parametrize("kind", ["account", "cli", "minecraft_player", "service"])
async def test_every_account_credential_audits_the_internal_account(kind: str) -> None:
    edits = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                receipt=SimpleNamespace(
                    after=EditValues.of(RuntimeSettings(welcome_forward_chance=0.5).model_dump(mode="json"))
                ),
                outcome=SimpleNamespace(revisions=(Revision(RUNTIME_TARGET, 2),)),
            )
        )
    )
    services = cast(Any, SimpleNamespace(edits=edits))
    caller = Caller(kind=cast(Any, kind), subject="test", nodes=UNBOUNDED, account_id=7)
    response = Response()
    await update_runtime_settings(
        RuntimeSettingsPatch(welcome_forward_chance=0.5), services, caller, response, uuid4(), '"runtime-settings-1-r1"'
    )
    command, actor = edits.execute.call_args.args
    assert command.values.unpack() == {"welcome_forward_chance": 0.5}
    assert actor.subject.account_id == 7
    assert response.headers["ETag"] == '"runtime-settings-1-r2"'


async def test_unowned_credentials_cannot_create_an_audit_entry() -> None:
    settings = SimpleNamespace(update_runtime=AsyncMock())
    with pytest.raises(AuthenticationError):
        await update_runtime_settings(
            RuntimeSettingsPatch(welcome_enabled=False),
            cast(Any, SimpleNamespace(settings=settings)),
            ANONYMOUS,
            Response(),
            uuid4(),
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
        update={
            "api": type(TEST_CONFIG.api).model_validate(
                TEST_CONFIG.api.model_dump() | {"secret_nodes": ["settings.runtime.view"]}
            )
        },
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


@pytest.mark.parametrize(
    "value",
    ["*", 'W/"runtime-settings-1-r1"', '"runtime-settings-2-r1"', '"runtime-settings-1-r1", "runtime-settings-1-r2"'],
)
def test_revision_preconditions_reject_unread_or_foreign_versions(value: str) -> None:
    with pytest.raises(InvalidRevisionError):
        expected_revision(RUNTIME_TARGET, value)


async def test_runtime_update_requires_the_version_read_by_the_caller() -> None:
    edits = SimpleNamespace(execute=AsyncMock())
    caller = Caller(kind="account", subject="test", nodes=UNBOUNDED, account_id=7)
    with pytest.raises(RevisionRequiredError):
        await update_runtime_settings(
            RuntimeSettingsPatch(welcome_enabled=False),
            cast(Any, SimpleNamespace(edits=edits)),
            caller,
            Response(),
            uuid4(),
            None,
        )
    edits.execute.assert_not_awaited()
