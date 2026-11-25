"""Settings application service tests."""

from functools import partial
from unittest.mock import Mock, create_autospec

import pytest

from squid.core.actors import ActingContext, AnonymousActor, UserActor
from squid.core.errors import AuthorizationError
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import SETTINGS_RUNTIME_EDIT, SETTINGS_RUNTIME_VIEW
from squid.core.settings.application import SettingsService
from squid.core.settings.models import ServerSetting
from tests.support.settings import FakeSettingsUnitOfWork, settings_state


async def test_membership_changes_commit_without_clearing_settings() -> None:
    committed = settings_state()
    service = SettingsService(
        permissions=create_autospec(PermissionService, instance=True),
        uow_factory=partial(FakeSettingsUnitOfWork, committed),
    )
    await service.guild_removed(1, context=ActingContext.direct(AnonymousActor()))
    assert committed.value == {}
    await service.guild_joined(1, context=ActingContext.direct(AnonymousActor()))
    assert committed.value[1].in_server
    committed.value[1].locale = "fr"
    await service.guild_removed(1, context=ActingContext.direct(AnonymousActor()))
    assert not committed.value[1].in_server
    assert committed.value[1].locale == "fr"
    await service.guild_joined(1, context=ActingContext.direct(AnonymousActor()))
    assert committed.value[1].in_server
    assert committed.value[1].locale == "fr"


async def test_reads_preserve_missing_server_semantics() -> None:
    committed = settings_state({1: ServerSetting(server_id=1, voting_channel_id=123), 2: ServerSetting(server_id=2)})
    service = SettingsService(
        permissions=create_autospec(PermissionService, instance=True),
        uow_factory=partial(FakeSettingsUnitOfWork, committed),
    )
    assert await service.get_many(iter([1, 2, 3]), "Vote", context=ActingContext.direct(AnonymousActor())) == {
        1: 123,
        2: None,
    }
    assert await service.get(3, "Vote", context=ActingContext.direct(AnonymousActor())) is None
    assert await service.get_locale(3, context=ActingContext.direct(AnonymousActor())) is None
    assert await service.get_all(3, context=ActingContext.direct(AnonymousActor())) == {}
    assert await service.get_all(1, context=ActingContext.direct(AnonymousActor())) == {
        "Smallest": None,
        "Fastest": None,
        "First": None,
        "Builds": None,
        "Vote": 123,
    }
    assert set(committed.value) == {1, 2}


@pytest.mark.parametrize("editing", [False, True])
async def test_reading_a_runtime_revision_checks_permission_before_persisting_defaults(editing: bool) -> None:
    permissions = create_autospec(PermissionService, instance=True)
    permissions.require.side_effect = AuthorizationError
    factory = Mock()
    settings = SettingsService(uow_factory=factory, permissions=permissions)
    context = ActingContext.direct(UserActor(account_id=42))
    with pytest.raises(AuthorizationError):
        await settings.runtime_snapshot(context=context, for_edit=editing)
    permissions.require.assert_awaited_once_with(
        context.effective, SETTINGS_RUNTIME_EDIT if editing else SETTINGS_RUNTIME_VIEW
    )
    factory.assert_not_called()
