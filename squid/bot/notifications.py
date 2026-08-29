"""Slash-only notification management and durable Discord DM delivery."""

import logging
from typing import TYPE_CHECKING, override

import discord
from discord import app_commands
from discord.ext import commands

from squid.bot.consent import ensure_consented_account
from squid.bot.notifications_view import NotificationScreen
from squid.notifications import (
    PendingNotificationDelivery,
)
from squid.runtime import JobHandle

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid

logger = logging.getLogger(__name__)


class NotificationCog(commands.Cog):
    """Manage notification state and deliver queued DMs without prefix commands."""

    def __init__(self, bot: RedstoneSquid) -> None:
        self.bot = bot
        self._delivery_task: JobHandle | None = None

    @override
    async def cog_load(self) -> None:
        self._delivery_task = self.bot.background_tasks.start_periodic(
            self.process_deliveries,
            name="notification-deliveries",
            interval=15,
        )

    @override
    async def cog_unload(self) -> None:
        if self._delivery_task is not None:
            await self.bot.background_tasks.cancel(self._delivery_task)

    @app_commands.command(name="notifications", description="Manage notification channels and subscriptions")
    async def notifications(self, interaction: discord.Interaction) -> None:
        """Open the notification preferences and subscription workspace."""
        account_id = await self._account_id(interaction)
        if account_id is None:
            return
        await NotificationScreen(
            notifications=self.bot.services.notifications,
            account_id=account_id,
            author_id=interaction.user.id,
        ).show(interaction)

    async def process_deliveries(self) -> None:
        """Drain a bounded DM batch; retry ambiguous failures and suspend explicit forbiddens."""
        await self.bot.wait_until_ready()
        for delivery in await self.bot.services.notifications.claim_deliveries():
            try:
                user = self.bot.get_user(delivery.discord_id) or await self.bot.fetch_user(delivery.discord_id)
                await user.send(
                    render_delivery(delivery, self.bot.notification_site_url),
                    allowed_mentions=discord.AllowedMentions.none(),
                    nonce=str(delivery.nonce.int & ((1 << 63) - 1)),
                )
            except discord.Forbidden as error:
                await self.bot.services.notifications.suspend_dm(delivery, error)
            except Exception as error:
                dead_lettered = await self.bot.services.notifications.fail_delivery(delivery, error)
                if dead_lettered:
                    logger.exception(
                        "Dead-lettered a Discord notification DM",
                        extra={"squid.notification.delivery_id": delivery.id},
                    )
            else:
                await self.bot.services.notifications.complete_delivery(delivery)

    async def _account_id(self, interaction: discord.Interaction) -> int | None:
        """The caller's consented account, or `None` once they have been told why not."""
        return await ensure_consented_account(interaction, self.bot.services.accounts)


def render_delivery(delivery: PendingNotificationDelivery, site_url: str | None) -> str:
    """Render transport-safe DM text from a materialized notification payload."""
    build_id = delivery.payload.get("build_id")
    if delivery.kind.value == "staff_build_submitted":
        message = "A new build is awaiting staff review."
        if isinstance(build_id, int):
            return f"{message}\nOpen it in Discord with `/build browse id:{build_id}`."
        return message
    build_link = f"{site_url}/builds/{build_id}" if site_url is not None and isinstance(build_id, int) else None
    if delivery.kind.value == "record_gained":
        raw_records = delivery.payload.get("records", [])
        count = len(raw_records) if isinstance(raw_records, list) else 1
        message = f"A credited build gained {count} record{'s' if count != 1 else ''}."
    elif delivery.kind.value == "build_confirmed":
        message = "Your build was confirmed."
    elif delivery.kind.value == "build_denied":
        message = "Your build was denied."
    elif delivery.kind.value == "creator_build_confirmed":
        message = "A creator you follow has a newly confirmed build."
    else:
        message = "A build notification is available."
    return f"{message}\n{build_link}" if build_link is not None else message


async def setup(bot: RedstoneSquid) -> None:
    await bot.add_cog(NotificationCog(bot))
