"""Notification application policy contracts."""

from dataclasses import dataclass
from typing import cast
from uuid import UUID

import pytest

from squid.accounts.errors import ConsentRequiredError
from squid.notifications import NotificationPreferences, NotificationService
from squid.notifications.application import NotificationRepository
from squid.notifications.domain import (
    InboxVisibility,
    NotificationSubscription,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.notifications.errors import NotificationSubscriptionNotFoundError

SUBJECT_ID = UUID("11111111-1111-1111-1111-111111111111")


@dataclass(slots=True)
class NotificationRepositoryFake:
    preferences: NotificationPreferences
    target_exists: bool = True
    attempted_add: bool = False
    target_checks: int = 0

    async def get_preferences(self, account_id: int) -> NotificationPreferences:
        return self.preferences

    async def update_preferences(
        self, account_id: int, *, web_enabled: bool, dm_enabled: bool
    ) -> NotificationPreferences | None:
        return None

    async def subscription_target_exists(self, kind: SubscriptionKind, subject_id: UUID) -> bool:
        self.target_checks += 1
        return self.target_exists

    async def add_subscription(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None,
        record_filter: RecordSubscriptionFilter | None,
    ) -> NotificationSubscription:
        self.attempted_add = True
        raise AssertionError("a rejected subscription must not be persisted")

    async def mark_read(self, account_id: int, notification_id: int, *, visibility: InboxVisibility) -> bool:
        return account_id == 7 and notification_id == 3 and visibility.include_staff

    async def mark_unread(self, account_id: int, notification_id: int, *, visibility: InboxVisibility) -> bool:
        return account_id == 7 and notification_id == 3 and visibility.include_staff


def _repository(*, consent_pending: bool, target_exists: bool = True) -> NotificationRepositoryFake:
    return NotificationRepositoryFake(
        NotificationPreferences(account_id=7, consent_pending=consent_pending), target_exists=target_exists
    )


def _service(repository: NotificationRepositoryFake) -> NotificationService:
    return NotificationService(cast(NotificationRepository, repository))


async def test_channels_cannot_be_enabled_before_the_privacy_notice_is_accepted() -> None:
    with pytest.raises(ConsentRequiredError):
        await _service(_repository(consent_pending=True)).set_preferences(7, web_enabled=True, dm_enabled=False)


async def test_subscriptions_require_the_privacy_notice_before_target_lookup() -> None:
    repository = _repository(consent_pending=True)

    with pytest.raises(ConsentRequiredError):
        await _service(repository).subscribe(7, kind=SubscriptionKind.CREATOR, subject_id=SUBJECT_ID)

    assert repository.target_checks == 0
    assert not repository.attempted_add


async def test_public_subscription_targets_are_validated_before_persistence() -> None:
    repository = _repository(consent_pending=False, target_exists=False)

    with pytest.raises(NotificationSubscriptionNotFoundError):
        await _service(repository).subscribe(7, kind=SubscriptionKind.RECORD, subject_id=SUBJECT_ID)

    assert repository.target_checks == 1
    assert not repository.attempted_add


async def test_read_and_unread_use_the_same_visibility_contract() -> None:
    service = _service(_repository(consent_pending=False))
    visibility = InboxVisibility(include_staff=True)

    await service.mark_read(7, 3, visibility=visibility)
    await service.mark_unread(7, 3, visibility=visibility)
