"""Slash-only notification management and durable Discord DM delivery."""

import logging
from typing import TYPE_CHECKING, override
from uuid import UUID

import discord
from discord import app_commands
from discord.ext import commands

from squid.bot.consent import ensure_consented_account
from squid.bot.i18n import resolve_locale, t
from squid.bot.utils.autocomplete import autocompletes
from squid.core.i18n import _
from squid.notifications import (
    NotificationSubscription,
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)
from squid.runtime import JobHandle

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid

logger = logging.getLogger(__name__)


class NotificationCog(commands.GroupCog, group_name="notifications", group_description="Manage notification opt-ins"):
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

    @app_commands.command(name="status", description="Show your notification channels")
    async def status(self, interaction: discord.Interaction) -> None:
        locale = await resolve_locale(interaction, self.bot.services.settings)
        account_id = await self._account_id(interaction)
        if account_id is None:
            return
        preferences = await self.bot.services.notifications.preferences(account_id)
        lines = [
            t(
                locale,
                _("Web inbox: {state}"),
                state=t(locale, _("on")) if preferences.web_enabled else t(locale, _("off")),
            ),
            t(
                locale,
                _("Discord DMs: {state}"),
                state=t(locale, _("on")) if preferences.dm_enabled else t(locale, _("off")),
            ),
        ]
        if preferences.dm_suspended_at is not None:
            lines.append(t(locale, _("DMs are suspended because Discord rejected one.")))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="channels", description="Change web and Discord DM channels")
    async def channels(self, interaction: discord.Interaction, web: bool, dm: bool) -> None:
        locale = await resolve_locale(interaction, self.bot.services.settings)
        account_id = await self._account_id(interaction)
        if account_id is None:
            return
        await self.bot.services.notifications.set_preferences(account_id, web_enabled=web, dm_enabled=dm)
        await interaction.response.send_message(t(locale, _("Notification channels updated.")), ephemeral=True)

    @autocompletes(creator_id="creator_profiles")
    @app_commands.command(name="follow-creator", description="Follow a public creator profile UUID")
    async def follow_creator(self, interaction: discord.Interaction, creator_id: str) -> None:
        locale = await resolve_locale(interaction, self.bot.services.settings)
        account_id = await self._account_id(interaction)
        if account_id is None:
            return
        subscription = await self.bot.services.notifications.subscribe(
            account_id,
            kind=SubscriptionKind.CREATOR,
            subject_id=UUID(creator_id),
        )
        await interaction.response.send_message(
            t(locale, _("Creator subscription #{id} saved."), id=subscription.id), ephemeral=True
        )

    @autocompletes(competition_id="competitions")
    @app_commands.command(name="follow-record", description="Follow one stable record competition UUID")
    async def follow_record(self, interaction: discord.Interaction, competition_id: str) -> None:
        locale = await resolve_locale(interaction, self.bot.services.settings)
        account_id = await self._account_id(interaction)
        if account_id is None:
            return
        subscription = await self.bot.services.notifications.subscribe(
            account_id,
            kind=SubscriptionKind.RECORD,
            subject_id=UUID(competition_id),
        )
        await interaction.response.send_message(
            t(locale, _("Record subscription #{id} saved."), id=subscription.id), ephemeral=True
        )

    @autocompletes(
        build_kind="build_kinds",
        record_class="record_classes",
        version_scope="version_scopes",
        tag_id="showcase_tag_ids",
    )
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
        locale = await resolve_locale(interaction, self.bot.services.settings)
        account_id = await self._account_id(interaction)
        if account_id is None:
            return
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
        await interaction.response.send_message(
            t(locale, _("Record filter #{id} saved."), id=subscription.id), ephemeral=True
        )

    @app_commands.command(name="list", description="List your notification subscriptions")
    async def list_subscriptions(self, interaction: discord.Interaction) -> None:
        locale = await resolve_locale(interaction, self.bot.services.settings)
        account_id = await self._account_id(interaction)
        if account_id is None:
            return
        subscriptions = await self.bot.services.notifications.subscriptions(account_id)
        content = "\n".join(
            f"#{subscription.id}: {subscription.kind.value} {_subscription_target(subscription)}"
            for subscription in subscriptions
        )
        await interaction.response.send_message(content or t(locale, _("You have no subscriptions.")), ephemeral=True)

    @autocompletes(subscription_id="notification_subscriptions")
    @app_commands.command(name="unfollow", description="Remove one notification subscription")
    async def unfollow(self, interaction: discord.Interaction, subscription_id: int) -> None:
        locale = await resolve_locale(interaction, self.bot.services.settings)
        account_id = await self._account_id(interaction)
        if account_id is None:
            return
        await self.bot.services.notifications.unsubscribe(account_id, subscription_id)
        await interaction.response.send_message(t(locale, _("Subscription removed.")), ephemeral=True)

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
        locale = await resolve_locale(interaction, self.bot.services.settings)
        return await ensure_consented_account(interaction, self.bot.services.accounts, locale=locale)


def render_delivery(delivery: PendingNotificationDelivery, site_url: str | None) -> str:
    """Render transport-safe DM text from a materialized notification payload."""
    build_id = delivery.payload.get("build_id")
    if delivery.kind.value == "staff_build_submitted":
        message = "A new build is awaiting staff review."
        if isinstance(build_id, int):
            return f"{message}\nOpen it in Discord with `/build view id:{build_id}`."
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


def _subscription_target(subscription: NotificationSubscription) -> str:
    if subscription.subject_id is not None:
        return str(subscription.subject_id)
    return str(subscription.record_filter.as_dict()) if subscription.record_filter is not None else ""


async def setup(bot: RedstoneSquid) -> None:
    await bot.add_cog(NotificationCog(bot))
