"""The panel that replaced `status`, `channels`, `list` and `unfollow`."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
from whenever import Instant

from squid.bot.notifications_view import NotificationPanelView, UnfollowSelect
from squid.notifications import (
    NotificationPreferences,
    NotificationSubscription,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)

ACCOUNT_ID = 5
AUTHOR_ID = 11
SUBJECT_ID = UUID(int=1)


def subscription(
    subscription_id: int,
    *,
    kind: SubscriptionKind = SubscriptionKind.CREATOR,
    subject_id: UUID | None = SUBJECT_ID,
    record_filter: RecordSubscriptionFilter | None = None,
) -> NotificationSubscription:
    return NotificationSubscription(
        id=subscription_id,
        account_id=ACCOUNT_ID,
        kind=kind,
        subject_id=subject_id,
        record_filter=record_filter,
        created_at=Instant.now(),
    )


async def make_panel(
    *,
    web: bool = False,
    dm: bool = False,
    subscriptions: tuple[NotificationSubscription, ...] = (),
) -> tuple[NotificationPanelView, Any]:
    preferences = NotificationPreferences(ACCOUNT_ID, consent_pending=False, web_enabled=web, dm_enabled=dm)
    service = SimpleNamespace(
        preferences=AsyncMock(return_value=preferences),
        subscriptions=AsyncMock(return_value=subscriptions),
        set_preferences=AsyncMock(return_value=preferences),
        unsubscribe=AsyncMock(),
    )
    panel = NotificationPanelView(
        notifications=cast(Any, service),
        account_id=ACCOUNT_ID,
        author_id=AUTHOR_ID,
    )
    await panel.load()
    return panel, service


def _select(panel: NotificationPanelView) -> UnfollowSelect | None:
    return next((child for child in panel.walk_children() if isinstance(child, UnfollowSelect)), None)


async def test_the_panel_offers_no_unfollow_picker_when_nothing_is_followed() -> None:
    panel, _service = await make_panel()

    assert _select(panel) is None


async def test_every_subscription_is_removable_without_reading_its_id_back() -> None:
    """`unfollow` took an id you had to read off `list` and type into a second command.

    The select carries the ids, so the id never becomes something a person handles.
    """
    panel, service = await make_panel(subscriptions=(subscription(3), subscription(9)))
    picker = _select(panel)
    assert picker is not None

    assert [option.value for option in picker.options] == ["3", "9"]
    assert picker.max_values == 2

    await panel.unfollow([3, 9])

    assert [call.args for call in service.unsubscribe.await_args_list] == [(ACCOUNT_ID, 3), (ACCOUNT_ID, 9)]


async def test_toggling_one_channel_leaves_the_other_alone() -> None:
    """The service writes both switches at once, so a toggle has to resend the other."""
    panel, service = await make_panel(web=True, dm=False)

    await panel.set_channels(web=panel.web_enabled, dm=not panel.dm_enabled)

    service.set_preferences.assert_awaited_once_with(ACCOUNT_ID, web_enabled=True, dm_enabled=True)


async def test_a_record_filter_reads_as_its_predicates() -> None:
    """`list` printed `str(filter.as_dict())`, dict braces and all (audit C5)."""
    record_filter = RecordSubscriptionFilter(
        build_kinds=frozenset({"door"}),
        record_classes=frozenset({"smallest"}),
        tags=(TagPredicate(4, "exact", "glass"),),
    )
    panel, _service = await make_panel(
        subscriptions=(
            subscription(1, kind=SubscriptionKind.RECORD_FILTER, subject_id=None, record_filter=record_filter),
        )
    )

    assert panel.detail(panel.subscriptions[0]) == "door · smallest · tag 4=glass"


async def test_the_panel_belongs_to_whoever_opened_it() -> None:
    panel, _service = await make_panel()
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=AUTHOR_ID + 1),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    assert await panel.interaction_check(cast(discord.Interaction[Any], interaction)) is False
