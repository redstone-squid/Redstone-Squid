"""The panel behind `/notifications`.

Four commands used to answer what one screen shows: `status` read the two channels,
`channels` wrote both, `list` printed the subscriptions, and `unfollow` took an id you had
to read off `list` and type back. A subscription is a thing you look at and then remove, so
looking at it and removing it belong to the same message (audit C5's retyping half).
"""

from typing import TYPE_CHECKING

import squid_discord as sd
import squid_layouts as sl
from squid.bot.i18n import t
from squid.bot.ui import create_mount
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


class NotificationPanel(sl.Component):
    """A mounted notification workspace with semantic choices and actions."""

    selected_ids: tuple[str, ...] = sl.state(())
    closed: bool = sl.state(default=False)
    # Refreshed from the service by load(), so a snapshot would only restore them stale.
    _preferences: NotificationPreferences | None = sl.state(None, persist=False)
    _subscriptions: tuple[NotificationSubscription, ...] = sl.state((), persist=False)

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

    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        """Re-read this account's channels and follows. Also what unfollowing calls afterwards."""
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
            return (sl.section(sl.heading(t(self.locale, _("Notifications closed")))),)
        on, off = t(self.locale, _("On")), t(self.locale, _("Off"))
        fields = (
            sl.field(t(self.locale, _("Web inbox")), on if self.web_enabled else off),
            sl.field(t(self.locale, _("Discord DMs")), on if self.dm_enabled else off),
            sl.field(t(self.locale, _("Following")), self._subscription_list()),
        )
        description = t(self.locale, _("Toggle where notifications arrive, and unfollow what you no longer want."))
        suspension_note = self._suspension_note()
        nodes: list[sl.LayoutNode] = [
            sl.section(
                sl.heading(t(self.locale, _("Notifications"))),
                sl.truncate(sl.paragraph(description)),
                sl.fields(*fields),
                suspension_note and sl.note(suspension_note),
            )
        ]
        if self.subscriptions:
            nodes.append(
                sl.semantic.Choices(
                    key="unfollow",
                    choices=tuple(
                        sl.semantic.Choice(
                            str(subscription.id),
                            self.describe(subscription),
                            self.detail(subscription),
                        )
                        for subscription in self.subscriptions
                    ),
                    selection=sl.controlled(self.selected_ids, self._selection_changed),
                    minimum=0,
                    maximum=len(self.subscriptions),
                )
            )
        nodes.extend(
            (
                sl.toggle(
                    t(self.locale, _("Web inbox")),
                    key="web",
                    on=sl.controlled(self.web_enabled, self._toggle_web),
                    tone=sl.Tone.SUCCESS if self.web_enabled else sl.Tone.NEUTRAL,
                ),
                sl.toggle(
                    t(self.locale, _("Discord DMs")),
                    key="dm",
                    on=sl.controlled(self.dm_enabled, self._toggle_dm),
                    tone=sl.Tone.SUCCESS if self.dm_enabled else sl.Tone.NEUTRAL,
                ),
            )
        )
        nodes.append(
            sl.primitives.Row(
                (
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

    async def _toggle_web(self, event: sl.ToggleEvent) -> None:
        await event.acknowledge()
        self._preferences = await self._notifications.set_preferences(
            self._account_id,
            web_enabled=event.value,
            dm_enabled=self.dm_enabled,
        )

    async def _toggle_dm(self, event: sl.ToggleEvent) -> None:
        await event.acknowledge()
        self._preferences = await self._notifications.set_preferences(
            self._account_id,
            web_enabled=self.web_enabled,
            dm_enabled=event.value,
        )

    async def _unfollow(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        for subscription_id in self.selected_ids:
            await self._notifications.unsubscribe(self._account_id, int(subscription_id))
        await self._refresh()
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

    def mount(self, *, source: sd.host.HostSource) -> sd.Mount:
        return create_mount(
            self,
            source=source,
            access=sd.Owner(self._author_id),
            locale=self.locale,
            timeout=SESSION_SECONDS,
        )


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
