"""Discord delivery boundary behavior for durable notifications."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import discord
import pytest
from pytest_mock import MockerFixture

from squid.bot.notifications import NotificationCog
from squid.notifications import PendingNotificationDelivery
from squid.notifications.domain import NotificationKind


@dataclass(frozen=True)
class HttpResponse:
    status: int
    reason: str


def _delivery() -> PendingNotificationDelivery:
    return PendingNotificationDelivery(
        id=1,
        generation=1,
        discord_id=2,
        nonce=UUID("11111111-1111-1111-1111-111111111111"),
        claim_token=UUID("22222222-2222-2222-2222-222222222222"),
        attempts=1,
        kind=NotificationKind.BUILD_CONFIRMED,
        payload={"build_id": 42},
    )


def _cog(
    mocker: MockerFixture,
    *,
    send_error: Exception | None = None,
    dead_lettered: bool = False,
) -> tuple[NotificationCog, PendingNotificationDelivery, Any, Any]:
    delivery = _delivery()
    notifications = SimpleNamespace(
        claim_deliveries=mocker.AsyncMock(return_value=(delivery,)),
        complete_delivery=mocker.AsyncMock(),
        fail_delivery=mocker.AsyncMock(return_value=dead_lettered),
        suspend_dm=mocker.AsyncMock(),
    )
    user = SimpleNamespace(send=mocker.AsyncMock(side_effect=send_error))
    bot = SimpleNamespace(
        wait_until_ready=mocker.AsyncMock(),
        services=SimpleNamespace(notifications=notifications),
        get_user=mocker.Mock(return_value=user),
        fetch_user=mocker.AsyncMock(),
        notification_site_url="https://example.test",
    )
    return cast(NotificationCog, SimpleNamespace(bot=bot)), delivery, notifications, user


async def test_delivery_disables_every_allowed_mention_and_completes_the_claim(mocker: MockerFixture) -> None:
    cog, delivery, notifications, user = _cog(mocker)

    await NotificationCog.process_deliveries(cog)

    mentions = user.send.await_args.kwargs["allowed_mentions"]
    assert mentions.everyone is False
    assert mentions.users is False
    assert mentions.roles is False
    assert mentions.replied_user is False
    notifications.complete_delivery.assert_awaited_once_with(delivery)
    notifications.fail_delivery.assert_not_awaited()
    notifications.suspend_dm.assert_not_awaited()


async def test_forbidden_delivery_suspends_dms_without_retrying(mocker: MockerFixture) -> None:
    response = cast(Any, HttpResponse(status=403, reason="Forbidden"))
    forbidden = discord.Forbidden(response, "Cannot send messages to this user")
    cog, delivery, notifications, _user = _cog(mocker, send_error=forbidden)

    await NotificationCog.process_deliveries(cog)

    notifications.suspend_dm.assert_awaited_once_with(delivery, forbidden)
    notifications.fail_delivery.assert_not_awaited()
    notifications.complete_delivery.assert_not_awaited()


@pytest.mark.parametrize("dead_lettered", [False, True])
async def test_ambiguous_delivery_failure_uses_the_durable_retry_boundary(
    mocker: MockerFixture,
    dead_lettered: bool,
) -> None:
    error = RuntimeError("Discord timed out after accepting the send")
    cog, delivery, notifications, _user = _cog(mocker, send_error=error, dead_lettered=dead_lettered)

    await NotificationCog.process_deliveries(cog)

    notifications.fail_delivery.assert_awaited_once_with(delivery, error)
    notifications.suspend_dm.assert_not_awaited()
    notifications.complete_delivery.assert_not_awaited()
