"""Slash-only notification management and durable Discord DM delivery."""

import logging
from typing import TYPE_CHECKING, override
from uuid import UUID

import discord
from discord import app_commands
from discord.ext import commands

from squid.bot.consent import ensure_consented_account
from squid.bot.i18n import resolve_locale, t
from squid.bot.notifications_view import NotificationPanel
from squid.bot.ui import error_layout, info_layout, respond_payload
from squid.bot.utils.autocomplete import autocompletes
from squid.core.i18n import _
from squid.notifications import (
    PendingNotificationDelivery,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)
from squid.runtime import JobHandle
from squid_ui_discord import respond_to

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

    @app_commands.command(name="show", description="Open your notification channels and subscriptions")
    async def show(self, interaction: discord.Interaction) -> None:
        """Open the panel that `status`, `channels`, `list` and `unfollow` used to be."""
        locale = await resolve_locale(interaction, self.bot.services.settings)
        account_id = await self._account_id(interaction)
        if account_id is None:
            return
        component = NotificationPanel(
            notifications=self.bot.services.notifications,
            account_id=account_id,
            author_id=interaction.user.id,
            locale=locale,
        )
        message_root = component.mount(source=interaction)
        await message_root.send(respond_to(interaction, ephemeral=True, wait=True))

    @autocompletes(
        creator="creator_profiles",
        competition="competitions",
        build_kind="build_kinds",
        record_class="record_classes",
        version_scope="version_scopes",
        tag="showcase_tag_ids",
    )
    @app_commands.command(name="follow", description="Follow a creator, a record, or records matching a filter")
    @app_commands.describe(
        creator=app_commands.locale_str(_("A creator profile to follow.")),
        competition=app_commands.locale_str(_("One stable record competition to follow.")),
        build_kind=app_commands.locale_str(_("Follow records of this build kind.")),
        record_class=app_commands.locale_str(_("Follow records of this class.")),
        version_scope=app_commands.locale_str(_("Follow records pinned to this version scope.")),
        tag=app_commands.locale_str(_("Follow records carrying this showcase tag.")),
        tag_value=app_commands.locale_str(_("Require an exact value for that tag.")),
    )
    async def follow(
        self,
        interaction: discord.Interaction,
        creator: str | None = None,
        competition: str | None = None,
        build_kind: str | None = None,
        record_class: str | None = None,
        version_scope: str | None = None,
        tag: int | None = None,
        tag_value: str | None = None,
    ) -> None:
        """Follow one subject, whichever kind of subject it is.

        `follow-creator`, `follow-record` and `follow-records` were three commands for one
        verb, told apart only by which argument you had. Which argument you have still tells
        them apart; it just no longer costs a picker entry each.
        """
        locale = await resolve_locale(interaction, self.bot.services.settings)
        account_id = await self._account_id(interaction)
        if account_id is None:
            return

        filters = (build_kind, record_class, version_scope, tag)
        chosen = [bool(creator), bool(competition), any(value is not None for value in filters)]
        if sum(chosen) != 1:
            await respond_payload(
                interaction,
                error_layout(
                    t(locale, _("Nothing to follow")),
                    t(locale, _("Give exactly one of a creator, a record, or a record filter.")),
                ),
            )
            return

        if creator:
            subscription = await self.bot.services.notifications.subscribe(
                account_id, kind=SubscriptionKind.CREATOR, subject_id=UUID(creator)
            )
            followed = t(locale, _("Following a creator."))
        elif competition:
            subscription = await self.bot.services.notifications.subscribe(
                account_id, kind=SubscriptionKind.RECORD, subject_id=UUID(competition)
            )
            followed = t(locale, _("Following a record."))
        else:
            record_filter = RecordSubscriptionFilter(
                build_kinds=frozenset({build_kind}) if build_kind else frozenset(),
                record_classes=frozenset({record_class}) if record_class else frozenset(),
                version_scopes=frozenset({version_scope}) if version_scope else frozenset(),
                tags=()
                if tag is None
                else (TagPredicate(tag, "present" if tag_value is None else "exact", tag_value),),
            )
            subscription = await self.bot.services.notifications.subscribe(
                account_id, kind=SubscriptionKind.RECORD_FILTER, record_filter=record_filter
            )
            followed = t(locale, _("Following records matching that filter."))
        del subscription  # Nothing user-facing needs its id: `/notifications` lists and removes it.
        await respond_payload(
            interaction,
            info_layout(followed, t(locale, _("Open `/notifications` to see or undo everything you follow."))),
        )

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


async def setup(bot: RedstoneSquid) -> None:
    await bot.add_cog(NotificationCog(bot))
