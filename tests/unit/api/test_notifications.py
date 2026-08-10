"""Notification REST orchestration contracts."""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError as PydanticValidationError
from whenever import Instant

from squid.api.security import Principal, Scope
from squid.api.v1.notifications import accept_notice, create_subscription, list_inbox
from squid.api.v1.schemas.notifications import NotificationPreferenceUpdate, NotificationSubscriptionCreate
from squid.core.pagination import SignedCursor
from squid.notifications import (
    CURRENT_NOTIFICATION_NOTICE_VERSION,
    InboxNotification,
    NotificationPreferences,
    NotificationSubscription,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)
from squid.notifications.domain import NotificationKind

USER = Principal(kind="user", subject="user:7", scopes=frozenset(Scope), discord_id=123, user_id=7)
SIGNER = SignedCursor(b"notification-api-test-secret")


async def test_notification_consent_is_independent_and_defaults_channels_off() -> None:
    notifications = AsyncMock()
    notifications.accept_notice.return_value = NotificationPreferences(
        user_id=7,
        notice_version=CURRENT_NOTIFICATION_NOTICE_VERSION,
        consented_at=Instant.now(),
    )

    response = await accept_notice(
        NotificationPreferenceUpdate(),
        cast(Any, notifications),
        USER,
    )

    assert response.consented is True
    assert response.web_enabled is False
    assert response.dm_enabled is False
    notifications.accept_notice.assert_awaited_once_with(7, web_enabled=False, dm_enabled=False)


async def test_record_filter_subscription_preserves_presence_and_exact_predicates() -> None:
    notifications = AsyncMock()
    record_filter = RecordSubscriptionFilter(tags=(TagPredicate(4), TagPredicate(7, "exact", "slim")))
    notifications.subscribe.return_value = NotificationSubscription(
        id=9,
        user_id=7,
        kind=SubscriptionKind.RECORD_FILTER,
        subject_id=None,
        record_filter=record_filter,
        created_at=Instant.now(),
    )
    request = NotificationSubscriptionCreate.model_validate(
        {
            "kind": "record_filter",
            "filter": {
                "tags": [
                    {"tag_id": 4, "operator": "present"},
                    {"tag_id": 7, "operator": "exact", "value": "slim"},
                ]
            },
        }
    )

    response = await create_subscription(request, cast(Any, notifications), USER)

    assert response.filter == record_filter.as_dict()
    notifications.subscribe.assert_awaited_once_with(
        7,
        kind=SubscriptionKind.RECORD_FILTER,
        subject_id=None,
        record_filter=record_filter,
    )


def test_empty_record_filter_is_rejected_at_the_api_boundary() -> None:
    with pytest.raises(PydanticValidationError):
        NotificationSubscriptionCreate.model_validate({"kind": "record_filter", "filter": {}})


async def test_staff_inbox_access_is_rechecked_on_each_read() -> None:
    notifications = AsyncMock()
    notifications.can_view_staff.return_value = True
    notifications.inbox.return_value = (
        InboxNotification(
            id=3,
            kind=NotificationKind.STAFF_BUILD_SUBMITTED,
            payload={"build_id": 42},
            created_at=Instant.now(),
        ),
    )

    page = await list_inbox(cast(Any, notifications), SIGNER, USER)

    assert page.items[0].kind == "staff_build_submitted"
    notifications.can_view_staff.assert_awaited_once_with(123)
    notifications.inbox.assert_awaited_once_with(7, after_id=None, limit=21, include_staff=True)
