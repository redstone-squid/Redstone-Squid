"""The notification preference and subscription workspace."""

from dataclasses import dataclass
from uuid import UUID

from whenever import Instant

from squid.bot.notifications_view import NotificationScreen
from squid.notifications import (
    InboxNotification,
    NotificationPreferences,
    NotificationService,
    NotificationSubscription,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.notifications.domain import NotificationKind
from squid_ui.testing import RecordingResponder, choose, labels, press, submit


@dataclass(frozen=True)
class SubscribeCall:
    account_id: int
    kind: SubscriptionKind
    subject_id: UUID | None
    record_filter: RecordSubscriptionFilter | None


class NotificationRecorder(NotificationService):
    def __init__(self) -> None:
        self.subscription_reads = 0
        self.subscribe_calls: list[SubscribeCall] = []
        self.inbox_reads = 0
        self.read_changes: list[tuple[int, int, bool]] = []

    async def preferences(self, account_id: int) -> NotificationPreferences:
        return NotificationPreferences(account_id, consent_pending=False)

    async def subscriptions(self, account_id: int) -> tuple[NotificationSubscription, ...]:
        assert account_id == 7
        self.subscription_reads += 1
        return ()

    async def subscribe(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None = None,
        record_filter: RecordSubscriptionFilter | None = None,
    ) -> NotificationSubscription:
        self.subscribe_calls.append(SubscribeCall(account_id, kind, subject_id, record_filter))
        return NotificationSubscription(1, account_id, kind, subject_id, record_filter, Instant.now())

    async def inbox(self, account_id: int, *, selector=None, page_size: int = 20, visibility=None):
        from squid.core.pagination import Page

        self.inbox_reads += 1
        return Page(
            items=(InboxNotification(3, NotificationKind.BUILD_CONFIRMED, {"build_id": 9}, Instant.now()),),
            total=1,
            next=None,
            prev=None,
        )

    async def mark_read(self, account_id: int, notification_id: int, *, visibility=None) -> None:
        self.read_changes.append((account_id, notification_id, True))

    async def mark_unread(self, account_id: int, notification_id: int, *, visibility=None) -> None:
        self.read_changes.append((account_id, notification_id, False))


@dataclass(frozen=True)
class ScreenHarness:
    screen: NotificationScreen
    notifications: NotificationRecorder


def make_screen() -> ScreenHarness:
    notifications = NotificationRecorder()
    return ScreenHarness(
        NotificationScreen(notifications=notifications, account_id=7, author_id=42),
        notifications,
    )


async def test_screen_offers_all_follow_workflows() -> None:
    screen = make_screen().screen
    await screen.on_load()

    rendered = screen.render()

    assert NotificationScreen.session is not None
    assert NotificationScreen.session.name == "notifications"
    assert NotificationScreen.audience == "personal"
    assert {"Follow creator", "Follow record", "Follow matching records"} <= set(labels(rendered))


async def test_following_a_creator_refreshes_the_subscription_browser() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    creator = UUID("11111111-1111-1111-1111-111111111111")
    responder = RecordingResponder()

    await submit(screen, "follow-creator", {"creator": str(creator)}, responder=responder)

    assert harness.notifications.subscribe_calls == [SubscribeCall(7, SubscriptionKind.CREATOR, creator, None)]
    assert harness.notifications.subscription_reads == 2
    assert len(responder.notices) == 1


async def test_following_a_record_filter_uses_typed_tag_input() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    responder = RecordingResponder()

    await submit(
        screen,
        "follow-filter",
        {
            "build_kind": "door",
            "record_class": None,
            "version_scope": "current",
            "tag": 4,
            "tag_value": "compact",
        },
        responder=responder,
    )

    call = harness.notifications.subscribe_calls[-1]
    assert call.kind is SubscriptionKind.RECORD_FILTER
    assert call.record_filter is not None
    record_filter = call.record_filter
    assert record_filter.build_kinds == frozenset({"door"})
    assert record_filter.version_scopes == frozenset({"current"})
    assert record_filter.tags[0].tag_id == 4
    assert record_filter.tags[0].value == "compact"


async def test_empty_record_filter_does_not_call_the_service() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    responder = RecordingResponder()

    await submit(
        screen,
        "follow-filter",
        {
            "build_kind": None,
            "record_class": None,
            "version_scope": None,
            "tag": None,
            "tag_value": None,
        },
        responder=responder,
    )

    assert harness.notifications.subscribe_calls == []
    assert len(responder.notices) == 1


async def test_inbox_items_can_be_marked_read_and_unread_without_typing_ids() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()

    await choose(screen, "inbox", "3")
    await press(screen, "mark_selected_read")
    await choose(screen, "inbox", "3")
    await press(screen, "mark_selected_unread")

    assert harness.notifications.read_changes == [(7, 3, True), (7, 3, False)]
