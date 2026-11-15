"""Manage notification channels, follows, and the newest visible inbox items."""

from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING, Any, cast, override
from uuid import UUID

from pydantic import JsonValue

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.edit_controller import EditController
from squid.bot.lookup import pick
from squid.bot.ui import tr
from squid.core.actors import Actor
from squid.edits.application import EditRunner
from squid.edits.domain import EditValues, Revision
from squid.notifications import (
    DEFAULT_INBOX_VISIBILITY,
    InboxVisibility,
    NotificationPreferences,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)
from squid.notifications.edits import (
    INBOX_EDIT,
    PREFERENCES_EDIT,
    SUBSCRIPTIONS_EDIT,
    inbox_target,
    notification_target,
)
from squid.notifications.models import InboxNotification, NotificationSubscription
from squid.records.domain import BaseTitle, BuildKind, VersionScope
from squid.suggestions.domain import Suggestion
from squid.tags.domain import TagValueType
from squid_i18n import md_tr

if TYPE_CHECKING:
    from squid.notifications.application import NotificationService

SESSION_SECONDS = 300

MAX_LISTED = 25
"""Maximum subscriptions or inbox items offered by one Discord select."""

type VisibilityResolver = Callable[[], Awaitable[InboxVisibility]]


def _kind_label(kind: SubscriptionKind) -> sl.TextLike:
    match kind:
        case SubscriptionKind.CREATOR:
            return tr(t"bot-notifications-view-creator = Creator")
        case SubscriptionKind.RECORD:
            return tr(t"bot-notifications-view-record = Record")
        case SubscriptionKind.RECORD_FILTER:
            return tr(t"bot-notifications-view-record-filter = Record filter")


class NotificationScreen(sd.Screen[None, Any, RedstoneSquid]):
    """Compose notification workflows with one revision-aware edit history."""

    policy = sd.ResponseSpec(timeout=SESSION_SECONDS, session=sd.SessionSpec("notifications"))

    def __init__(
        self,
        *,
        notifications: NotificationService,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        account_id: int,
        author_id: int,
        visibility: InboxVisibility = DEFAULT_INBOX_VISIBILITY,
        visibility_resolver: VisibilityResolver | None = None,
    ) -> None:
        self.settings = NotificationSettings(notifications, account_id)
        self.edits = EditController(edits, actor, self._refresh)
        self.preferences = NotificationPreferencesControls(self.settings, self.edits)
        self.subscriptions = NotificationSubscriptions(self.settings, self.edits)
        self.inbox = NotificationInbox(notifications, account_id, self.edits, visibility, visibility_resolver)
        self.tabs = sp.Tabs(
            (
                sp.Tab("inbox", tr(t"notifications-tab-inbox = Inbox"), self.inbox),
                sp.Tab("following", tr(t"bot-notifications-view-following = Following"), self.subscriptions),
                sp.Tab("delivery", tr(t"notifications-tab-delivery = Delivery"), self.preferences),
            ),
            key="notification-tabs",
        )

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        # Preferences and subscriptions share a revision; undo and redo refresh both.
        await self.settings.refresh()
        self.subscriptions.reconcile_selection()
        await self.inbox.refresh()

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        following = len(self.settings.subscriptions)
        unread = self.inbox.unread_count
        return (
            sl.section(
                sl.heading(tr(t"bot-notifications-view-notifications = Notifications")),
                sl.note(md_tr(t"notifications-summary = {unread} unread · {following} follows")),
            ),
            self.tabs,
            *self.edits.refresh_controls(),
        )


class NotificationSettings(sl.Reactive):
    """The preferences and subscriptions snapshot sharing one persisted revision."""

    preferences: NotificationPreferences | None = sl.state(None)
    subscriptions: tuple[NotificationSubscription, ...] = sl.state(())
    labels: Mapping[int, str] = sl.state(factory=dict)

    def __init__(self, notifications: NotificationService, account_id: int) -> None:
        self._notifications = notifications
        self._account_id = account_id

    async def refresh(self) -> None:
        snapshot = await self._notifications.editor_snapshot(self._account_id)
        self.preferences = snapshot.preferences
        self.subscriptions = snapshot.subscriptions
        self.labels = dict(snapshot.labels)

    async def save(self, edits: EditController, kind: str, values: EditValues, label: sl.TextLike) -> None:
        revision = 0 if self.preferences is None else self.preferences.revision
        await edits.bind(kind, notification_target(self._account_id), revision).save(values, label)


class NotificationPreferencesControls(sl.Component[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    """Edit delivery preferences using the shared settings revision."""

    def __init__(self, settings: NotificationSettings, edits: EditController) -> None:
        self._settings = settings
        self.edits = edits

    @property
    def web_enabled(self) -> bool:
        return self._settings.preferences is not None and self._settings.preferences.web_enabled

    @property
    def dm_enabled(self) -> bool:
        return self._settings.preferences is not None and self._settings.preferences.dm_enabled

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        suspension_note = self.suspension_note()
        return (
            sl.section(
                sl.heading(tr(t"notifications-delivery-heading = Where notifications arrive")),
                sl.paragraph(
                    tr(t"notifications-delivery-help = Choose your delivery channels. Changes save immediately.")
                ),
                suspension_note and sl.note(suspension_note),
            ),
            sl.action_controls(
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
                key="delivery",
            ),
        )

    async def _toggle_web(self, event: sd.ToggleEvent) -> None:
        await event.acknowledge()
        await self._save_preferences((event.value, self.dm_enabled))

    async def _toggle_dm(self, event: sd.ToggleEvent) -> None:
        await event.acknowledge()
        await self._save_preferences((self.web_enabled, event.value))

    async def _save_preferences(self, after: tuple[bool, bool]) -> None:
        await self._settings.save(
            self.edits,
            PREFERENCES_EDIT,
            EditValues.of({"web_enabled": after[0], "dm_enabled": after[1]}),
            tr(t"notification-history-preferences = Change notification preferences"),
        )

    def suspension_note(self) -> sl.TextLike | None:
        if self._settings.preferences is None or self._settings.preferences.dm_suspended_at is None:
            return None
        return tr(
            t"notifications-dm-delivery-suspended = Discord rejected a DM, so DMs are "
            t"suspended until you re-enable them."
        )


class NotificationSubscriptions(sl.Component[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    """Own follow forms and the selection of subscriptions to remove."""

    selected_ids: tuple[str, ...] = sl.state(())
    page: int = sl.state(0)
    filter_tag: Suggestion | None = sl.state(None)
    filter_tag_type: TagValueType = sl.state(TagValueType.TEXT)

    def __init__(self, settings: NotificationSettings, edits: EditController) -> None:
        self._settings = settings
        self.edits = edits

    def reconcile_selection(self) -> None:
        self.selected_ids = tuple(
            selected
            for selected in self.selected_ids
            if any(str(item.id) == selected for item in self._settings.subscriptions)
        )
        self.page = min(self.page, max(0, (len(self._settings.subscriptions) - 1) // MAX_LISTED))

    @property
    def subscriptions(self) -> tuple[NotificationSubscription, ...]:
        return self._settings.subscriptions[self.page * MAX_LISTED : (self.page + 1) * MAX_LISTED]

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        selection: tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...] = ()
        if self.subscriptions:
            selection = (
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
                    selection=sl.controlled(
                        tuple(
                            key for key in self.selected_ids if any(str(row.id) == key for row in self.subscriptions)
                        ),
                        self._selection_changed,
                    ),
                    minimum=0,
                    maximum=len(self.subscriptions),
                ),
            )
        actions = sl.action_controls(
            sl.action_control(
                tr(t"bot-notifications-view-follow-creator = Follow creator"),
                self._follow_creator,
                key="follow-creator",
            ),
            sl.action_control(
                tr(t"bot-notifications-view-follow-record = Follow record"),
                self._follow_record,
                key="follow-record",
            ),
            sl.form(
                tr(t"bot-notifications-view-follow-matching-records = Follow matching records"),
                self._filter_form(),
                key="follow-filter",
                on_submit=partial(self._follow_filter, tag=self.filter_tag),
            ),
            bool(self.subscriptions)
            and sl.action_control(
                tr(t"bot-notifications-view-unfollow-selected = Unfollow selected"),
                self._unfollow,
                key="unfollow_selected",
                tone=sl.Tone.DANGER,
                available=bool(self.selected_ids),
            ),
            key="subscription-actions",
        )
        return (
            sl.section(
                sl.heading(tr(t"notifications-following-heading = Your follows")),
                sl.paragraph(
                    tr(
                        t"notifications-following-help = Follow creators or records to hear about new builds and records."
                    )
                ),
                not self._settings.subscriptions
                and sl.note(tr(t"notifications-following-empty = Nothing followed yet. Add your first follow below.")),
            ),
            *selection,
            sl.note(self.filter_tag.label) if self.filter_tag is not None else sl.note(""),
            sl.action_controls(
                sl.action_control(
                    tr(t"notification-choose-tag = Choose filter tag"),
                    self._choose_filter_tag,
                    key="filter-tag",
                ),
                sl.action_control(
                    tr(t"notification-clear-tag = Clear filter tag"),
                    self._clear_filter_tag,
                    key="clear-filter-tag",
                    available=self.filter_tag is not None,
                ),
                key="filter-tag-actions",
            ),
            sl.action_controls(
                sl.action_control(
                    tr(t"notification-previous = Previous follows"),
                    self._previous,
                    key="previous",
                    available=self.page > 0,
                ),
                sl.action_control(
                    tr(t"notification-next = Next follows"),
                    self._next,
                    key="next",
                    available=(self.page + 1) * MAX_LISTED < len(self._settings.subscriptions),
                ),
                key="follow-pages",
            )
            if len(self._settings.subscriptions) > MAX_LISTED
            else sl.note(""),
            actions,
        )

    async def _previous(self, _event: sd.PressEvent) -> None:
        self.page = max(0, self.page - 1)

    async def _next(self, _event: sd.PressEvent) -> None:
        self.page = min((len(self._settings.subscriptions) - 1) // MAX_LISTED, self.page + 1)

    async def _selection_changed(self, event: sd.ChoiceEvent) -> None:
        visible = {str(row.id) for row in self.subscriptions}
        self.selected_ids = tuple(key for key in self.selected_ids if key not in visible) + event.selected

    async def _unfollow(self, event: sd.PressEvent) -> None:
        await event.acknowledge()
        if self.selected_ids:
            await self._settings.save(
                self.edits,
                SUBSCRIPTIONS_EDIT,
                EditValues.of({"states": {key: False for key in self.selected_ids}}),
                tr(t"notification-history-unfollow = Unfollow"),
            )

    async def _save_follow(
        self,
        kind: SubscriptionKind,
        *,
        subject_id: UUID | None = None,
        record_filter: RecordSubscriptionFilter | None = None,
    ) -> None:
        await self._settings.save(
            self.edits,
            SUBSCRIPTIONS_EDIT,
            EditValues.of(
                {
                    "create": {
                        "kind": kind.value,
                        "subject_id": None if subject_id is None else str(subject_id),
                        "record_filter": None
                        if record_filter is None
                        else cast(dict[str, JsonValue], record_filter.as_dict()),
                    }
                }
            ),
            tr(t"notification-history-follow = Follow"),
        )

    async def _follow_creator(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await pick(
            event,
            "creator_profiles",
            tr(t"notification-choose-creator = Choose a creator"),
            self._creator_picked,
            confirm=tr(t"notification-follow = Follow"),
        )

    async def _creator_picked(self, event: sd.ActionEvent, item: Suggestion) -> None:
        await self._save_follow(kind=SubscriptionKind.CREATOR, subject_id=UUID(item.value))
        await event.notice(tr(t"bot-notifications-view-following-that-creator = Following that creator."))

    async def _follow_record(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await pick(
            event,
            "record_titles",
            tr(t"notification-choose-record = Choose a record"),
            self._record_picked,
            confirm=tr(t"notification-follow = Follow"),
        )

    async def _record_picked(self, event: sd.ActionEvent, item: Suggestion) -> None:
        # The lookup carries the stable title UUID, never a definition's numeric ID.
        await self._save_follow(kind=SubscriptionKind.RECORD, subject_id=UUID(item.value))
        await event.notice(tr(t"bot-notifications-view-following-that-record = Following that record."))

    async def _choose_filter_tag(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await pick(event, "showcase_tag_ids", tr(t"notification-choose-tag = Choose filter tag"), self._tag_picked)

    async def _tag_picked(self, event: sd.ActionEvent, item: Suggestion) -> None:
        request = cast(sd.Request[Any, RedstoneSquid], event.context)
        definitions = await request.client.services.tags.public_definitions()
        tag = next((tag for tag in definitions if str(tag.id) == item.value), None)
        if tag is None:
            await event.notice(
                tr(t"notification-tag-unavailable = That tag is no longer available. Choose another tag.")
            )
            return
        self.filter_tag = item
        self.filter_tag_type = tag.value_type

    async def _clear_filter_tag(self, _event: sd.PressEvent) -> None:
        self.filter_tag = None

    async def _follow_filter(self, event: sd.SubmitEvent, *, tag: Suggestion | None = None) -> None:
        build_kind = self._optional_text(event.values.get("build_kind"))
        base_title = self._optional_text(event.values.get("base_title"))
        version_scope = self._optional_text(event.values.get("version_scope"))
        tag_id = None if tag is None else int(tag.value)
        tag_value = cast(str | float | bool | None, event.values.get("tag_value"))
        if isinstance(tag_value, str):
            tag_value = tag_value.strip() or None
        if not any((build_kind, base_title, version_scope, tag_id is not None)):
            await event.notice(tr(t"notifications-record-filter-required = Choose at least one record filter."))
            return
        record_filter = RecordSubscriptionFilter(
            build_kinds=frozenset({build_kind}) if build_kind else frozenset(),
            base_titles=frozenset({base_title}) if base_title else frozenset(),
            version_scopes=frozenset({version_scope}) if version_scope else frozenset(),
            tags=()
            if tag_id is None
            else (TagPredicate(tag_id, "present" if tag_value is None else "exact", tag_value),),
        )
        await self._save_follow(
            kind=SubscriptionKind.RECORD_FILTER,
            record_filter=record_filter,
        )
        await self._followed(
            event,
            tr(t"notifications-follow-filter-confirmation = Following records matching that filter."),
        )

    async def _followed(self, event: sd.SubmitEvent, notice: sl.TextLike) -> None:
        await event.notice(notice)

    @staticmethod
    def _optional_text(value: object) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None

    def _filter_form(self) -> sl.forms.FormSpec:
        value_fields: tuple[sl.forms.FormField, ...] = ()
        label = tr(t"bot-notifications-view-exact-tag-value = Exact tag value")
        if self.filter_tag is not None and self.filter_tag_type is not TagValueType.NONE:
            field: sl.forms.FormField
            if self.filter_tag_type is TagValueType.BOOLEAN:
                field = sl.forms.ChoiceField(
                    key="tag_value",
                    label=label,
                    required=False,
                    options=(
                        sl.forms.ChoiceOption("true", tr(t"notification-tag-true = True"), value=True),
                        sl.forms.ChoiceOption("false", tr(t"notification-tag-false = False"), value=False),
                    ),
                )
            elif self.filter_tag_type is TagValueType.NUMERIC:
                field = sl.forms.FloatField(key="tag_value", label=label, required=False)
            else:
                field = sl.forms.TextField(key="tag_value", label=label, required=False, maximum=100)
            value_fields = (field,)
        return sl.forms.FormSpec(
            tr(t"bot-notifications-view-follow-matching-records = Follow matching records"),
            (
                sl.forms.ChoiceField(
                    key="build_kind",
                    label=tr(t"bot-notifications-view-build-kind = Build kind"),
                    required=False,
                    options=tuple(
                        sl.forms.ChoiceOption(item.value, item.value.replace("_", " ").title(), item.value)
                        for item in BuildKind
                    ),
                ),
                sl.forms.ChoiceField(
                    key="base_title",
                    label=tr(t"bot-notifications-view-base-title = Base title"),
                    required=False,
                    options=tuple(
                        sl.forms.ChoiceOption(item.value, item.value.replace("_", " ").title(), item.value)
                        for item in BaseTitle
                    ),
                ),
                sl.forms.ChoiceField(
                    key="version_scope",
                    label=tr(t"bot-notifications-view-version-scope = Version scope"),
                    required=False,
                    options=tuple(
                        sl.forms.ChoiceOption(item.value, item.value.replace("_", " ").title(), item.value)
                        for item in VersionScope
                    ),
                ),
                *value_fields,
            ),
        )

    def summary(self) -> sl.TextLike:
        if not self._settings.subscriptions:
            return tr(t"bot-notifications-view-nothing-yet = _Nothing yet._")
        lines: list[sl.TextLike] = []
        for subscription in self.subscriptions:
            label = self.describe(subscription)
            detail = self.detail(subscription)
            lines.append(
                md_tr(
                    t"""
                    bot-notifications-subscription =
                        {{ "*" }}*{label}**
                        {detail}
                    """,
                )
            )
        hidden = len(self._settings.subscriptions) - len(self.subscriptions)
        if hidden > 0:
            lines.append(md_tr(t"bot-notifications-remainder = …and {hidden} more."))
        return sl.text.Text.join("\n", lines)

    def describe(self, subscription: NotificationSubscription) -> sl.TextLike:
        return self._settings.labels.get(subscription.id) or _kind_label(subscription.kind)

    def detail(self, subscription: NotificationSubscription) -> sl.TextLike:
        if subscription.record_filter is not None:
            return _filter_text(subscription.record_filter)
        return _kind_label(subscription.kind)


class NotificationInbox(sl.Component[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    """Own visible inbox items, selection, and read-state changes."""

    _visibility: InboxVisibility = sl.state(DEFAULT_INBOX_VISIBILITY)
    selected_inbox_ids: tuple[str, ...] = sl.state(())
    _inbox: tuple[InboxNotification, ...] = sl.state(())

    def __init__(
        self,
        notifications: NotificationService,
        account_id: int,
        edits: EditController,
        visibility: InboxVisibility,
        visibility_resolver: VisibilityResolver | None,
    ) -> None:
        self._notifications = notifications
        self._account_id = account_id
        self.edits = edits
        self._visibility = visibility
        self._visibility_resolver = visibility_resolver

    @property
    def unread_count(self) -> int:
        return sum(item.read_at is None for item in self._inbox)

    async def refresh(self) -> None:
        await self._resolve_visibility()
        self._inbox = tuple(
            (
                await self._notifications.inbox(
                    self._account_id,
                    page_size=MAX_LISTED,
                    visibility=self._visibility,
                )
            ).items
        )
        self.selected_inbox_ids = tuple(
            selected for selected in self.selected_inbox_ids if any(str(item.id) == selected for item in self._inbox)
        )

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        if not self._inbox:
            return (
                sl.section(
                    sl.heading(tr(t"notifications-inbox-empty = Your inbox is empty")),
                    sl.paragraph(
                        tr(t"notifications-inbox-empty-help = Follow creators or records in Following to get updates.")
                    ),
                ),
            )
        return (
            sl.section(
                sl.heading(tr(t"notifications-inbox-heading = Recent notifications")),
                sl.note(tr(t"notifications-inbox-help = Select notifications to mark them read or unread.")),
            ),
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
            ),
            sl.action_controls(
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
                key="inbox-actions",
            ),
        )

    async def _inbox_selection_changed(self, event: sd.ChoiceEvent) -> None:
        self.selected_inbox_ids = event.selected

    async def _mark_selected_read(self, event: sd.PressEvent) -> None:
        await self._set_selected_read_state(event, read=True)

    async def _mark_selected_unread(self, event: sd.PressEvent) -> None:
        await self._set_selected_read_state(event, read=False)

    async def _set_selected_read_state(self, event: sd.PressEvent, *, read: bool) -> None:
        await event.acknowledge()
        rows = tuple(row for row in self._inbox if str(row.id) in self.selected_inbox_ids)
        if not rows:
            return
        await self.edits.bind_many(
            INBOX_EDIT, tuple(Revision(inbox_target(row.id), row.revision) for row in rows)
        ).save(
            EditValues.of({"states": {str(row.id): read for row in rows}}),
            tr(t"notification-history-read = Change read state"),
        )

    async def _resolve_visibility(self) -> InboxVisibility:
        if self._visibility_resolver is not None:
            self._visibility = await self._visibility_resolver()
        return self._visibility

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
