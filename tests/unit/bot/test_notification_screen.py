"""The notification preference and subscription workspace."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import squid_ui as sl
from squid.bot.notifications_view import NotificationScreen
from squid.notifications import NotificationPreferences, SubscriptionKind
from squid_ui.testing import labels


def make_screen() -> NotificationScreen:
    notifications = SimpleNamespace(
        preferences=AsyncMock(return_value=NotificationPreferences(7, consent_pending=False)),
        subscriptions=AsyncMock(return_value=()),
        set_preferences=AsyncMock(),
        subscribe=AsyncMock(),
        unsubscribe=AsyncMock(),
    )
    return NotificationScreen(notifications=cast(Any, notifications), account_id=7, author_id=42)


async def test_screen_offers_all_follow_workflows() -> None:
    screen = make_screen()
    await screen.on_load()

    rendered = screen.render()

    assert NotificationScreen.session_name == "notifications"
    assert NotificationScreen.visibility == "personal"
    assert {"Follow creator", "Follow record", "Follow matching records"} <= set(labels(rendered))


async def test_following_a_creator_refreshes_the_subscription_browser() -> None:
    screen = make_screen()
    await screen.on_load()
    creator = UUID("11111111-1111-1111-1111-111111111111")
    event = SimpleNamespace(values={"creator": str(creator)}, notice=AsyncMock())

    await screen._follow_creator(cast(sl.SubmitEvent, event))

    cast(Any, screen._notifications).subscribe.assert_awaited_once_with(
        7,
        kind=SubscriptionKind.CREATOR,
        subject_id=creator,
    )
    assert cast(Any, screen._notifications).subscriptions.await_count == 2
    event.notice.assert_awaited_once()


async def test_following_a_record_filter_uses_typed_tag_input() -> None:
    screen = make_screen()
    await screen.on_load()
    event = SimpleNamespace(
        values={
            "build_kind": "door",
            "record_class": None,
            "version_scope": "current",
            "tag": 4,
            "tag_value": "compact",
        },
        notice=AsyncMock(),
    )

    await screen._follow_filter(cast(sl.SubmitEvent, event))

    call = cast(Any, screen._notifications).subscribe.await_args
    assert call.kwargs["kind"] is SubscriptionKind.RECORD_FILTER
    record_filter = call.kwargs["record_filter"]
    assert record_filter.build_kinds == frozenset({"door"})
    assert record_filter.version_scopes == frozenset({"current"})
    assert record_filter.tags[0].tag_id == 4
    assert record_filter.tags[0].value == "compact"


async def test_empty_record_filter_does_not_call_the_service() -> None:
    screen = make_screen()
    await screen.on_load()
    event = SimpleNamespace(
        values={
            "build_kind": None,
            "record_class": None,
            "version_scope": None,
            "tag": None,
            "tag_value": None,
        },
        notice=AsyncMock(),
    )

    await screen._follow_filter(cast(sl.SubmitEvent, event))

    cast(Any, screen._notifications).subscribe.assert_not_awaited()
    event.notice.assert_awaited_once()
