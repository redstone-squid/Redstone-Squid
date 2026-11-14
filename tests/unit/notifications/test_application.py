"""Notification edit policy and background cleanup contracts."""

from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from squid.accounts.errors import ConsentRequiredError
from squid.core.actors import Actor
from squid.edits.domain import EditCommand, EditValues, Revision
from squid.notifications import NotificationPreferences, NotificationService
from squid.notifications.application import NotificationRepository
from squid.notifications.edits import (
    PREFERENCES_EDIT,
    SUBSCRIPTIONS_EDIT,
    NotificationEditUnitOfWork,
    NotificationPreferencesEdit,
    NotificationSubscriptionsEdit,
    notification_target,
)
from squid.notifications.errors import NotificationSubscriptionNotFoundError
from squid.permissions.application.services import PermissionService
from squid.settings.domain.runtime import RuntimeSettings
from tests.support.delivery_uow import NotificationUnitOfWork
from tests.support.settings import RuntimeSettingsStub


@pytest.mark.parametrize("kind", [PREFERENCES_EDIT, SUBSCRIPTIONS_EDIT])
async def test_edits_require_consent_before_target_lookup(kind: str) -> None:
    uow = AsyncMock(spec=NotificationEditUnitOfWork)
    uow.notifications = AsyncMock()
    uow.notifications.lock_settings.return_value = 1
    uow.notifications.get_preferences.return_value = NotificationPreferences(account_id=7, consent_pending=True)
    permissions = AsyncMock(spec=PermissionService)
    permissions.require.return_value = None
    handler = (
        NotificationPreferencesEdit(permissions)
        if kind == PREFERENCES_EDIT
        else NotificationSubscriptionsEdit(permissions)
    )
    values: dict[str, JsonValue] = (
        {"web_enabled": True} if kind == PREFERENCES_EDIT else {"create": {"kind": "creator"}}
    )
    with pytest.raises(ConsentRequiredError):
        await handler.prepare(
            uow, Actor(account_id=7), EditCommand(kind, (Revision(notification_target(7), 1),), EditValues.of(values))
        )
    uow.notifications.subscription_target_exists.assert_not_called()


async def test_subscription_target_is_checked_before_persistence() -> None:
    uow = AsyncMock(spec=NotificationEditUnitOfWork)
    uow.notifications = AsyncMock()
    uow.notifications.lock_settings.return_value = 1
    uow.notifications.get_preferences.return_value = NotificationPreferences(account_id=7, consent_pending=False)
    uow.notifications.subscription_target_exists.return_value = False
    permissions = AsyncMock(spec=PermissionService)
    permissions.require.return_value = None
    with pytest.raises(NotificationSubscriptionNotFoundError):
        await NotificationSubscriptionsEdit(permissions).prepare(
            uow,
            Actor(account_id=7),
            EditCommand(
                SUBSCRIPTIONS_EDIT,
                (Revision(notification_target(7), 1),),
                EditValues.of({"create": {"kind": "record", "subject_id": "11111111-1111-1111-1111-111111111111"}}),
            ),
        )
    uow.notifications.save_subscription.assert_not_called()


async def test_cleanup_reads_retention_each_time() -> None:
    class Repository:
        def __init__(self) -> None:
            self.days: list[int] = []

        async def cleanup(self, *, retention_days: int) -> int:
            self.days.append(retention_days)
            return 1

    repository = Repository()
    settings = RuntimeSettingsStub()
    service = NotificationService(
        uow_factory=lambda: NotificationUnitOfWork(cast(NotificationRepository, repository)),
        runtime_settings=settings.get_runtime,
    )
    assert await service.cleanup() == 1
    settings.value = RuntimeSettings(notification_retention_days=7)
    assert await service.cleanup() == 1
    assert repository.days == [90, 7]
