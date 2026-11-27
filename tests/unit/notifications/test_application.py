"""Notification edit policy and background cleanup contracts."""

from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from squid.core.actors import ActingContext, UserActor
from squid.core.edits.domain import EditCommand, EditValues, Revision
from squid.core.errors import OwnershipRequiredError
from squid.core.notifications import NotificationPreferences, NotificationService
from squid.core.notifications.application import NotificationRepository
from squid.core.notifications.edits import (
    PREFERENCES_EDIT,
    SUBSCRIPTIONS_EDIT,
    NotificationEditUnitOfWork,
    NotificationPreferencesEdit,
    NotificationSubscriptionsEdit,
    notification_target,
)
from squid.core.notifications.errors import NotificationSubscriptionNotFoundError
from squid.core.pagination import Page
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import ACCOUNT_SELF_READ, BUILD_SUBMISSION_VIEW_PENDING
from squid.core.settings.domain.runtime import RuntimeSettings
from tests.support.delivery_uow import NotificationUnitOfWork
from tests.support.settings import RuntimeSettingsStub


@pytest.mark.parametrize("kind", [PREFERENCES_EDIT, SUBSCRIPTIONS_EDIT])
async def test_edits_require_ownership_before_target_lookup(kind: str) -> None:
    uow = AsyncMock(spec=NotificationEditUnitOfWork)
    uow.notifications = AsyncMock()
    uow.notifications.lock_settings.return_value = 1
    uow.notifications.get_preferences.return_value = NotificationPreferences(account_id=7)
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
    with pytest.raises(OwnershipRequiredError):
        await handler.prepare(
            uow,
            UserActor(account_id=8),
            EditCommand(kind, (Revision(notification_target(7), 1),), EditValues.of(values)),
        )
    uow.notifications.subscription_target_exists.assert_not_called()


async def test_subscription_target_is_checked_before_persistence() -> None:
    uow = AsyncMock(spec=NotificationEditUnitOfWork)
    uow.notifications = AsyncMock()
    uow.notifications.lock_settings.return_value = 1
    uow.notifications.get_preferences.return_value = NotificationPreferences(account_id=7)
    uow.notifications.subscription_target_exists.return_value = False
    permissions = AsyncMock(spec=PermissionService)
    permissions.require.return_value = None
    with pytest.raises(NotificationSubscriptionNotFoundError):
        await NotificationSubscriptionsEdit(permissions).prepare(
            uow,
            UserActor(account_id=7),
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
        permissions=AsyncMock(spec=PermissionService),
        uow_factory=lambda: NotificationUnitOfWork(cast(NotificationRepository, repository)),
        runtime_settings=settings.get_runtime,
    )
    assert await service.cleanup(context=ActingContext.worker("test-worker")) == 1
    settings.value = RuntimeSettings(notification_retention_days=7)
    assert await service.cleanup(context=ActingContext.worker("test-worker")) == 1
    assert repository.days == [90, 7]


@pytest.mark.parametrize("staff", [True, False])
async def test_inbox_checks_the_reader_and_filters_staff_rows(staff: bool) -> None:
    repository = AsyncMock(spec=NotificationRepository)
    repository.list_inbox.return_value = Page((), 0, None, None)
    repository.count_inbox.return_value = 0
    permissions = AsyncMock(spec=PermissionService)
    permissions.check.return_value.allowed = staff
    service = NotificationService(permissions=permissions, uow_factory=lambda: NotificationUnitOfWork(repository))
    context = ActingContext.direct(UserActor(account_id=7))
    await service.inbox(context=context)
    permissions.require.assert_awaited_once_with(context.effective, ACCOUNT_SELF_READ)
    permissions.check.assert_awaited_once_with(context.effective, BUILD_SUBMISSION_VIEW_PENDING)
    assert repository.list_inbox.await_args.args == (7,)
    assert repository.list_inbox.await_args.kwargs["visibility"].include_staff is staff
