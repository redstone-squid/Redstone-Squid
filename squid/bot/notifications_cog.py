"""Slash-only notification management and durable Discord DM delivery."""

import logging
from typing import TYPE_CHECKING, Any, Self, override

import anyio
import discord

import squid_ui.ext.discord as sd
from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.catalogue.browse import BuildBrowseScreen
from squid.bot.i18n import to_locale_str
from squid.bot.notification_text import notification_build_id, notification_details
from squid.bot.notifications_view import NotificationScreen
from squid.bot.utils.permissions import allows
from squid.core.actors import Actor
from squid.core.i18n import DEFAULT_LOCALE, localization_for, tr
from squid.core.notifications import (
    InboxVisibility,
    PendingNotificationDelivery,
)
from squid.core.notifications.domain import NotificationKind
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_VIEW_PENDING
from squid.runtime import JobHandle
from squid_i18n import discord_text, md_tr, resolve_text
from squid_ui.content.text import Text, localization_scope

logger = logging.getLogger(__name__)

DELIVERY_BATCH_SIZE = 20
DELIVERY_TIMEOUT_SECONDS = 30


if TYPE_CHECKING:
    import squid.bot.app


class NotificationCog(sd.Cog["squid.bot.app.RedstoneSquid"]):
    """Manage notification state and deliver queued DMs without prefix commands."""

    def __init__(self, bot: squid.bot.app.RedstoneSquid) -> None:
        super().__init__(bot)
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

    @sd.command(
        name=to_locale_str(tr(t"command-notifications-name = notifications")),
        description=to_locale_str(
            tr(t"notifications-channel-preferences = Manage notification channels and subscriptions")
        ),
    )
    async def notifications(self, request: sd.Request[Self, squid.bot.app.RedstoneSquid]) -> NotificationScreen | None:
        """Open the notification preferences and subscription workspace."""
        account_id = await self._account_id(request)
        if account_id is None:
            return None

        async def actor() -> Actor:
            return await request.client.actors.resolve(request, refresh=True)

        async def visibility() -> InboxVisibility:
            current = await actor()
            return InboxVisibility(
                include_staff=(
                    await self.bot.services.permissions.check(current, BUILD_SUBMISSION_VIEW_PENDING)
                ).allowed
            )

        return NotificationScreen(
            notifications=self.bot.services.notifications,
            edits=self.bot.services.edits,
            actor=actor,
            account_id=account_id,
            author_id=request.user.id,
            visibility=await self._inbox_visibility(request),
            visibility_resolver=visibility,
        )

    @staticmethod
    async def _inbox_visibility(caller: sd.Request[Any, squid.bot.app.RedstoneSquid]) -> InboxVisibility:
        return InboxVisibility(include_staff=await allows(caller, BUILD_SUBMISSION_VIEW_PENDING))

    async def process_deliveries(self) -> None:
        """Drain a bounded DM batch; retry ambiguous failures and suspend explicit forbiddens."""
        await self.bot.wait_until_ready()
        for _ in range(DELIVERY_BATCH_SIZE):
            # Reserve only work we can send now; queued claims could expire while
            # earlier sends wait on Discord, allowing another worker to send them.
            deliveries = await self.bot.services.notifications.claim_deliveries(limit=1)
            if not deliveries:
                break
            delivery = deliveries[0]
            try:
                with anyio.fail_after(DELIVERY_TIMEOUT_SECONDS):
                    user = self.bot.get_user(delivery.discord_id) or await self.bot.fetch_user(delivery.discord_id)
                    build_id = notification_build_id(delivery.payload)
                    content = render_delivery(delivery, self.bot.notification_site_url)
                    nonce = str(delivery.nonce.int & ((1 << 63) - 1))
                    if build_id is None:
                        await user.send(content, allowed_mentions=discord.AllowedMentions.none(), nonce=nonce)
                    else:
                        view = discord.ui.LayoutView(timeout=None)
                        view.add_item(discord.ui.TextDisplay(content))
                        view.add_item(
                            discord.ui.ActionRow(
                                discord.ui.Button(
                                    label=tr(t"notifications-open-build = Open build").resolve(),
                                    custom_id=BuildBrowseScreen.open_detail.custom_id(build_id=build_id),
                                )
                            )
                        )
                        await user.send(
                            view=view,
                            allowed_mentions=discord.AllowedMentions.none(),
                            nonce=nonce,
                        )
            except discord.Forbidden as error:
                await self.bot.services.notifications.suspend_dm(delivery, error)
            except Exception as error:
                if isinstance(error, TimeoutError):
                    error = TimeoutError("Discord notification delivery timed out")
                dead_lettered = await self.bot.services.notifications.fail_delivery(delivery, error)
                if dead_lettered:
                    logger.exception(
                        "Dead-lettered a Discord notification DM",
                        extra={"squid.notification.delivery_id": delivery.id},
                    )
            else:
                await self.bot.services.notifications.complete_delivery(delivery)

    async def _account_id(self, request: sd.Request[Self, squid.bot.app.RedstoneSquid]) -> int | None:
        """The caller's consented account, or `None` once they have been told why not."""
        return await ensure_consented_account(request, self.bot.services.accounts)


def delivery_message(delivery: PendingNotificationDelivery) -> Text:
    """Build the same contextual message shown in the notification inbox."""
    return notification_details(delivery.kind, delivery.payload)


def render_delivery(
    delivery: PendingNotificationDelivery,
    site_url: str | None,
    *,
    locale: str = DEFAULT_LOCALE,
) -> str:
    """Localize a delivery and append a public build link when one is available.

    Staff-review notifications retain their Discord instructions and do not receive
    a site link, even when `site_url` is configured.
    """
    rendered = discord_text(
        resolve_text(delivery_message(delivery), localization_for(locale)), escape=discord.utils.escape_markdown
    )
    build_id = notification_build_id(delivery.payload)
    if build_id is not None:
        if site_url and delivery.kind is not NotificationKind.STAFF_BUILD_SUBMITTED:
            rendered += f"\n{site_url.rstrip('/')}/builds/{build_id}"
        else:
            with localization_scope(localization_for(locale)):
                rendered += (
                    "\n"
                    + md_tr(
                        t"notifications-open-in-discord = Open it in Discord with `/build browse id:{build_id!s}`."
                    ).resolve()
                )
    with localization_scope(localization_for(locale)):
        return (
            rendered
            + "\n\n"
            + md_tr(t"notifications-manage-hint = View your inbox and manage updates with `/notifications`.").resolve()
        )


async def setup(bot: Any) -> None:
    await bot.add_cog(NotificationCog(bot))
