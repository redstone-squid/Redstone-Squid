"""Slash-only notification management and durable Discord DM delivery."""

import logging
from typing import Any, override

import discord
from discord import app_commands

import squid_ui_discord as sd
from squid.bot.consent import ensure_consented_account
from squid.bot.notifications_view import NotificationScreen
from squid.bot.utils.permissions import allows
from squid.core.i18n import DEFAULT_LOCALE, localization_for, tr
from squid.notifications import (
    InboxVisibility,
    PendingNotificationDelivery,
)
from squid.notifications.domain import NotificationKind
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_VIEW_PENDING
from squid.runtime import JobHandle
from squid_ui.text import Message, localization_scope

logger = logging.getLogger(__name__)


class NotificationCog(sd.Cog[Any]):
    """Manage notification state and deliver queued DMs without prefix commands."""

    bot: Any

    def __init__(self, bot: Any) -> None:
        super().__init__(bot)
        self._delivery_task: JobHandle | None = None

    @override
    async def ui_load(self) -> None:
        self._delivery_task = self.bot.background_tasks.start_periodic(
            self.process_deliveries,
            name="notification-deliveries",
            interval=15,
        )

    @override
    async def ui_unload(self) -> None:
        if self._delivery_task is not None:
            await self.bot.background_tasks.cancel(self._delivery_task)

    @app_commands.command(
        name="notifications",
        description=app_commands.locale_str("Manage notification channels and subscriptions"),
    )
    async def notifications(self, interaction: discord.Interaction[Any]) -> None:
        """Open the notification preferences and subscription workspace."""
        account_id = await self._account_id(interaction)
        if account_id is None:
            return
        await self.ui.respond(
            interaction,
            NotificationScreen(
                notifications=self.bot.services.notifications,
                account_id=account_id,
                author_id=interaction.user.id,
                visibility=await self._inbox_visibility(interaction),
                visibility_resolver=lambda event: self._inbox_visibility(sd.native(event)),
            ),
        )

    @staticmethod
    async def _inbox_visibility(interaction: discord.Interaction[Any]) -> InboxVisibility:
        return InboxVisibility(include_staff=await allows(interaction, BUILD_SUBMISSION_VIEW_PENDING))

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
        return await ensure_consented_account(await sd.request(interaction), self.bot.services.accounts)


def delivery_message(delivery: PendingNotificationDelivery) -> Message:
    """Build deferred DM text from a materialized notification payload."""
    build_id = delivery.payload.get("build_id")
    if delivery.kind is NotificationKind.STAFF_BUILD_SUBMITTED:
        if isinstance(build_id, int):
            return tr(t"A new build is awaiting staff review.\nOpen it in Discord with `/build browse id:{build_id}`.")
        return tr(t"A new build is awaiting staff review.")
    if delivery.kind is NotificationKind.RECORD_GAINED:
        raw_records = delivery.payload.get("records", [])
        count = len(raw_records) if isinstance(raw_records, list) else 1
        return tr(
            t"A credited build gained {count} record.",
            plural=t"A credited build gained {count} records.",
        )
    if delivery.kind is NotificationKind.BUILD_CONFIRMED:
        return tr(t"Your build was confirmed.")
    if delivery.kind is NotificationKind.BUILD_DENIED:
        return tr(t"Your build was denied.")
    if delivery.kind is NotificationKind.CREATOR_BUILD_CONFIRMED:
        return tr(t"A creator you follow has a newly confirmed build.")
    return tr(t"A build notification is available.")


def render_delivery(
    delivery: PendingNotificationDelivery,
    site_url: str | None,
    *,
    locale: str = DEFAULT_LOCALE,
) -> str:
    """Localize transport-safe DM text when it is delivered."""
    with localization_scope(localization_for(locale)):
        rendered = tr(delivery_message(delivery))
    build_id = delivery.payload.get("build_id")
    if delivery.kind is NotificationKind.STAFF_BUILD_SUBMITTED:
        return rendered
    build_link = f"{site_url}/builds/{build_id}" if site_url is not None and isinstance(build_id, int) else None
    return f"{rendered}\n{build_link}" if build_link is not None else rendered


async def setup(bot: Any) -> None:
    await bot.add_cog(NotificationCog(bot))
