"""The notification preference and subscription workspace."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast, override
from uuid import UUID

import pytest
from whenever import Instant

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.notifications_view import NotificationScreen
from squid.core.actors import Actor
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.notifications import (
    DEFAULT_INBOX_VISIBILITY,
    InboxVisibility,
    NotificationPreferences,
    NotificationService,
    RecordSubscriptionFilter,
    SubscriptionKind,
)
from squid.notifications.application import NotificationEditorSnapshot
from squid.notifications.domain import NotificationKind
from squid.notifications.models import InboxNotification, NotificationSubscription
from squid.suggestions.domain import Suggestion
from squid.tags.domain import TagValueType
from squid_ui.ext.discord import SessionSpec, primitives
from squid_ui.ext.discord.testing import stage
from squid_ui.selectors import query, select
from squid_ui.testing import RecordingResponder, choose, labels, press, render_index, render_tree, submit
from tests.support.notification_edits import NotificationEditRecorder


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
        self.inbox_reads: list[InboxVisibility] = []
        self.read_changes: list[tuple[int, int, bool, InboxVisibility]] = []

    @override
    async def editor_snapshot(self, account_id: int) -> NotificationEditorSnapshot:
        return NotificationEditorSnapshot(await self.preferences(account_id), await self.subscriptions(account_id))

    @override
    async def preferences(self, account_id: int) -> NotificationPreferences:
        return NotificationPreferences(account_id, consent_pending=False)

    @override
    async def subscriptions(self, account_id: int) -> tuple[NotificationSubscription, ...]:
        assert account_id == 7
        self.subscription_reads += 1
        return ()

    async def set_preferences(self, account_id: int, *, web_enabled: bool, dm_enabled: bool) -> NotificationPreferences:
        return NotificationPreferences(
            account_id, consent_pending=False, web_enabled=web_enabled, dm_enabled=dm_enabled
        )

    async def subscribe(
        self,
        account_id: int,
        *,
        kind: SubscriptionKind,
        subject_id: UUID | None = None,
        record_filter: RecordSubscriptionFilter | None = None,
    ) -> NotificationSubscription:
        self.subscribe_calls.append(SubscribeCall(account_id, kind, subject_id, record_filter))
        return NotificationSubscription(
            id=1,
            account_id=account_id,
            kind=kind,
            subject_id=subject_id,
            record_filter=record_filter,
            created_at=Instant.now(),
        )

    @override
    async def inbox(
        self,
        account_id: int,
        *,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
        visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY,
    ) -> Page[InboxNotification]:
        self.inbox_reads.append(visibility)
        item = InboxNotification(
            id=3,
            kind=NotificationKind.BUILD_CONFIRMED,
            payload={"build_id": 9},
            created_at=Instant.now(),
            read_at=Instant.now() if self.read_changes and self.read_changes[-1][2] else None,
        )
        item.revision = 1 + len(self.read_changes)
        return Page(items=(item,), total=1, next=None, prev=None)

    async def mark_read(
        self,
        account_id: int,
        notification_id: int,
        *,
        visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY,
    ) -> None:
        self.read_changes.append((account_id, notification_id, True, visibility))

    async def mark_unread(
        self,
        account_id: int,
        notification_id: int,
        *,
        visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY,
    ) -> None:
        self.read_changes.append((account_id, notification_id, False, visibility))


@dataclass(frozen=True)
class ScreenHarness:
    screen: NotificationScreen
    notifications: NotificationRecorder


def make_screen(
    *,
    visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY,
    visibility_resolver: Callable[[], Awaitable[InboxVisibility]] | None = None,
) -> ScreenHarness:
    notifications = NotificationRecorder()

    async def actor() -> Actor:
        return Actor(account_id=7, discord_user_id=42)

    return ScreenHarness(
        NotificationScreen(
            notifications=notifications,
            edits=NotificationEditRecorder(notifications, visibility),
            actor=actor,
            account_id=7,
            author_id=42,
            visibility=visibility,
            visibility_resolver=visibility_resolver,
        ),
        notifications,
    )


async def test_screen_offers_all_follow_workflows() -> None:
    screen = make_screen().screen
    await screen.on_load()

    await press(screen.tabs, "notification-tabs.following")
    rendered = render_tree(screen)

    assert isinstance(NotificationScreen.__response_spec__.session, SessionSpec)
    assert NotificationScreen.__response_spec__.session.name == "notifications"
    assert NotificationScreen.__response_spec__.audience == "personal"
    assert {"Follow creator", "Follow record", "Follow matching records"} <= set(labels(rendered))


async def test_following_a_creator_refreshes_the_subscription_browser() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    creator = UUID("11111111-1111-1111-1111-111111111111")
    responder = RecordingResponder()

    with sl.runtime.transaction():
        await screen.subscriptions._creator_picked(
            cast(
                sd.ActionEvent, sl.ActionEvent(context=None, actor=sl.interactions.Actor(id="42"), responder=responder)
            ),
            Suggestion(str(creator), "Builder"),
        )

    assert harness.notifications.subscribe_calls == [SubscribeCall(7, SubscriptionKind.CREATOR, creator, None)]
    assert harness.notifications.subscription_reads == 2
    assert len(responder.notices) == 1


async def test_following_a_record_filter_uses_typed_tag_input() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    responder = RecordingResponder()
    screen.subscriptions.filter_tag = Suggestion("4", "Compact")

    with sl.runtime.transaction():
        await submit(
            screen.subscriptions,
            "follow-filter",
            {
                "build_kind": "door",
                "base_title": None,
                "version_scope": "up_to_date",
                "tag_value": "compact",
            },
            responder=responder,
        )

    call = harness.notifications.subscribe_calls[-1]
    assert call.kind is SubscriptionKind.RECORD_FILTER
    assert call.record_filter is not None
    record_filter = call.record_filter
    assert record_filter.build_kinds == frozenset({"door"})
    assert record_filter.version_scopes == frozenset({"up_to_date"})
    assert record_filter.tags[0].tag_id == 4
    assert record_filter.tags[0].value == "compact"


async def test_empty_record_filter_does_not_call_the_service() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    responder = RecordingResponder()

    with sl.runtime.transaction():
        await submit(
            screen.subscriptions,
            "follow-filter",
            {
                "build_kind": None,
                "base_title": None,
                "version_scope": None,
                "tag_value": None,
            },
            responder=responder,
        )

    assert harness.notifications.subscribe_calls == []
    assert len(responder.notices) == 1


async def test_inbox_items_can_be_marked_read_and_unread_without_typing_ids() -> None:
    visibility = InboxVisibility(include_staff=True)
    harness = make_screen(visibility=visibility)
    screen = harness.screen
    await screen.on_load()

    await choose(screen.inbox, "inbox", "3")
    with sl.runtime.transaction():
        await press(screen.inbox, "mark_selected_read")
    await choose(screen.inbox, "inbox", "3")
    with sl.runtime.transaction():
        await press(screen.inbox, "mark_selected_unread")

    assert harness.notifications.inbox_reads == [visibility, visibility, visibility]
    assert harness.notifications.read_changes == [(7, 3, True, visibility), (7, 3, False, visibility)]


async def test_inbox_refresh_rechecks_staff_visibility() -> None:
    refreshed = InboxVisibility()

    async def resolve() -> InboxVisibility:
        return refreshed

    harness = make_screen(
        visibility=InboxVisibility(include_staff=True),
        visibility_resolver=resolve,
    )
    screen = harness.screen
    await screen.on_load()

    await choose(screen.inbox, "inbox", "3")
    with sl.runtime.transaction():
        await press(screen.inbox, "mark_selected_read")

    assert harness.notifications.inbox_reads[-1] == refreshed


async def test_mounted_inbox_updates_summary_without_undo() -> None:
    harness = make_screen()
    async with stage(harness.screen, owner=object()) as ui:
        inbox_key = next(key for key in ui.root.snapshot().handler_keys if key.endswith(".inbox"))
        read_key = next(key for key in ui.root.snapshot().handler_keys if key.endswith(".mark_selected_read"))
        await ui.select(inbox_key, ["3"])
        await ui.press(read_key)
        assert harness.screen.inbox.unread_count == 0
        assert harness.screen.inbox.selected_inbox_ids == ("3",)
        assert harness.screen.subscriptions.selected_ids == ()

        handler_keys = ui.root.snapshot().handler_keys
        assert not any(key.endswith(("edit-undo", "edit-redo")) for key in handler_keys)
        assert not any(key.endswith(".follow-creator") for key in handler_keys)
        assert not any(key.endswith(".web") for key in handler_keys)
        await ui.press(
            next(key for key in ui.root.snapshot().handler_keys if key.endswith("notification-tabs.following"))
        )
        assert any(key.endswith(".follow-creator") for key in ui.root.snapshot().handler_keys)
        await ui.press(
            next(key for key in ui.root.snapshot().handler_keys if key.endswith("notification-tabs.delivery"))
        )
        assert any(key.endswith(".web") for key in ui.root.snapshot().handler_keys)
        await ui.press(next(key for key in ui.root.snapshot().handler_keys if key.endswith("notification-tabs.inbox")))
        assert harness.screen.inbox.selected_inbox_ids == ("3",)


async def test_selection_actions_need_something_to_select() -> None:
    # The recorder follows nothing but has inbox entries: unfollowing is impossible, while marking
    # read is greyed out until an entry is selected.
    screen = make_screen().screen
    await screen.on_load()

    tree = render_index(screen)

    assert not query(tree, select(object).key("unfollow_selected")).exists()
    mark_read = query(tree, select(sl.semantic.ActionControl).key("mark_selected_read")).one().node
    assert mark_read.available is False


async def test_follows_after_the_first_page_can_be_selected_and_named() -> None:
    screen = make_screen().screen
    await screen.on_load()
    screen.settings.subscriptions = tuple(
        NotificationSubscription(
            id=index, account_id=7, kind=SubscriptionKind.CREATOR, subject_id=UUID(int=index), created_at=Instant.now()
        )
        for index in range(1, 32)
    )
    screen.settings.labels = {31: "Last builder"}
    async with stage(screen.subscriptions, owner=object()) as ui:
        await ui.select(ui.key_for("unfollow-0"), ["1"])
        await ui.press(ui.key_for("__cursor_next.$/follows"))
        shown = ui.control(ui.key_for("unfollow-25")).payload["options"]
        assert [option["value"] for option in shown] == [str(index) for index in range(26, 32)]
        assert any(option["label"] == "Last builder" for option in shown)
        await ui.select(ui.key_for("unfollow-25"), ["31"])
        assert screen.subscriptions.selected_ids == ("1", "31")
        await ui.press(ui.key_for("__cursor_previous.$/follows"))
        assert screen.subscriptions.selected_ids == ("1", "31")
        await ui.select(ui.key_for("unfollow-0"), [])
        assert screen.subscriptions.selected_ids == ("31",)
        screen.settings.subscriptions = screen.settings.subscriptions[:10]
        screen.subscriptions.reconcile_selection()
        await ui.root.refresh()
        assert screen.subscriptions.selected_ids == ()
        assert len(ui.control(ui.key_for("unfollow-0")).payload["options"]) == 10
        ui.assert_within_limits()


@pytest.mark.parametrize(
    ("value_type", "value"),
    [(TagValueType.BOOLEAN, False), (TagValueType.NUMERIC, 0.0), (TagValueType.TEXT, "compact")],
)
async def test_tag_filter_keeps_false_and_zero_as_exact_values(
    value_type: TagValueType, value: bool | float | str
) -> None:
    harness = make_screen()
    await harness.screen.on_load()
    controls = harness.screen.subscriptions
    controls.filter_tag = Suggestion("4", "Selected tag")
    controls.filter_tag_type = value_type
    with sl.runtime.transaction():
        await submit(controls, "follow-filter", {"tag_value": value}, responder=RecordingResponder())
    saved = harness.notifications.subscribe_calls[-1].record_filter
    assert saved is not None
    assert saved.tags[0].operator == "exact"
    assert saved.tags[0].value == value
    assert type(saved.tags[0].value) is type(value)


async def test_inbox_selection_shows_build_context_and_routed_action() -> None:
    screen = make_screen().screen
    await screen.on_load()

    assert "Build #9 was confirmed." in labels(render_tree(screen.inbox))
    assert "Open build" not in labels(render_tree(screen.inbox))
    await choose(screen.inbox, "inbox", "3")

    details = screen.inbox._selected_details()
    assert len(details) == 1
    section = details[0]
    assert isinstance(section, primitives.Section)
    assert isinstance(section.accessory, primitives.RoutedButton)
    assert "/builds/9" in section.accessory.route_id
    assert "Open build" in labels(render_tree(screen.inbox))


async def test_inbox_hides_action_for_notification_without_a_build() -> None:
    screen = make_screen().screen
    await screen.on_load()
    screen.inbox._inbox[0].payload = {}
    await choose(screen.inbox, "inbox", "3")

    assert "Open build" not in labels(render_tree(screen.inbox))
    assert "A build was confirmed." in labels(render_tree(screen.inbox))


async def test_following_does_not_render_empty_subtext_markers() -> None:
    harness = make_screen()
    async with stage(harness.screen, owner=object()) as ui:
        await ui.press(
            next(key for key in ui.root.snapshot().handler_keys if key.endswith("notification-tabs.following"))
        )
        assert not any(line.strip() == "-#" for text in ui.texts() for line in text.splitlines())
        assert any(key.endswith(".filter-tag") for key in ui.root.snapshot().handler_keys)
        assert any(key.endswith(".follow-creator") for key in ui.root.snapshot().handler_keys)
