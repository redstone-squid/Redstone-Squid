"""Notification application policy contracts."""

from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from whenever import Instant

from squid.notifications import CURRENT_NOTIFICATION_NOTICE_VERSION, NotificationPreferences, NotificationService
from squid.notifications.domain import SubscriptionKind
from squid.notifications.errors import NotificationConsentRequiredError, NotificationSubscriptionNotFoundError

SUBJECT_ID = UUID("11111111-1111-1111-1111-111111111111")


async def test_channels_cannot_be_enabled_before_notification_consent() -> None:
    repository = AsyncMock()
    repository.update_preferences.return_value = None
    service = NotificationService(cast(Any, repository))

    with pytest.raises(NotificationConsentRequiredError):
        await service.set_preferences(7, web_enabled=True, dm_enabled=False)


async def test_subscriptions_require_notification_specific_consent() -> None:
    repository = AsyncMock()
    repository.get_preferences.return_value = NotificationPreferences(
        account_id=7,
        notice_version=None,
        consented_at=None,
    )
    service = NotificationService(cast(Any, repository))

    with pytest.raises(NotificationConsentRequiredError):
        await service.subscribe(7, kind=SubscriptionKind.CREATOR, subject_id=SUBJECT_ID)

    repository.subscription_target_exists.assert_not_awaited()


async def test_public_subscription_targets_are_validated_before_persistence() -> None:
    repository = AsyncMock()
    repository.get_preferences.return_value = NotificationPreferences(
        account_id=7,
        notice_version=CURRENT_NOTIFICATION_NOTICE_VERSION,
        consented_at=Instant.now(),
    )
    repository.subscription_target_exists.return_value = False
    service = NotificationService(cast(Any, repository))

    with pytest.raises(NotificationSubscriptionNotFoundError):
        await service.subscribe(7, kind=SubscriptionKind.RECORD, subject_id=SUBJECT_ID)

    repository.add_subscription.assert_not_awaited()
