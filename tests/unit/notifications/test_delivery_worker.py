"""Discord delivery boundary behavior for durable notifications."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Literal, cast
from unittest.mock import AsyncMock
from uuid import UUID

import anyio
import discord
import pytest
from aiohttp import ClientResponse
from pytest_mock import MockerFixture

import squid_ui.ext.discord as sd
from squid.bot import notifications_cog as notification_module
from squid.bot.app import RedstoneSquid
from squid.bot.notifications_cog import NotificationCog
from squid.core.notifications import NotificationService, PendingNotificationDelivery
from squid.core.notifications.application import NotificationRepository
from squid.core.notifications.domain import NotificationKind
from tests.support.delivery_uow import NotificationUnitOfWork
from tests.support.services import bot_services


@dataclass
class DeliveryRepository:
    """Track one in-memory claim and its outcome; no retry timing or database locking is modeled."""

    delivery: PendingNotificationDelivery
    status: Literal["pending", "claimed", "completed", "failed", "suspended"] = "pending"
    error: str | None = None
    dead_lettered: bool = False

    async def claim_deliveries(self, *, limit: int) -> tuple[PendingNotificationDelivery, ...]:
        assert limit > 0
        if self.status != "pending":
            return ()
        self.status = "claimed"
        return (self.delivery,)

    def _settle(
        self,
        delivery: PendingNotificationDelivery,
        status: Literal["completed", "failed", "suspended"],
        error: str | None = None,
    ) -> bool:
        assert delivery == self.delivery
        assert self.status == "claimed", "a claim must have exactly one outcome"
        self.status, self.error = status, error
        return True

    async def complete_delivery(self, delivery: PendingNotificationDelivery) -> bool:
        return self._settle(delivery, "completed")

    async def fail_delivery(self, delivery: PendingNotificationDelivery, error: str, *, max_attempts: int) -> bool:
        assert max_attempts == 8
        self._settle(delivery, "failed", error)
        return self.dead_lettered

    async def suspend_dm(self, delivery: PendingNotificationDelivery, error: str) -> bool:
        return self._settle(delivery, "suspended", error)


@dataclass
class DeliveryWorker:
    cog: NotificationCog
    repository: DeliveryRepository
    send: AsyncMock


@pytest.fixture
async def worker(mocker: MockerFixture) -> AsyncIterator[DeliveryWorker]:
    repository = DeliveryRepository(
        PendingNotificationDelivery(
            id=1,
            generation=1,
            discord_id=2,
            nonce=UUID("11111111-1111-1111-1111-111111111111"),
            claim_token=UUID("22222222-2222-2222-2222-222222222222"),
            attempts=1,
            kind=NotificationKind.BUILD_CONFIRMED,
            payload={"build_id": 42},
        )
    )
    async with discord.Client(intents=discord.Intents.none()) as client:
        runtime = sd.install(client)
        # Bypass gateway readiness and seed a mock user whose send method records delivery.
        # The cog, UI scope, and notification service are real; no network send occurs.
        client._ready.set()  # pyright: ignore[reportPrivateUsage]
        user = mocker.Mock(spec=discord.User, id=repository.delivery.discord_id)
        client._connection._users[user.id] = user  # pyright: ignore[reportPrivateUsage]
        mocker.patch.object(
            client,
            "services",
            bot_services(
                notifications=NotificationService(
                    uow_factory=lambda: NotificationUnitOfWork(cast(NotificationRepository, repository))
                )
            ),
            create=True,
        )
        mocker.patch.object(client, "notification_site_url", "https://example.test", create=True)
        try:
            yield DeliveryWorker(NotificationCog(cast(RedstoneSquid, client)), repository, user.send)
        finally:
            await runtime.close()


async def test_delivery_disables_mentions_and_completes_the_claim_once(worker: DeliveryWorker) -> None:
    await worker.cog.process_deliveries()
    await worker.cog.process_deliveries()

    worker.send.assert_awaited_once()
    assert worker.send.await_args is not None
    assert worker.send.await_args.args == ()
    view = worker.send.await_args.kwargs["view"]
    assert isinstance(view, discord.ui.LayoutView)
    text = view.children[0]
    assert isinstance(text, discord.ui.TextDisplay)
    assert text.content == (
        "Build #42 was approved.\nhttps://example.test/builds/42\n\n"
        "View your inbox and manage updates with `/notifications`."
    )
    assert worker.send.await_args.kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert worker.send.await_args.kwargs["nonce"] == "1229782938247303441"
    assert worker.repository.status == "completed"
    assert worker.repository.error is None


async def test_forbidden_delivery_suspends_dms_without_retrying(worker: DeliveryWorker, mocker: MockerFixture) -> None:
    response = mocker.Mock(spec=ClientResponse, status=403, reason="Forbidden")
    forbidden = discord.Forbidden(response, "Cannot send messages to this user")
    worker.send.side_effect = forbidden

    await worker.cog.process_deliveries()

    assert worker.repository.status == "suspended"
    assert worker.repository.error == str(forbidden)


@pytest.mark.parametrize("dead_lettered", [False, True])
async def test_ambiguous_delivery_failure_is_retried_and_only_dead_letters_are_logged(
    worker: DeliveryWorker, dead_lettered: bool, caplog: pytest.LogCaptureFixture
) -> None:
    error = RuntimeError("Discord timed out after accepting the send")
    worker.repository.dead_lettered = dead_lettered
    worker.send.side_effect = error

    await worker.cog.process_deliveries()

    assert worker.repository.status == "failed"
    assert worker.repository.error == str(error)
    records = [record for record in caplog.records if record.name == "squid.bot.notifications_cog"]
    assert len(records) == int(dead_lettered)
    if dead_lettered:
        assert records[0].__dict__["squid.notification.delivery_id"] == worker.repository.delivery.id
        assert records[0].exc_info is not None
        assert records[0].exc_info[1] is error


async def test_delivery_claims_only_after_previous_send_settles(worker: DeliveryWorker, mocker: MockerFixture) -> None:
    attempts = 0

    async def claim(*, limit: int) -> tuple[PendingNotificationDelivery, ...]:
        nonlocal attempts
        assert limit == 1
        if attempts:
            assert worker.repository.status == "completed"
        if attempts == 2:
            return ()
        attempts += 1
        worker.repository.status = "claimed"
        return (worker.repository.delivery,)

    mocker.patch.object(worker.cog.bot.services.notifications, "claim_deliveries", side_effect=claim)

    await worker.cog.process_deliveries()

    assert attempts == 2
    assert worker.send.await_count == 2


async def test_delivery_stops_at_batch_limit(worker: DeliveryWorker, mocker: MockerFixture) -> None:
    async def claim(*, limit: int) -> tuple[PendingNotificationDelivery, ...]:
        assert limit == 1
        worker.repository.status = "claimed"
        return (worker.repository.delivery,)

    claims = mocker.patch.object(worker.cog.bot.services.notifications, "claim_deliveries", side_effect=claim)

    await worker.cog.process_deliveries()

    assert claims.await_count == notification_module.DELIVERY_BATCH_SIZE
    assert worker.send.await_count == notification_module.DELIVERY_BATCH_SIZE


@pytest.mark.parametrize("during_lookup", [False, True])
async def test_stalled_discord_request_is_retried(
    worker: DeliveryWorker, mocker: MockerFixture, during_lookup: bool
) -> None:
    async def stall(*args: object, **kwargs: object) -> None:
        await anyio.sleep_forever()

    mocker.patch.object(notification_module, "DELIVERY_TIMEOUT_SECONDS", 0.01)
    if during_lookup:
        mocker.patch.object(worker.cog.bot, "get_user", return_value=None)
        mocker.patch.object(worker.cog.bot, "fetch_user", side_effect=stall)
    else:
        worker.send.side_effect = stall

    await worker.cog.process_deliveries()

    assert worker.repository.status == "failed"
    assert worker.repository.error == "Discord notification delivery timed out"


async def test_shutdown_does_not_count_as_delivery_failure(worker: DeliveryWorker) -> None:
    with anyio.CancelScope() as scope:

        async def cancel(*args: object, **kwargs: object) -> None:
            scope.cancel()
            await anyio.sleep_forever()

        worker.send.side_effect = cancel
        await worker.cog.process_deliveries()

    assert scope.cancelled_caught
    assert worker.repository.status == "claimed"
    assert worker.repository.error is None


async def test_staff_delivery_carries_build_into_routed_button(worker: DeliveryWorker) -> None:
    worker.repository.delivery = replace(worker.repository.delivery, kind=NotificationKind.STAFF_BUILD_SUBMITTED)
    await worker.cog.process_deliveries()
    assert worker.send.await_args is not None
    view = worker.send.await_args.kwargs["view"]
    assert isinstance(view, discord.ui.LayoutView)
    text = view.children[0]
    assert isinstance(text, discord.ui.TextDisplay)
    assert text.content == notification_module.render_delivery(
        worker.repository.delivery, worker.cog.bot.notification_site_url
    )
    row = view.children[1]
    assert isinstance(row, discord.ui.ActionRow)
    button = row.children[0]
    assert isinstance(button, discord.ui.Button)
    assert button.label == "Open build"
    assert button.custom_id == notification_module.BuildBrowseScreen.open_detail.custom_id(build_id=42)
