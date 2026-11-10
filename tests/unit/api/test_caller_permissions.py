"""Credential scopes and account authority must both permit an HTTP operation."""

from typing import Literal

import pytest
from pytest_mock import MockerFixture

from squid.api.security import ANONYMOUS, UNBOUNDED, Caller
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import Subject
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_VIEW_PENDING


@pytest.mark.parametrize("account_id", [None, 7])
async def test_a_credential_without_the_node_cannot_use_account_authority(
    mocker: MockerFixture, account_id: int | None
) -> None:
    permissions = mocker.create_autospec(PermissionService, instance=True)
    caller = Caller(kind="service", subject="key", account_id=account_id)

    assert not await caller.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING)
    permissions.allows.assert_not_called()


async def test_an_ownerless_service_credential_uses_its_own_authority(mocker: MockerFixture) -> None:
    permissions = mocker.create_autospec(PermissionService, instance=True)
    caller = Caller(kind="service", subject="bootstrap", nodes=UNBOUNDED)

    assert await caller.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING)
    permissions.allows.assert_not_called()


@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("kind", ["service", "account", "cli", "minecraft_player"])
async def test_owned_credentials_consult_current_account_authority_without_a_guild(
    mocker: MockerFixture,
    allowed: bool,
    kind: Literal["service", "account", "cli", "minecraft_player"],
) -> None:
    permissions = mocker.create_autospec(PermissionService, instance=True)
    permissions.allows.return_value = allowed
    caller = Caller(kind=kind, subject="owned", account_id=7, nodes=UNBOUNDED)

    assert await caller.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING) is allowed
    permissions.allows.assert_awaited_once_with(Subject(account_id=7, guild_id=None), BUILD_SUBMISSION_VIEW_PENDING)


@pytest.mark.parametrize("allowed", [False, True])
async def test_anonymous_authority_is_resolved_by_the_permission_service(mocker: MockerFixture, allowed: bool) -> None:
    permissions = mocker.create_autospec(PermissionService, instance=True)
    permissions.allows.return_value = allowed

    assert await ANONYMOUS.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING) is allowed
    permissions.allows.assert_awaited_once_with(Subject(account_id=None, guild_id=None), BUILD_SUBMISSION_VIEW_PENDING)
