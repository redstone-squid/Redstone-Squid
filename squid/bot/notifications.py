"""Slash-only notification management and durable Discord DM delivery."""

import asyncio
import logging
from typing import TYPE_CHECKING, override
from uuid import UUID

import discord
from discord import app_commands
from discord.ext import commands

from squid.notifications import (
    NotificationSubscription,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid

logger = logging.getLogger(__name__)


class NotificationCog(commands.GroupCog, group_name="notifications", group_description="Manage notification opt-ins"):
    """Manage notification state and deliver queued DMs without prefix commands."""

    def __init__(self, bot: "RedstoneSquid") -> None:
        self.bot = bot
        self._delivery_task: asyncio.Task[None] | None = None

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

    @app_commands.command(name="status", description="Show your notification consent and channels")
    async def status(self, interaction: discord.Interaction) -> None:
        account = await self.bot.services.users.get_or_create_account(interaction.user.id)
        assert account.id is not None
        preferences = await self.bot.services.notifications.preferences(account.id)
        await interaction.response.send_message(
            "Notification notice: "
            f"{'accepted' if preferences.has_current_consent else 'not accepted'}\n"
            f"Web inbox: {'on' if preferences.web_enabled else 'off'}\n"
            f"Discord DMs: {'on' if preferences.dm_enabled else 'off'}"
            + (" (suspended after Discord rejected a DM)" if preferences.dm_suspended_at is not None else ""),
            ephemeral=True,
        )

    @app_commands.command(name="consent", description="Accept the notification notice and choose channels")
    async def consent(self, interaction: discord.Interaction, web: bool = False, dm: bool = False) -> None:
        account = await self.bot.services.users.get_or_create_account(interaction.user.id)
        assert account.id is not None
        await self.bot.services.notifications.accept_notice(account.id, web_enabled=web, dm_enabled=dm)
        await interaction.response.send_message("Notification preferences saved.", ephemeral=True)

    @app_commands.command(name="channels", description="Change web and Discord DM channels")
    async def channels(self, interaction: discord.Interaction, web: bool, dm: bool) -> None:
        account = await self.bot.services.users.get_or_create_account(interaction.user.id)
        assert account.id is not None
        await self.bot.services.notifications.set_preferences(account.id, web_enabled=web, dm_enabled=dm)
        await interaction.response.send_message("Notification channels updated.", ephemeral=True)

    @app_commands.command(name="follow-creator", description="Follow a public creator profile UUID")
    async def follow_creator(self, interaction: discord.Interaction, creator_id: str) -> None:
        account_id = await self._account_id(interaction)
        subscription = await self.bot.services.notifications.subscribe(
            account_id,
            kind=SubscriptionKind.CREATOR,
            subject_id=UUID(creator_id),
        )
        await interaction.response.send_message(f"Creator subscription #{subscription.id} saved.", ephemeral=True)

    @app_commands.command(name="follow-record", description="Follow one stable record competition UUID")
    async def follow_record(self, interaction: discord.Interaction, competition_id: str) -> None:
        account_id = await self._account_id(interaction)
        subscription = await self.bot.services.notifications.subscribe(
            account_id,
            kind=SubscriptionKind.RECORD,
            subject_id=UUID(competition_id),
        )
        await interaction.response.send_message(f"Record subscription #{subscription.id} saved.", ephemeral=True)

    @app_commands.command(name="follow-records", description="Follow records matching broad structured predicates")
    async def follow_records(
        self,
        interaction: discord.Interaction,
        build_kind: str | None = None,
        record_class: str | None = None,
        version_scope: str | None = None,
        tag_id: int | None = None,
        tag_value: str | None = None,
    ) -> None:
        account_id = await self._account_id(interaction)
        tags = ()
        if tag_id is not None:
            tags = (TagPredicate(tag_id, "present" if tag_value is None else "exact", tag_value),)
        record_filter = RecordSubscriptionFilter(
            build_kinds=frozenset({build_kind}) if build_kind else frozenset(),
            record_classes=frozenset({record_class}) if record_class else frozenset(),
            version_scopes=frozenset({version_scope}) if version_scope else frozenset(),
            tags=tags,
        )
        subscription = await self.bot.services.notifications.subscribe(
            account_id,
            kind=SubscriptionKind.RECORD_FILTER,
            record_filter=record_filter,
        )
        await interaction.response.send_message(f"Record filter #{subscription.id} saved.", ephemeral=True)

    @app_commands.command(name="list", description="List your notification subscriptions")
    async def list_subscriptions(self, interaction: discord.Interaction) -> None:
        subscriptions = await self.bot.services.notifications.subscriptions(await self._account_id(interaction))
        content = "\n".join(
            f"#{subscription.id}: {subscription.kind.value} {_subscription_target(subscription)}"
            for subscription in subscriptions
        )
        await interaction.response.send_message(content or "You have no subscriptions.", ephemeral=True)

    @app_commands.command(name="unfollow", description="Remove one notification subscription")
    async def unfollow(self, interaction: discord.Interaction, subscription_id: int) -> None:
        await self.bot.services.notifications.unsubscribe(await self._account_id(interaction), subscription_id)
        await interaction.response.send_message("Subscription removed.", ephemeral=True)

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

    async def _account_id(self, interaction: discord.Interaction) -> int:
        account = await self.bot.services.users.get_or_create_account(interaction.user.id)
        assert account.id is not None
        return account.id


def render_delivery(delivery: PendingNotificationDelivery, site_url: str | None) -> str:
    """Render transport-safe DM text from a materialized notification payload."""
    build_id = delivery.payload.get("build_id")
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
        message = "A new build is awaiting staff review."
    return f"{message}\n{build_link}" if build_link is not None else message


def _subscription_target(subscription: NotificationSubscription) -> str:
    if subscription.subject_id is not None:
        return str(subscription.subject_id)
    return str(subscription.record_filter.as_dict()) if subscription.record_filter is not None else ""


async def setup(bot: "RedstoneSquid") -> None:
    await bot.add_cog(NotificationCog(bot))
