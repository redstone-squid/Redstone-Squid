"""Notification REST orchestration contracts."""

from types import SimpleNamespace
from typing import cast, override
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError
from whenever import Instant

from squid.api.security import UNBOUNDED, Caller
from squid.api.v1.notifications import (
    create_subscription,
    list_inbox,
    list_subscriptions,
    mark_all_read,
    mark_read,
    mark_unread,
    update_preferences,
)
from squid.api.v1.schemas.notifications import NotificationPreferenceUpdate, NotificationSubscriptionCreate
from squid.core.actors import ActingContext
from squid.core.notifications import (
    DEFAULT_INBOX_VISIBILITY,
    InboxVisibility,
    NotificationPreferences,
    NotificationService,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)
from squid.core.notifications.application import (
    FollowedSubject,
    Inbox,
    InboxEntry,
    NotificationEditorSnapshot,
)
from squid.core.notifications.domain import (
    ALL_INBOX_ITEMS,
    InboxListFilters,
    NotificationKind,
    NotificationMessage,
    NotifiedBuild,
    NotifiedRecord,
)
from squid.core.notifications.models import NotificationSubscription
from squid.core.pagination import FIRST_PAGE, ChronologicalSort, Page, PageSelector
from squid.runtime import ApiServices
from tests.support.notification_edits import NotificationEditRecorder

ACCOUNT = Caller(kind="account", subject="account:7", nodes=UNBOUNDED, account_id=7)


class NotificationRecorder(NotificationService):
    def __init__(self) -> None:
        self.preferences_result = NotificationPreferences(account_id=7)
        self.subscription_result: NotificationSubscription | None = None
        self.inbox_result = Inbox(Page(items=(), total=0, next=None, prev=None), unread_count=0)
        self.follows: tuple[FollowedSubject, ...] = ()
        self.unread: tuple[int, ...] = ()
        self.preference_updates: list[tuple[int, bool, bool]] = []
        self.subscription_calls: list[tuple[int, SubscriptionKind, UUID | None, RecordSubscriptionFilter | None]] = []
        self.inbox_reads: list[tuple[ActingContext, PageSelector, int, bool]] = []
        self.read_changes: list[tuple[int, int, bool, InboxVisibility]] = []

    @override
    async def preferences(self, *, context: ActingContext) -> NotificationPreferences:
        return self.preferences_result

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

    @override
    async def inbox(
        self,
        *,
        context: ActingContext,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        filters: InboxListFilters = ALL_INBOX_ITEMS,
    ) -> Inbox:
        self.inbox_reads.append((context, selector, page_size, filters.unread_only))
        return self.inbox_result

    @override
    async def unread_ids(self, *, context: ActingContext, through_id: int | None = None) -> tuple[int, ...]:
        return tuple(item for item in self.unread if through_id is None or item <= through_id)

    @override
    async def editor_snapshot(self, *, context: ActingContext) -> NotificationEditorSnapshot:
        return NotificationEditorSnapshot(self.preferences_result, self.follows)

    @override
    async def subscription_snapshot(
        self,
        *,
        context: ActingContext,
        kind: SubscriptionKind,
        subject_id: UUID | None,
        record_filter: RecordSubscriptionFilter | None,
    ) -> tuple[FollowedSubject | None, int]:
        if self.subscription_result is None:
            return None, 0
        return FollowedSubject(
            self.subscription_result, filter_summary=None if record_filter is None else record_filter.summary({})
        ), 1

    async def mark_read(
        self, account_id: int, notification_id: int, *, visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY
    ) -> None:
        self.read_changes.append((account_id, notification_id, True, visibility))

    async def mark_unread(
        self, account_id: int, notification_id: int, *, visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY
    ) -> None:
        self.read_changes.append((account_id, notification_id, False, visibility))


async def test_channels_stay_off_until_they_are_explicitly_turned_on() -> None:
    """An empty preference request must send disabled values for both channels."""
    notifications = NotificationRecorder()
    response = (
        await update_preferences.fn(
            NotificationPreferenceUpdate(),
            notifications,
            ACCOUNT,
            cast(ApiServices, SimpleNamespace(edits=NotificationEditRecorder(notifications))),
            uuid4(),
            '"notification-settings-7-r0"',
            _reservation=None,
        )
    ).content
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
                "tags": [{"tag_id": 4, "operator": "present"}, {"tag_id": 7, "operator": "exact", "value": "slim"}]
            },
        }
    )
    response = (
        await create_subscription.fn(
            request,
            notifications,
            ACCOUNT,
            cast(ApiServices, SimpleNamespace(edits=NotificationEditRecorder(notifications))),
            uuid4(),
            '"notification-settings-7-r0"',
            _reservation=None,
        )
    ).content
    expected = record_filter.as_dict()
    expected["criteria"] = expected.pop("base_titles")
    assert response.filter == expected
    assert response.filter_summary == "Tagged Tag #4 · Tag #7: slim"
    assert notifications.subscription_calls == [(7, SubscriptionKind.RECORD_FILTER, None, record_filter)]


def test_empty_record_filter_is_rejected_at_the_api_boundary() -> None:
    with pytest.raises(PydanticValidationError):
        NotificationSubscriptionCreate.model_validate({"kind": "record_filter", "filter": {}})


async def test_inbox_uses_the_authenticated_account() -> None:
    notifications = NotificationRecorder()
    await list_inbox.fn(notifications, ACCOUNT)
    assert notifications.inbox_reads == [(ACCOUNT.context(), PageSelector(sort=ChronologicalSort.NEWEST), 20, False)]


async def test_read_and_unread_routes_share_the_resolved_visibility() -> None:
    notifications = NotificationRecorder()
    visibility = InboxVisibility(include_staff=True)
    recorder = NotificationEditRecorder(notifications, visibility)
    services = cast(ApiServices, SimpleNamespace(edits=recorder))
    assert (await mark_read.fn(3, services, ACCOUNT, uuid4(), '"inbox-3-r1"', _reservation=None)).status_code == 204
    assert (await mark_unread.fn(3, services, ACCOUNT, uuid4(), '"inbox-3-r2"', _reservation=None)).status_code == 204
    assert [command.expected[0].value for command in recorder.commands] == [1, 2]
    assert notifications.read_changes == [(7, 3, True, visibility), (7, 3, False, visibility)]


async def test_notification_writes_accept_latest_state() -> None:
    recorder = NotificationEditRecorder(NotificationRecorder())
    services = cast(ApiServices, SimpleNamespace(edits=recorder))
    await mark_read.fn(3, services, ACCOUNT, uuid4(), _reservation=None)
    assert recorder.commands[0].expected[0].value is None


async def test_inbox_items_carry_typed_payloads_links_and_the_unread_count() -> None:
    notifications = NotificationRecorder()
    created = Instant.parse_iso("2026-11-01T12:00:00Z")
    record = NotifiedRecord(UUID("11111111-1111-4111-8111-111111111111"), "Fastest 2x2")
    entries = (
        InboxEntry(
            5,
            2,
            created,
            None,
            NotificationMessage(NotificationKind.RECORD_GAINED, NotifiedBuild(9, "2x2 Piston Door"), (record,)),
        ),
        InboxEntry(4, 1, created, created, NotificationMessage(NotificationKind.RECORD_RETIRED, records=(record,))),
    )
    notifications.inbox_result = Inbox(Page(items=entries, total=2, next=None, prev=None), unread_count=7)

    page = await list_inbox.fn(notifications, ACCOUNT, unread_only=[True])

    assert notifications.inbox_reads[0][3] is True
    assert page.unread_count == 7
    gained, retired = (item.model_dump(mode="json") for item in page.items)
    assert gained["href"] == "/builds/9"
    assert gained["payload"] == {
        "kind": "record_gained",
        "build": {"id": 9, "title": "2x2 Piston Door"},
        "records": [{"id": str(record.id), "title": "Fastest 2x2"}],
    }
    assert retired["href"] == f"/records/{record.id}"
    assert retired["payload"]["kind"] == "record_retired"


async def test_read_all_marks_unread_items_up_to_the_anchor_at_their_latest_revision() -> None:
    notifications = NotificationRecorder()
    notifications.unread = (3, 5, 8)
    recorder = NotificationEditRecorder(notifications)
    services = cast(ApiServices, SimpleNamespace(edits=recorder))

    response = await mark_all_read.fn(notifications, services, ACCOUNT, uuid4(), through_id=5, _reservation=None)

    assert response.status_code == 204
    (command,) = recorder.commands
    assert [(version.target.key, version.value) for version in command.expected] == [("3", None), ("5", None)]
    assert [(item, read) for _, item, read, _ in notifications.read_changes] == [(3, True), (5, True)]


async def test_read_all_with_nothing_unread_makes_no_edit() -> None:
    recorder = NotificationEditRecorder(NotificationRecorder())
    services = cast(ApiServices, SimpleNamespace(edits=recorder))
    await mark_all_read.fn(recorder.service, services, ACCOUNT, uuid4(), _reservation=None)
    assert recorder.commands == []


async def test_subscriptions_filter_by_subject_and_name_their_subjects() -> None:
    notifications = NotificationRecorder()
    creator, record = uuid4(), uuid4()
    notifications.follows = (
        FollowedSubject(
            NotificationSubscription(
                id=1, account_id=7, kind=SubscriptionKind.CREATOR, subject_id=creator, created_at=Instant.now()
            ),
            "Some Builder",
        ),
        FollowedSubject(
            NotificationSubscription(
                id=2, account_id=7, kind=SubscriptionKind.RECORD, subject_id=record, created_at=Instant.now()
            ),
            "Fastest 2x2",
        ),
    )

    response = await list_subscriptions.fn(
        notifications, ACCOUNT, kind=[SubscriptionKind.CREATOR], subject_id=[creator]
    )

    (item,) = response.content.items
    assert (item.id, item.subject_label, item.subject_href, item.filter_summary) == (
        1,
        "Some Builder",
        f"/accounts/{creator}",
        None,
    )
