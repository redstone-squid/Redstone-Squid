"""Notification REST orchestration contracts."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError as PydanticValidationError
from whenever import Instant

from squid.api.security import UNBOUNDED, Caller
from squid.api.v1.notifications import create_subscription, list_inbox, update_preferences
from squid.api.v1.schemas.notifications import NotificationPreferenceUpdate, NotificationSubscriptionCreate
from squid.core.pagination import Page, PageSelector
from squid.notifications import (
    InboxNotification,
    NotificationPreferences,
    NotificationSubscription,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)
from squid.notifications.domain import NotificationKind

ACCOUNT = Caller(kind="account", subject="account:7", nodes=UNBOUNDED, account_id=7)


async def test_channels_stay_off_until_they_are_explicitly_turned_on() -> None:
    """Accepting the privacy notice permits notifications; it does not enable any channel."""
    notifications = AsyncMock()
    notifications.set_preferences.return_value = NotificationPreferences(account_id=7, consent_pending=False)

    response = await update_preferences(
        NotificationPreferenceUpdate(),
        cast(Any, notifications),
        ACCOUNT,
    )

    assert response.consent_pending is False
    assert response.web_enabled is False
    assert response.dm_enabled is False
    notifications.set_preferences.assert_awaited_once_with(7, web_enabled=False, dm_enabled=False)


async def test_record_filter_subscription_preserves_presence_and_exact_predicates() -> None:
    notifications = AsyncMock()
    record_filter = RecordSubscriptionFilter(tags=(TagPredicate(4), TagPredicate(7, "exact", "slim")))
    notifications.subscribe.return_value = NotificationSubscription(
        id=9,
        account_id=7,
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

    response = await create_subscription(request, cast(Any, notifications), ACCOUNT)

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


@pytest.mark.parametrize("holds_node", [True, False])
async def test_staff_inbox_access_follows_the_pending_submission_node(holds_node: bool) -> None:
    """Rechecked on each read, and now against a credential rather than a snowflake.

    `caller_allows` intersects the credential's nodes with the permission engine's
    answer, so a credential that does not carry the node cannot reach staff items at
    all -- which the retired config allowlist, keyed on a snowflake, could not express.
    """
    notifications = AsyncMock()
    item = InboxNotification(
        id=3,
        kind=NotificationKind.STAFF_BUILD_SUBMITTED,
        payload={"build_id": 42},
        created_at=Instant.now(),
    )
    notifications.inbox.return_value = Page(items=(item,), total=1, next=None, prev=None)
    permissions = cast(Any, SimpleNamespace(allows=AsyncMock(return_value=holds_node)))

    page = await list_inbox(cast(Any, notifications), permissions, ACCOUNT)

    assert page.items[0].kind == "staff_build_submitted"
    notifications.inbox.assert_awaited_once_with(7, selector=PageSelector(), page_size=20, include_staff=holds_node)
