"""The panel behind `/notifications`.

Four commands used to answer what one screen shows: `status` read the two channels,
`channels` wrote both, `list` printed the subscriptions, and `unfollow` took an id you had
to read off `list` and type back. A subscription is a thing you look at and then remove, so
looking at it and removing it belong to the same message (audit C5's retyping half).
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast
from uuid import UUID

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.ui import tr
from squid.notifications import (
    DEFAULT_INBOX_VISIBILITY,
    InboxNotification,
    InboxVisibility,
    NotificationPreferences,
    NotificationSubscription,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)

if TYPE_CHECKING:
    from squid.notifications.application import NotificationService

SESSION_SECONDS = 300

MAX_LISTED = 25
"""A select holds 25 options, which is also as many as a card should list."""

type VisibilityResolver = Callable[[sl.ActionEvent], Awaitable[InboxVisibility]]


def _kind_label(kind: SubscriptionKind) -> sl.TextLike:
    match kind:
        case SubscriptionKind.CREATOR:
            return tr(t"Creator")
        case SubscriptionKind.RECORD:
            return tr(t"Record")
        case SubscriptionKind.RECORD_FILTER:
            return tr(t"Record filter")


class NotificationScreen(sd.Screen):
    """A notification workspace that ends when closed, replaced, or timed out."""

    session = sd.SessionSpec("notifications")
    timeout = SESSION_SECONDS
    audience = "personal"

    selected_ids: tuple[str, ...] = sl.state(())
    selected_inbox_ids: tuple[str, ...] = sl.state(())
    closed: bool = sl.state(default=False)
    # Refreshed from the service by load(), so a snapshot would only restore them stale.
    _preferences: NotificationPreferences | None = sl.state(None, persist=False)
    _subscriptions: tuple[NotificationSubscription, ...] = sl.state((), persist=False)
    _inbox: tuple[InboxNotification, ...] = sl.state((), persist=False)

    def __init__(
        self,
        *,
        notifications: NotificationService,
        account_id: int,
        author_id: int,
        visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY,
        visibility_resolver: VisibilityResolver | None = None,
    ) -> None:
        self._notifications = notifications
        self._account_id = account_id
        self._author_id = author_id
        self._visibility = visibility
        self._visibility_resolver = visibility_resolver

    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self, event: sl.ActionEvent | None = None) -> None:
        """Re-read this account's channels and follows. Also what unfollowing calls afterwards."""
        if event is not None:
            await self._resolve_visibility(event)
        self._preferences = await self._notifications.preferences(self._account_id)
        self._subscriptions = tuple(await self._notifications.subscriptions(self._account_id))
        self._inbox = tuple(
            (
                await self._notifications.inbox(
                    self._account_id,
                    page_size=MAX_LISTED,
                    visibility=self._visibility,
                )
            ).items
        )
        self.selected_ids = tuple(
            selected for selected in self.selected_ids if any(str(item.id) == selected for item in self.subscriptions)
        )
        self.selected_inbox_ids = tuple(
            selected for selected in self.selected_inbox_ids if any(str(item.id) == selected for item in self._inbox)
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

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self.closed:
            return (sl.section(sl.heading(tr(t"Notifications closed"))),)
        on, off = tr(t"On"), tr(t"Off")
        fields = (
            sl.field(tr(t"Web inbox"), on if self.web_enabled else off),
            sl.field(tr(t"Discord DMs"), on if self.dm_enabled else off),
            sl.field(tr(t"Following"), self._subscription_list()),
            sl.field(tr(t"Unread"), str(sum(item.read_at is None for item in self._inbox))),
        )
        description = tr(t"Toggle where notifications arrive, and unfollow what you no longer want.")
        suspension_note = self._suspension_note()
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.section(
                sl.heading(tr(t"Notifications")),
                sl.truncate(sl.paragraph(description)),
                sl.fields(*fields),
                suspension_note and sl.note(suspension_note),
            )
        ]
        if self.subscriptions:
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(
                            self.describe(subscription),
                            key=str(subscription.id),
                            description=self.detail(subscription),
                        )
                        for subscription in self.subscriptions
                    ),
                    key="unfollow",
                    selection=sl.controlled(self.selected_ids, self._selection_changed),
                    minimum=0,
                    maximum=len(self.subscriptions),
                )
            )
        if self._inbox:
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(
                            self._inbox_label(notification),
                            key=str(notification.id),
                            description=tr(t"Unread") if notification.read_at is None else tr(t"Read"),
                        )
                        for notification in self._inbox
                    ),
                    key="inbox",
                    selection=sl.controlled(self.selected_inbox_ids, self._inbox_selection_changed),
                    minimum=0,
                    maximum=len(self._inbox),
                )
            )
        nodes.extend(
            (
                sl.toggle(
                    tr(t"Web inbox"),
                    key="web",
                    on=sl.controlled(self.web_enabled, self._toggle_web),
                    tone=sl.Tone.SUCCESS if self.web_enabled else sl.Tone.NEUTRAL,
                ),
                sl.toggle(
                    tr(t"Discord DMs"),
                    key="dm",
                    on=sl.controlled(self.dm_enabled, self._toggle_dm),
                    tone=sl.Tone.SUCCESS if self.dm_enabled else sl.Tone.NEUTRAL,
                ),
            )
        )
        nodes.extend(
            (
                sl.form(
                    tr(t"Follow creator"),
                    sl.forms.FormSpec(
                        tr(t"Follow a creator"),
                        (
                            sl.forms.TextField(
                                key="creator",
                                label=tr(t"Creator profile ID"),
                                maximum=36,
                            ),
                        ),
                    ),
                    key="follow-creator",
                    on_submit=self._follow_creator,
                ),
                sl.form(
                    tr(t"Follow record"),
                    sl.forms.FormSpec(
                        tr(t"Follow a record"),
                        (
                            sl.forms.TextField(
                                key="competition",
                                label=tr(t"Record competition ID"),
                                maximum=36,
                            ),
                        ),
                    ),
                    key="follow-record",
                    on_submit=self._follow_record,
                ),
                sl.form(
                    tr(t"Follow matching records"),
                    self._filter_form(),
                    key="follow-filter",
                    on_submit=self._follow_filter,
                ),
            )
        )
        nodes.append(
            sl.action_controls(
                sl.action_control(
                    tr(t"Unfollow selected"),
                    self._unfollow,
                    key="unfollow_selected",
                    tone=sl.Tone.DANGER,
                    available=bool(self.selected_ids),
                ),
                sl.action_control(
                    tr(t"Mark selected read"),
                    self._mark_selected_read,
                    key="mark_selected_read",
                    available=bool(self.selected_inbox_ids),
                ),
                sl.action_control(
                    tr(t"Mark selected unread"),
                    self._mark_selected_unread,
                    key="mark_selected_unread",
                    available=bool(self.selected_inbox_ids),
                ),
                sl.action_control(
                    tr(t"Close"),
                    self._close,
                    key="close",
                ),
                key="notification-actions",
            )
        )
        return tuple(nodes)

    async def _selection_changed(self, event: sl.ChoiceEvent) -> None:
        self.selected_ids = event.selected

    async def _inbox_selection_changed(self, event: sl.ChoiceEvent) -> None:
        self.selected_inbox_ids = event.selected

    async def _mark_selected_read(self, event: sl.PressEvent) -> None:
        await self._set_selected_read_state(event, read=True)

    async def _mark_selected_unread(self, event: sl.PressEvent) -> None:
        await self._set_selected_read_state(event, read=False)

    async def _set_selected_read_state(self, event: sl.PressEvent, *, read: bool) -> None:
        await event.acknowledge()
        visibility = await self._resolve_visibility(event)
        operation = self._notifications.mark_read if read else self._notifications.mark_unread
        for notification_id in self.selected_inbox_ids:
            await operation(self._account_id, int(notification_id), visibility=visibility)
        await self._refresh()
        self.invalidate()

    async def _toggle_web(self, event: sl.ToggleEvent) -> None:
        await event.acknowledge()
        self._preferences = await self._notifications.set_preferences(
            self._account_id,
            web_enabled=event.value,
            dm_enabled=self.dm_enabled,
        )
        await self._refresh(event)

    async def _toggle_dm(self, event: sl.ToggleEvent) -> None:
        await event.acknowledge()
        self._preferences = await self._notifications.set_preferences(
            self._account_id,
            web_enabled=self.web_enabled,
            dm_enabled=event.value,
        )
        await self._refresh(event)

    async def _unfollow(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        for subscription_id in self.selected_ids:
            await self._notifications.unsubscribe(self._account_id, int(subscription_id))
        await self._refresh(event)
        self.invalidate()

    async def _follow_creator(self, event: sl.SubmitEvent) -> None:
        creator = self._uuid(event.values["creator"])
        if creator is None:
            await event.notice(tr(t"Enter a creator profile ID in UUID form."))
            return
        await self._notifications.subscribe(
            self._account_id,
            kind=SubscriptionKind.CREATOR,
            subject_id=creator,
        )
        await self._followed(event, tr(t"Following that creator."))

    async def _follow_record(self, event: sl.SubmitEvent) -> None:
        competition = self._uuid(event.values["competition"])
        if competition is None:
            await event.notice(tr(t"Enter a record competition ID in UUID form."))
            return
        await self._notifications.subscribe(
            self._account_id,
            kind=SubscriptionKind.RECORD,
            subject_id=competition,
        )
        await self._followed(event, tr(t"Following that record."))

    async def _follow_filter(self, event: sl.SubmitEvent) -> None:
        build_kind = self._optional_text(event.values.get("build_kind"))
        record_class = self._optional_text(event.values.get("record_class"))
        version_scope = self._optional_text(event.values.get("version_scope"))
        tag = event.values.get("tag")
        tag_value = self._optional_text(event.values.get("tag_value"))
        if not any((build_kind, record_class, version_scope, tag is not None)):
            await event.notice(tr(t"Choose at least one record filter."))
            return
        record_filter = RecordSubscriptionFilter(
            build_kinds=frozenset({build_kind}) if build_kind else frozenset(),
            record_classes=frozenset({record_class}) if record_class else frozenset(),
            version_scopes=frozenset({version_scope}) if version_scope else frozenset(),
            tags=()
            if tag is None
            else (TagPredicate(cast(int, tag), "present" if tag_value is None else "exact", tag_value),),
        )
        await self._notifications.subscribe(
            self._account_id,
            kind=SubscriptionKind.RECORD_FILTER,
            record_filter=record_filter,
        )
        await self._followed(event, tr(t"Following records matching that filter."))

    async def _followed(self, event: sl.SubmitEvent, notice: sl.TextLike) -> None:
        await self._refresh(event)
        await event.notice(notice)

    async def _resolve_visibility(self, event: sl.ActionEvent) -> InboxVisibility:
        if self._visibility_resolver is not None:
            self._visibility = await self._visibility_resolver(event)
        return self._visibility

    @staticmethod
    def _uuid(value: object) -> UUID | None:
        try:
            return UUID(str(value))
        except ValueError:
            return None

    @staticmethod
    def _optional_text(value: object) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None

    @staticmethod
    def _filter_form() -> sl.forms.FormSpec:
        return sl.forms.FormSpec(
            tr(t"Follow matching records"),
            (
                sl.forms.TextField(key="build_kind", label=tr(t"Build kind"), required=False, maximum=100),
                sl.forms.TextField(key="record_class", label=tr(t"Record class"), required=False, maximum=100),
                sl.forms.TextField(key="version_scope", label=tr(t"Version scope"), required=False, maximum=100),
                sl.forms.IntField(key="tag", label=tr(t"Showcase tag ID"), required=False, minimum=1),
                sl.forms.TextField(key="tag_value", label=tr(t"Exact tag value"), required=False, maximum=100),
            ),
        )

    async def _close(self, event: sl.PressEvent) -> None:
        self.closed = True
        await event.finish()

    def _subscription_list(self) -> sl.TextLike:
        if not self._subscriptions:
            return tr(t"_Nothing yet._")
        params: dict[str, object] = {}
        lines: list[str] = []
        for index, subscription in enumerate(self.subscriptions):
            label = f"label_{index}"
            params[label] = self.describe(subscription)
            lines.append(f"**{{{label}}}**\n{self.detail(subscription)}")
        hidden = len(self._subscriptions) - len(self.subscriptions)
        if hidden > 0:
            count = hidden
            params["remainder"] = tr(t"…and {count} more.")
            lines.append("{remainder}")
        return sl.text.Message("\n".join(lines), params)

    @staticmethod
    def _inbox_label(notification: InboxNotification) -> sl.TextLike:
        match notification.kind.value:
            case "build_confirmed":
                return tr(t"Build confirmed")
            case "build_denied":
                return tr(t"Build denied")
            case "creator_build_confirmed":
                return tr(t"Creator build confirmed")
            case "record_gained":
                return tr(t"Record gained")
            case "staff_build_submitted":
                return tr(t"Build awaiting review")
            case _:
                return tr(t"Build notification")

    def describe(self, subscription: NotificationSubscription) -> sl.TextLike:
        return _kind_label(subscription.kind)

    def detail(self, subscription: NotificationSubscription) -> str:
        if subscription.record_filter is not None:
            return _filter_text(subscription.record_filter)
        return f"\x60{subscription.subject_id}\x60"

    def _suspension_note(self) -> sl.TextLike | None:
        if self._preferences is None or self._preferences.dm_suspended_at is None:
            return None
        return tr(t"Discord rejected a DM, so DMs are suspended until you re-enable them.")


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
