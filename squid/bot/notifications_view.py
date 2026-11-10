"""Manage notification channels, follows, and the newest visible inbox items."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast, override
from uuid import UUID

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.ui import tr
from squid.notifications import (
    DEFAULT_INBOX_VISIBILITY,
    InboxVisibility,
    NotificationPreferences,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)
from squid.notifications.models import InboxNotification, NotificationSubscription

if TYPE_CHECKING:
    from squid.notifications.application import NotificationService

SESSION_SECONDS = 300

MAX_LISTED = 25
"""Maximum subscriptions or inbox items offered by one Discord select."""

type VisibilityResolver = Callable[[sd.ActionEvent], Awaitable[InboxVisibility]]


def _kind_label(kind: SubscriptionKind) -> sl.TextLike:
    match kind:
        case SubscriptionKind.CREATOR:
            return tr(t"bot-notifications-view-creator = Creator")
        case SubscriptionKind.RECORD:
            return tr(t"bot-notifications-view-record = Record")
        case SubscriptionKind.RECORD_FILTER:
            return tr(t"bot-notifications-view-record-filter = Record filter")


class NotificationScreen(sd.Screen):
    """A notification workspace that ends when replaced or timed out."""

    policy = sd.ResponseSpec(timeout=SESSION_SECONDS, session=sd.SessionSpec("notifications"))

    selected_ids: tuple[str, ...] = sl.state(())
    selected_inbox_ids: tuple[str, ...] = sl.state(())
    # Reload service data on mount instead of restoring it from a snapshot.
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

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self, event: sd.ActionEvent | None = None) -> None:
        """Reload preferences, subscriptions, and the first visible inbox page.

        When an event is supplied, resolve inbox visibility again. Remove selections
        that no longer appear in the displayed subscriptions or inbox.
        """
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

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        on, off = tr(t"bot-notifications-view-on = On"), tr(t"bot-notifications-view-off = Off")
        fields = (
            sl.field(tr(t"bot-notifications-view-web-inbox = Web inbox"), on if self.web_enabled else off),
            sl.field(tr(t"bot-notifications-view-discord-dms = Discord DMs"), on if self.dm_enabled else off),
            sl.field(tr(t"bot-notifications-view-following = Following"), self._subscription_list()),
            sl.field(
                tr(t"bot-notifications-view-unread = Unread"), str(sum(item.read_at is None for item in self._inbox))
            ),
        )
        description = tr(
            t"notifications-channel-and-follow-help = Toggle where "
            t"notifications arrive, and unfollow what you no longer want."
        )
        suspension_note = self._suspension_note()
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sl.section(
                sl.heading(tr(t"bot-notifications-view-notifications = Notifications")),
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
                            description=tr(t"bot-notifications-view-unread = Unread")
                            if notification.read_at is None
                            else tr(t"bot-notifications-view-read = Read"),
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
                    tr(t"bot-notifications-view-web-inbox = Web inbox"),
                    key="web",
                    on=sl.controlled(self.web_enabled, self._toggle_web),
                    tone=sl.Tone.SUCCESS if self.web_enabled else sl.Tone.NEUTRAL,
                ),
                sl.toggle(
                    tr(t"bot-notifications-view-discord-dms = Discord DMs"),
                    key="dm",
                    on=sl.controlled(self.dm_enabled, self._toggle_dm),
                    tone=sl.Tone.SUCCESS if self.dm_enabled else sl.Tone.NEUTRAL,
                ),
            )
        )
        nodes.extend(
            (
                sl.form(
                    tr(t"bot-notifications-view-follow-creator = Follow creator"),
                    sl.forms.FormSpec(
                        tr(t"bot-notifications-view-follow-a-creator = Follow a creator"),
                        (
                            sl.forms.TextField(
                                key="creator",
                                label=tr(t"bot-notifications-view-creator-profile-id = Creator profile ID"),
                                maximum=36,
                            ),
                        ),
                    ),
                    key="follow-creator",
                    on_submit=self._follow_creator,
                ),
                sl.form(
                    tr(t"bot-notifications-view-follow-record = Follow record"),
                    sl.forms.FormSpec(
                        tr(t"bot-notifications-view-follow-a-record = Follow a record"),
                        (
                            sl.forms.TextField(
                                key="record_title",
                                label=tr(t"bot-notifications-view-record-title-id = Record title ID"),
                                maximum=36,
                            ),
                        ),
                    ),
                    key="follow-record",
                    on_submit=self._follow_record,
                ),
                sl.form(
                    tr(t"bot-notifications-view-follow-matching-records = Follow matching records"),
                    self._filter_form(),
                    key="follow-filter",
                    on_submit=self._follow_filter,
                ),
            )
        )
        nodes.append(
            sl.action_controls(
                sl.action_control(
                    tr(t"bot-notifications-view-unfollow-selected = Unfollow selected"),
                    self._unfollow,
                    key="unfollow_selected",
                    tone=sl.Tone.DANGER,
                    available=bool(self.selected_ids),
                ),
                sl.action_control(
                    tr(t"bot-notifications-view-mark-selected-read = Mark selected read"),
                    self._mark_selected_read,
                    key="mark_selected_read",
                    available=bool(self.selected_inbox_ids),
                ),
                sl.action_control(
                    tr(t"bot-notifications-view-mark-selected-unread = Mark selected unread"),
                    self._mark_selected_unread,
                    key="mark_selected_unread",
                    available=bool(self.selected_inbox_ids),
                ),
                key="notification-actions",
            )
        )
        return tuple(nodes)

    async def _selection_changed(self, event: sd.ChoiceEvent) -> None:
        self.selected_ids = event.selected

    async def _inbox_selection_changed(self, event: sd.ChoiceEvent) -> None:
        self.selected_inbox_ids = event.selected

    async def _mark_selected_read(self, event: sd.PressEvent) -> None:
        await self._set_selected_read_state(event, read=True)

    async def _mark_selected_unread(self, event: sd.PressEvent) -> None:
        await self._set_selected_read_state(event, read=False)

    async def _set_selected_read_state(self, event: sd.PressEvent, *, read: bool) -> None:
        await event.acknowledge()
        visibility = await self._resolve_visibility(event)
        operation = self._notifications.mark_read if read else self._notifications.mark_unread
        for notification_id in self.selected_inbox_ids:
            await operation(self._account_id, int(notification_id), visibility=visibility)
        await self._refresh()
        self.invalidate()

    async def _toggle_web(self, event: sd.ToggleEvent) -> None:
        await event.acknowledge()
        self._preferences = await self._notifications.set_preferences(
            self._account_id,
            web_enabled=event.value,
            dm_enabled=self.dm_enabled,
        )
        await self._refresh(event)

    async def _toggle_dm(self, event: sd.ToggleEvent) -> None:
        await event.acknowledge()
        self._preferences = await self._notifications.set_preferences(
            self._account_id,
            web_enabled=self.web_enabled,
            dm_enabled=event.value,
        )
        await self._refresh(event)

    async def _unfollow(self, event: sd.PressEvent) -> None:
        await event.acknowledge()
        for subscription_id in self.selected_ids:
            await self._notifications.unsubscribe(self._account_id, int(subscription_id))
        await self._refresh(event)
        self.invalidate()

    async def _follow_creator(self, event: sd.SubmitEvent) -> None:
        creator = self._uuid(event.values["creator"])
        if creator is None:
            await event.notice(tr(t"notifications-invalid-creator-uuid = Enter a creator profile ID in UUID form."))
            return
        await self._notifications.subscribe(
            self._account_id,
            kind=SubscriptionKind.CREATOR,
            subject_id=creator,
        )
        await self._followed(event, tr(t"bot-notifications-view-following-that-creator = Following that creator."))

    async def _follow_record(self, event: sd.SubmitEvent) -> None:
        """Follow the stable record title UUID, so the subscription survives record rebuilds."""
        record_title = self._uuid(event.values["record_title"])
        if record_title is None:
            await event.notice(tr(t"notifications-invalid-record-title-uuid = Enter a record title ID in UUID form."))
            return
        await self._notifications.subscribe(
            self._account_id,
            kind=SubscriptionKind.RECORD,
            subject_id=record_title,
        )
        await self._followed(event, tr(t"bot-notifications-view-following-that-record = Following that record."))

    async def _follow_filter(self, event: sd.SubmitEvent) -> None:
        build_kind = self._optional_text(event.values.get("build_kind"))
        base_title = self._optional_text(event.values.get("base_title"))
        version_scope = self._optional_text(event.values.get("version_scope"))
        tag = event.values.get("tag")
        tag_value = self._optional_text(event.values.get("tag_value"))
        if not any((build_kind, base_title, version_scope, tag is not None)):
            await event.notice(tr(t"notifications-record-filter-required = Choose at least one record filter."))
            return
        record_filter = RecordSubscriptionFilter(
            build_kinds=frozenset({build_kind}) if build_kind else frozenset(),
            base_titles=frozenset({base_title}) if base_title else frozenset(),
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
        await self._followed(
            event,
            tr(t"notifications-follow-filter-confirmation = Following records matching that filter."),
        )

    async def _followed(self, event: sd.SubmitEvent, notice: sl.TextLike) -> None:
        await self._refresh(event)
        await event.notice(notice)

    async def _resolve_visibility(self, event: sd.ActionEvent) -> InboxVisibility:
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
            tr(t"bot-notifications-view-follow-matching-records = Follow matching records"),
            (
                sl.forms.TextField(
                    key="build_kind",
                    label=tr(t"bot-notifications-view-build-kind = Build kind"),
                    required=False,
                    maximum=100,
                ),
                sl.forms.TextField(
                    key="base_title",
                    label=tr(t"bot-notifications-view-base-title = Base title"),
                    required=False,
                    maximum=100,
                ),
                sl.forms.TextField(
                    key="version_scope",
                    label=tr(t"bot-notifications-view-version-scope = Version scope"),
                    required=False,
                    maximum=100,
                ),
                sl.forms.IntField(
                    key="tag",
                    label=tr(t"bot-notifications-view-showcase-tag-id = Showcase tag ID"),
                    required=False,
                    minimum=1,
                ),
                sl.forms.TextField(
                    key="tag_value",
                    label=tr(t"bot-notifications-view-exact-tag-value = Exact tag value"),
                    required=False,
                    maximum=100,
                ),
            ),
        )

    def _subscription_list(self) -> sl.TextLike:
        if not self._subscriptions:
            return tr(t"bot-notifications-view-nothing-yet = _Nothing yet._")
        lines: list[sl.TextLike] = [
            tr(t"""
                bot-notifications-subscription =
                    {{ "*" }}*{(label := self.describe(subscription))}**
                    {(detail := self.detail(subscription))}
                """)  # noqa: F841 - captured Fluent binding
            for subscription in self.subscriptions
        ]
        hidden = len(self._subscriptions) - len(self.subscriptions)
        if hidden > 0:
            lines.append(tr(t"bot-notifications-remainder = …and {hidden} more."))
        return sl.text.Message.join("\n", lines)

    @staticmethod
    def _inbox_label(notification: InboxNotification) -> sl.TextLike:
        match notification.kind.value:
            case "build_confirmed":
                return tr(t"bot-notifications-view-build-confirmed = Build confirmed")
            case "build_denied":
                return tr(t"bot-notifications-view-build-denied = Build denied")
            case "creator_build_confirmed":
                return tr(t"bot-notifications-view-creator-build-confirmed = Creator build confirmed")
            case "record_gained":
                return tr(t"bot-notifications-view-record-gained = Record gained")
            case "staff_build_submitted":
                return tr(t"bot-notifications-view-build-awaiting-review = Build awaiting review")
            # The cases above cover NotificationKind exhaustively today; the default is what a
            # kind added to the enum later renders as instead of falling off the end.
            case _:  # pyright: ignore[reportUnnecessaryComparison]
                return tr(t"bot-notifications-view-build-notification = Build notification")

    def describe(self, subscription: NotificationSubscription) -> sl.TextLike:
        return _kind_label(subscription.kind)

    def detail(self, subscription: NotificationSubscription) -> str:
        if subscription.record_filter is not None:
            return _filter_text(subscription.record_filter)
        return f"\x60{subscription.subject_id}\x60"

    def _suspension_note(self) -> sl.TextLike | None:
        if self._preferences is None or self._preferences.dm_suspended_at is None:
            return None
        return tr(
            t"notifications-dm-delivery-suspended = Discord rejected a DM, so DMs are "
            t"suspended until you re-enable them."
        )


def _filter_text(record_filter: RecordSubscriptionFilter) -> str:
    """Format a structured subscription filter as readable predicate values."""
    parts = [
        ", ".join(sorted(values))
        for values in (record_filter.build_kinds, record_filter.base_titles, record_filter.version_scopes)
        if values
    ]
    parts.extend(
        f"tag {predicate.tag_id}" if predicate.value is None else f"tag {predicate.tag_id}={predicate.value}"
        for predicate in record_filter.tags
    )
    return " · ".join(parts)
