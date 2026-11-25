"""Credential scopes and account authority must both permit an HTTP operation."""

from typing import Literal
from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

from squid.api.security import ANONYMOUS, UNBOUNDED, Caller
from squid.core.actors import Actor
from squid.core.errors import AuthenticationError
from squid.core.permissions.application.ports import SubjectRecords
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain import Decision, Effect, Reason
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_VIEW_PENDING
from squid.core.permissions.errors import CredentialPermissionDeniedError, PermissionDeniedError
from squid.core.permissions.models import PermissionGrant


@pytest.mark.parametrize("account_id", [None, 7])
async def test_a_credential_without_the_node_cannot_use_account_authority(
    mocker: MockerFixture, account_id: int | None
) -> None:
    permissions = mocker.create_autospec(PermissionService, instance=True)
    caller = Caller(kind="service", subject="key", account_id=account_id)

    assert not await caller.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING)
    permissions.require.assert_not_called()


async def test_an_ownerless_service_credential_uses_its_own_authority(mocker: MockerFixture) -> None:
    permissions = mocker.create_autospec(PermissionService, instance=True)
    caller = Caller(kind="service", subject="bootstrap", nodes=UNBOUNDED)

    assert await caller.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING)
    permissions.require.assert_not_called()


@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("kind", ["service", "account", "cli", "minecraft_player"])
async def test_owned_credentials_consult_current_account_authority_without_a_guild(
    mocker: MockerFixture,
    allowed: bool,
    kind: Literal["service", "account", "cli", "minecraft_player"],
) -> None:
    permissions = mocker.create_autospec(PermissionService, instance=True)
    permissions.require.side_effect = (
        None
        if allowed
        else PermissionDeniedError(Decision(BUILD_SUBMISSION_VIEW_PENDING.name, allowed=False, reason=Reason.DEFAULT))
    )
    caller = Caller(kind=kind, subject="owned", account_id=7, nodes=UNBOUNDED)

    assert await caller.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING) is allowed
    assert caller.to_actor().account_id == 7
    assert caller.to_actor().guild_id is None
    permissions.require.assert_awaited_once_with(caller.to_actor(), BUILD_SUBMISSION_VIEW_PENDING)


@pytest.mark.parametrize("allowed", [False, True])
async def test_anonymous_authority_is_resolved_by_the_permission_service(mocker: MockerFixture, allowed: bool) -> None:
    permissions = mocker.create_autospec(PermissionService, instance=True)
    permissions.require.side_effect = (
        None
        if allowed
        else PermissionDeniedError(Decision(BUILD_SUBMISSION_VIEW_PENDING.name, allowed=False, reason=Reason.DEFAULT))
    )

    assert await ANONYMOUS.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING) is allowed
    permissions.require.assert_awaited_once_with(Actor(account_id=None, guild_id=None), BUILD_SUBMISSION_VIEW_PENDING)


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("effect", [None, Effect.DENY, Effect.FORBID])
async def test_require_distinguishes_credential_scope_from_account_acl(scoped: bool, effect: Effect | None) -> None:
    uow = AsyncMock()
    uow.__aenter__.return_value = uow
    grants = (
        ()
        if effect is None
        else (PermissionGrant(pattern=BUILD_SUBMISSION_VIEW_PENDING.name, effect=int(effect), subject_account_id=7),)
    )
    uow.permissions.load_for_subject.side_effect = lambda subject: SubjectRecords(
        subject=subject, epoch=1, grants=grants if subject.account_id == 7 else ()
    )
    permissions = PermissionService(uow_factory=lambda: uow)
    caller = Caller(kind="service", subject="owned", account_id=7, nodes=UNBOUNDED if scoped else frozenset())
    error_type = PermissionDeniedError if scoped else CredentialPermissionDeniedError
    with pytest.raises(error_type) as denied:
        await caller.require(permissions, BUILD_SUBMISSION_VIEW_PENDING)
    expected = {None: "missing_grant", Effect.DENY: "explicit_deny", Effect.FORBID: "forbidden"}[effect]
    assert denied.value.public_context == {
        "required_permission": BUILD_SUBMISSION_VIEW_PENDING.name,
        "permission_reason": expected if scoped else "credential_scope",
    }
    assert not await caller.allows(permissions, BUILD_SUBMISSION_VIEW_PENDING)
    if not scoped:
        uow.permissions.load_for_subject.assert_not_awaited()
    with pytest.raises(AuthenticationError):
        await ANONYMOUS.require(permissions, BUILD_SUBMISSION_VIEW_PENDING)


@pytest.mark.parametrize("owner", [None, 7])
async def test_core_permission_checks_enforce_the_service_credentials_ceiling(owner: int | None) -> None:
    actor = Caller(kind="service", subject="api-key:42", account_id=owner).to_actor()
    assert actor.credential_id == "api-key:42"
    assert actor.service_id == ("api-key:42" if owner is None else None)
    uow = AsyncMock()
    uow.__aenter__.return_value = uow
    grant = PermissionGrant(pattern="**", effect=int(Effect.ALLOW), subject_account_id=owner)
    uow.permissions.load_for_subject.return_value = SubjectRecords(subject=actor, epoch=1, grants=(grant,))
    permissions = PermissionService(uow_factory=lambda: uow)
    check = await permissions.check(actor, BUILD_SUBMISSION_VIEW_PENDING)
    assert not check.allowed
    assert check.decisions[0].reason is Reason.CREDENTIAL
