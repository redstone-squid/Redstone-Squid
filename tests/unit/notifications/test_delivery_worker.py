"""Discord delivery boundary behavior for durable notifications."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
import pytest
from aiohttp import ClientResponse
from pytest_mock import MockerFixture

import squid_ui_discord as sd
from squid.bot.notifications import NotificationCog
from squid.notifications import NotificationService, PendingNotificationDelivery
from squid.notifications.application import NotificationRepository
from squid.notifications.domain import NotificationKind


@dataclass
class DeliveryRepository:
    """One claimed delivery and its persisted outcome."""

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
        # Discord's gateway owns readiness and user caching in production. Only the DM send
        # crosses the network here; the cog, UI scope, and notification service are real.
        client._ready.set()  # pyright: ignore[reportPrivateUsage]
        user = mocker.Mock(spec=discord.User, id=repository.delivery.discord_id)
        client._connection._users[user.id] = user  # pyright: ignore[reportPrivateUsage]
        bot = SimpleNamespace(
            ui=runtime,
            services=SimpleNamespace(notifications=NotificationService(cast(NotificationRepository, repository))),
            wait_until_ready=client.wait_until_ready,
            get_user=client.get_user,
            fetch_user=client.fetch_user,
            notification_site_url="https://example.test",
        )
        try:
            yield DeliveryWorker(NotificationCog(bot), repository, user.send)
        finally:
            await runtime.close()


async def test_delivery_disables_mentions_and_completes_the_claim_once(worker: DeliveryWorker) -> None:
    await worker.cog.process_deliveries()
    await worker.cog.process_deliveries()

    worker.send.assert_awaited_once()
    assert worker.send.await_args is not None
    assert worker.send.await_args.args == ("Your build was confirmed.\nhttps://example.test/builds/42",)
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
    records = [record for record in caplog.records if record.name == "squid.bot.notifications"]
    assert len(records) == int(dead_lettered)
    if dead_lettered:
        assert records[0].__dict__["squid.notification.delivery_id"] == worker.repository.delivery.id
        assert records[0].exc_info is not None
        assert records[0].exc_info[1] is error
