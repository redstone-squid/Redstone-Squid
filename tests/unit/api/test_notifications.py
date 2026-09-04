"""Notification REST orchestration contracts."""

from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError
from whenever import Instant

from squid.api.security import UNBOUNDED, Caller
from squid.api.v1.notifications import (
    create_subscription,
    list_inbox,
    mark_read,
    mark_unread,
    resolve_inbox_visibility,
    update_preferences,
)
from squid.api.v1.schemas.notifications import NotificationPreferenceUpdate, NotificationSubscriptionCreate
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.notifications import (
    DEFAULT_INBOX_VISIBILITY,
    InboxNotification,
    InboxVisibility,
    NotificationPreferences,
    NotificationService,
    NotificationSubscription,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)
from squid.notifications.domain import NotificationKind
from squid.permissions.application import PermissionService
from squid.permissions.domain import PermissionNode, Subject

ACCOUNT = Caller(kind="account", subject="account:7", nodes=UNBOUNDED, account_id=7)


class NotificationRecorder(NotificationService):
    def __init__(self) -> None:
        self.preferences_result = NotificationPreferences(account_id=7, consent_pending=False)
        self.subscription_result: NotificationSubscription | None = None
        self.inbox_result: Page[InboxNotification] = Page(items=(), total=0, next=None, prev=None)
        self.preference_updates: list[tuple[int, bool, bool]] = []
        self.subscription_calls: list[tuple[int, SubscriptionKind, UUID | None, RecordSubscriptionFilter | None]] = []
        self.inbox_reads: list[tuple[int, PageSelector, int, InboxVisibility]] = []
        self.read_changes: list[tuple[int, int, bool, InboxVisibility]] = []

    async def set_preferences(self, account_id: int, *, web_enabled: bool, dm_enabled: bool) -> NotificationPreferences:
        self.preference_updates.append((account_id, web_enabled, dm_enabled))
        return self.preferences_result

    async def subscribe(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None = None,
        record_filter: RecordSubscriptionFilter | None = None,
    ) -> NotificationSubscription:
        self.subscription_calls.append((account_id, kind, subject_id, record_filter))
        assert self.subscription_result is not None
        return self.subscription_result

    async def inbox(
        self,
        account_id: int,
        *,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY,
    ) -> Page[InboxNotification]:
        self.inbox_reads.append((account_id, selector, page_size, visibility))
        return self.inbox_result

    async def mark_read(
        self, account_id: int, notification_id: int, *, visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY
    ) -> None:
        self.read_changes.append((account_id, notification_id, True, visibility))

    async def mark_unread(
        self, account_id: int, notification_id: int, *, visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY
    ) -> None:
        self.read_changes.append((account_id, notification_id, False, visibility))


class PermissionAnswer(PermissionService):
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    async def allows(self, subject: Subject, node: PermissionNode | str) -> bool:
        return self.allowed


async def test_channels_stay_off_until_they_are_explicitly_turned_on() -> None:
    """Accepting the privacy notice permits notifications; it does not enable any channel."""
    notifications = NotificationRecorder()

    response = await update_preferences(
        NotificationPreferenceUpdate(),
        notifications,
        ACCOUNT,
    )

    assert response.consent_pending is False
    assert response.web_enabled is False
    assert response.dm_enabled is False
    assert notifications.preference_updates == [(7, False, False)]


async def test_record_filter_subscription_preserves_presence_and_exact_predicates() -> None:
    notifications = NotificationRecorder()
    record_filter = RecordSubscriptionFilter(tags=(TagPredicate(4), TagPredicate(7, "exact", "slim")))
    notifications.subscription_result = NotificationSubscription(
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

    response = await create_subscription(request, notifications, ACCOUNT)

    assert response.filter == record_filter.as_dict()
    assert notifications.subscription_calls == [(7, SubscriptionKind.RECORD_FILTER, None, record_filter)]


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
    notifications = NotificationRecorder()
    item = InboxNotification(
        id=3,
        kind=NotificationKind.STAFF_BUILD_SUBMITTED,
        payload={"build_id": 42},
        created_at=Instant.now(),
    )
    notifications.inbox_result = Page(items=(item,), total=1, next=None, prev=None)
    permissions = PermissionAnswer(holds_node)

    visibility = await resolve_inbox_visibility(permissions, ACCOUNT)
    page = await list_inbox(notifications, ACCOUNT, visibility)

    assert page.items[0].kind == "staff_build_submitted"
    assert notifications.inbox_reads == [(7, PageSelector(), 20, InboxVisibility(include_staff=holds_node))]


async def test_read_and_unread_routes_share_the_resolved_visibility() -> None:
    notifications = NotificationRecorder()
    visibility = InboxVisibility(include_staff=True)

    assert (await mark_read(3, notifications, ACCOUNT, visibility)).status_code == 204
    assert (await mark_unread(3, notifications, ACCOUNT, visibility)).status_code == 204

    assert notifications.read_changes == [
        (7, 3, True, visibility),
        (7, 3, False, visibility),
    ]
