"""The panel behind `/notifications`.

Four commands used to answer what one screen shows: `status` read the two channels,
`channels` wrote both, `list` printed the subscriptions, and `unfollow` took an id you had
to read off `list` and type back. A subscription is a thing you look at and then remove, so
looking at it and removing it belong to the same message (audit C5's retyping half).
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, override

import discord

import squid_layouts as sl
from squid.bot.errors import ExpiringLayoutView
from squid.bot.i18n import t
from squid.bot.ui import DISCORD_BLUE, create_mount
from squid.bot.utils.components import CardField, card_container, edit_interaction_layout, no_mentions
from squid.core.i18n import _
from squid.notifications import (
    NotificationPreferences,
    NotificationSubscription,
    RecordSubscriptionFilter,
    SubscriptionKind,
)

if TYPE_CHECKING:
    from squid.notifications.application import NotificationService

SESSION_SECONDS = 300

MAX_LISTED = 25
"""A select holds 25 options, which is also as many as a card should list."""

KIND_LABELS = {
    SubscriptionKind.CREATOR: _("Creator"),
    SubscriptionKind.RECORD: _("Record"),
    SubscriptionKind.RECORD_FILTER: _("Record filter"),
}


class NotificationPanelView(ExpiringLayoutView):
    """Both delivery channels and every subscription, on one ephemeral screen.

    Holds the service rather than a snapshot, like the settings panel and for the same
    reason: the panel exists to write, and every write has to show its result.
    """

    def __init__(
        self,
        *,
        notifications: NotificationService,
        account_id: int,
        author_id: int,
        locale: str | None = None,
    ) -> None:
        super().__init__(timeout=SESSION_SECONDS)
        self._notifications = notifications
        self._account_id = account_id
        self._author_id = author_id
        self.locale = locale
        self._preferences: NotificationPreferences | None = None
        self._subscriptions: tuple[NotificationSubscription, ...] = ()

    async def load(self) -> None:
        """Read the caller's preferences and subscriptions, then render."""
        self._preferences = await self._notifications.preferences(self._account_id)
        self._subscriptions = tuple(await self._notifications.subscriptions(self._account_id))
        self.render()

    @override
    async def interaction_check(self, interaction: discord.Interaction[discord.Client], /) -> bool:
        if interaction.user.id == self._author_id:
            return True
        await interaction.response.send_message(
            t(self.locale, _("These notification controls belong to someone else.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    @property
    def web_enabled(self) -> bool:
        return self._preferences is not None and self._preferences.web_enabled

    @property
    def dm_enabled(self) -> bool:
        return self._preferences is not None and self._preferences.dm_enabled

    @property
    def subscriptions(self) -> tuple[NotificationSubscription, ...]:
        """The listed subscriptions, capped at what one select can offer."""
        return self._subscriptions[:MAX_LISTED]

    def render(self) -> None:
        self.clear_items()
        self.add_item(
            card_container(
                t(self.locale, _("Notifications")),
                t(self.locale, _("Toggle where notifications arrive, and unfollow what you no longer want.")),
                fields=self._fields(),
                footer=self._suspension_note(),
            )
        )
        if self.subscriptions:
            self.add_item(discord.ui.ActionRow(UnfollowSelect(self)))
        self.add_item(discord.ui.ActionRow(WebInboxButton(self), DiscordDMButton(self), ClosePanelButton(self)))

    async def set_channels(self, *, web: bool, dm: bool) -> None:
        """Write both delivery channels; the service takes them together."""
        self._preferences = await self._notifications.set_preferences(self._account_id, web_enabled=web, dm_enabled=dm)
        self.render()

    async def unfollow(self, subscription_ids: Sequence[int]) -> None:
        """Drop the selected subscriptions and show what is left."""
        for subscription_id in subscription_ids:
            await self._notifications.unsubscribe(self._account_id, subscription_id)
        await self.load()

    def disable_controls(self) -> None:
        for child in self.walk_children():
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                child.disabled = True
        self.stop()

    def _fields(self) -> list[CardField]:
        on, off = t(self.locale, _("On")), t(self.locale, _("Off"))
        return [
            CardField(t(self.locale, _("Web inbox")), on if self.web_enabled else off),
            CardField(t(self.locale, _("Discord DMs")), on if self.dm_enabled else off),
            CardField(t(self.locale, _("Following")), self._subscription_list()),
        ]

    def _subscription_list(self) -> str:
        if not self._subscriptions:
            return t(self.locale, _("_Nothing yet._"))
        lines = [
            f"**{self.describe(subscription)}**\n{self.detail(subscription)}" for subscription in self.subscriptions
        ]
        hidden = len(self._subscriptions) - len(self.subscriptions)
        if hidden > 0:
            lines.append(t(self.locale, _("…and {count} more."), count=hidden))
        return "\n".join(lines)

    def describe(self, subscription: NotificationSubscription) -> str:
        """What kind of thing this subscription follows."""
        return t(self.locale, KIND_LABELS[subscription.kind])

    def detail(self, subscription: NotificationSubscription) -> str:
        """What it follows, as far as this context can say.

        A creator and a competition are still named by their public UUID: resolving those to
        names means reading the accounts and records contexts from here, which is the half of
        audit C5 this step does not do. Nobody has to *retype* one any more, which was the
        part that hurt.
        """
        if subscription.record_filter is not None:
            return _filter_text(subscription.record_filter)
        return f"`{subscription.subject_id}`"

    def _suspension_note(self) -> str | None:
        if self._preferences is None or self._preferences.dm_suspended_at is None:
            return None
        return t(self.locale, _("Discord rejected a DM, so DMs are suspended until you re-enable them."))


class NotificationPanel(sl.Component):
    """A mounted notification workspace with semantic choices and actions."""

    selected_ids: tuple[str, ...] = sl.state(())
    closed: bool = sl.state(default=False)

    def __init__(
        self,
        *,
        notifications: NotificationService,
        account_id: int,
        author_id: int,
        locale: str | None = None,
    ) -> None:
        self._notifications = notifications
        self._account_id = account_id
        self._author_id = author_id
        self.locale = locale
        self._preferences: NotificationPreferences | None = None
        self._subscriptions: tuple[NotificationSubscription, ...] = ()

    async def load(self) -> None:
        self._preferences = await self._notifications.preferences(self._account_id)
        self._subscriptions = tuple(await self._notifications.subscriptions(self._account_id))
        self.selected_ids = tuple(
            selected for selected in self.selected_ids if any(str(item.id) == selected for item in self.subscriptions)
        )

    @property
    def web_enabled(self) -> bool:
        return self._preferences is not None and self._preferences.web_enabled

    @property
    def dm_enabled(self) -> bool:
        return self._preferences is not None and self._preferences.dm_enabled

    @property
    def subscriptions(self) -> tuple[NotificationSubscription, ...]:
        return self._subscriptions[:MAX_LISTED]

    def render(self) -> tuple[sl.LayoutNode, ...]:
        if self.closed:
            return (sl.primitives.banner(t(self.locale, _("Notifications closed")), accent=DISCORD_BLUE),)
        on, off = t(self.locale, _("On")), t(self.locale, _("Off"))
        fields = (
            sl.primitives.presets.Field(t(self.locale, _("Web inbox")), on if self.web_enabled else off),
            sl.primitives.presets.Field(t(self.locale, _("Discord DMs")), on if self.dm_enabled else off),
            sl.primitives.presets.Field(t(self.locale, _("Following")), self._subscription_list()),
        )
        nodes: list[sl.LayoutNode] = [
            sl.primitives.card(
                t(self.locale, _("Notifications")),
                t(self.locale, _("Toggle where notifications arrive, and unfollow what you no longer want.")),
                fields=fields,
                footer=self._suspension_note(),
            )
        ]
        if self.subscriptions:
            nodes.append(
                sl.Choices(
                    key="unfollow",
                    choices=tuple(
                        sl.Choice(
                            str(subscription.id),
                            self.describe(subscription),
                            self.detail(subscription),
                        )
                        for subscription in self.subscriptions
                    ),
                    selected=self.selected_ids,
                    on_change=self._selection_changed,
                    minimum=0,
                    maximum=len(self.subscriptions),
                )
            )
        nodes.append(
            sl.primitives.Row(
                (
                    sl.primitives.Button(
                        t(self.locale, _("Web inbox")),
                        self._toggle_web,
                        "web",
                        style=sl.primitives.ActionStyle.SUCCESS
                        if self.web_enabled
                        else sl.primitives.ActionStyle.SECONDARY,
                    ),
                    sl.primitives.Button(
                        t(self.locale, _("Discord DMs")),
                        self._toggle_dm,
                        "dm",
                        style=sl.primitives.ActionStyle.SUCCESS
                        if self.dm_enabled
                        else sl.primitives.ActionStyle.SECONDARY,
                    ),
                    sl.primitives.Button(
                        t(self.locale, _("Unfollow selected")),
                        self._unfollow,
                        "unfollow_selected",
                        style=sl.primitives.ActionStyle.DANGER,
                        disabled=not self.selected_ids,
                    ),
                    sl.primitives.Button(
                        t(self.locale, _("Close")),
                        self._close,
                        "close",
                    ),
                )
            )
        )
        return tuple(nodes)

    async def _selection_changed(self, event: sl.ChoiceEvent) -> None:
        self.selected_ids = event.selected

    async def _toggle_web(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        self._preferences = await self._notifications.set_preferences(
            self._account_id,
            web_enabled=not self.web_enabled,
            dm_enabled=self.dm_enabled,
        )
        self.invalidate()

    async def _toggle_dm(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        self._preferences = await self._notifications.set_preferences(
            self._account_id,
            web_enabled=self.web_enabled,
            dm_enabled=not self.dm_enabled,
        )
        self.invalidate()

    async def _unfollow(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        for subscription_id in self.selected_ids:
            await self._notifications.unsubscribe(self._account_id, int(subscription_id))
        await self.load()
        self.invalidate()

    async def _close(self, event: sl.PressEvent) -> None:
        self.closed = True
        await event.finish()

    def _subscription_list(self) -> str:
        if not self._subscriptions:
            return t(self.locale, _("_Nothing yet._"))
        lines = [
            f"**{self.describe(subscription)}**\n{self.detail(subscription)}" for subscription in self.subscriptions
        ]
        hidden = len(self._subscriptions) - len(self.subscriptions)
        if hidden > 0:
            lines.append(t(self.locale, _("…and {count} more."), count=hidden))
        return "\n".join(lines)

    def describe(self, subscription: NotificationSubscription) -> str:
        return t(self.locale, KIND_LABELS[subscription.kind])

    def detail(self, subscription: NotificationSubscription) -> str:
        if subscription.record_filter is not None:
            return _filter_text(subscription.record_filter)
        return f"\x60{subscription.subject_id}\x60"

    def _suspension_note(self) -> str | None:
        if self._preferences is None or self._preferences.dm_suspended_at is None:
            return None
        return t(self.locale, _("Discord rejected a DM, so DMs are suspended until you re-enable them."))

    def mount(self) -> sl.discord.Mount:
        return create_mount(self, locale=self.locale, timeout=SESSION_SECONDS, lock_to=self._author_id)


class UnfollowSelect(discord.ui.Select[NotificationPanelView]):
    """Pick subscriptions to drop, instead of reading an id off a list and typing it back."""

    def __init__(self, view: NotificationPanelView) -> None:
        options = [
            discord.SelectOption(
                label=view.describe(subscription),
                value=str(subscription.id),
                description=view.detail(subscription)[:100],
            )
            for subscription in view.subscriptions
        ]
        super().__init__(
            placeholder=t(view.locale, _("Unfollow…")),
            options=options,
            min_values=0,
            max_values=len(options),
        )
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._panel.unfollow([int(value) for value in self.values])
        await edit_interaction_layout(interaction, self._panel)


class WebInboxButton(discord.ui.Button[NotificationPanelView]):
    """Turn the web inbox on or off."""

    def __init__(self, view: NotificationPanelView) -> None:
        super().__init__(
            label=t(view.locale, _("Web inbox")),
            style=discord.ButtonStyle.success if view.web_enabled else discord.ButtonStyle.secondary,
        )
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._panel.set_channels(web=not self._panel.web_enabled, dm=self._panel.dm_enabled)
        await edit_interaction_layout(interaction, self._panel)


class DiscordDMButton(discord.ui.Button[NotificationPanelView]):
    """Turn Discord DM delivery on or off."""

    def __init__(self, view: NotificationPanelView) -> None:
        super().__init__(
            label=t(view.locale, _("Discord DMs")),
            style=discord.ButtonStyle.success if view.dm_enabled else discord.ButtonStyle.secondary,
        )
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._panel.set_channels(web=self._panel.web_enabled, dm=not self._panel.dm_enabled)
        await edit_interaction_layout(interaction, self._panel)


class ClosePanelButton(discord.ui.Button[NotificationPanelView]):
    def __init__(self, view: NotificationPanelView) -> None:
        super().__init__(label=t(view.locale, _("Close")), style=discord.ButtonStyle.secondary)
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._panel.disable_controls()
        await edit_interaction_layout(interaction, self._panel)


def _filter_text(record_filter: RecordSubscriptionFilter) -> str:
    """Render a structured record filter as the predicates a person wrote.

    `list` used to print `str(filter.as_dict())`, dict braces and all.
    """
    parts = [
        ", ".join(sorted(values))
        for values in (record_filter.build_kinds, record_filter.record_classes, record_filter.version_scopes)
        if values
    ]
    parts.extend(
        f"tag {predicate.tag_id}" if predicate.value is None else f"tag {predicate.tag_id}={predicate.value}"
        for predicate in record_filter.tags
    )
    return " · ".join(parts)
