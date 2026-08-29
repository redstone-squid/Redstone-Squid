"""The panel behind `/notifications`.

Four commands used to answer what one screen shows: `status` read the two channels,
`channels` wrote both, `list` printed the subscriptions, and `unfollow` took an id you had
to read off `list` and type back. A subscription is a thing you look at and then remove, so
looking at it and removing it belong to the same message (audit C5's retyping half).
"""

from typing import TYPE_CHECKING, cast
from uuid import UUID

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.ui import L
from squid.notifications import (
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


def _kind_label(kind: SubscriptionKind) -> sl.TextLike:
    match kind:
        case SubscriptionKind.CREATOR:
            return L(t"Creator")
        case SubscriptionKind.RECORD:
            return L(t"Record")
        case SubscriptionKind.RECORD_FILTER:
            return L(t"Record filter")


class NotificationScreen(sd.UserSessionScreen):
    """A notification workspace that ends when closed, replaced, or timed out."""

    session_name = "notifications"
    timeout = SESSION_SECONDS

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
    ) -> None:
        self._notifications = notifications
        self._account_id = account_id
        self._author_id = author_id

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

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self.closed:
            return (sl.section(sl.heading(L(t"Notifications closed"))),)
        on, off = L(t"On"), L(t"Off")
        fields = (
            sl.field(L(t"Web inbox"), on if self.web_enabled else off),
            sl.field(L(t"Discord DMs"), on if self.dm_enabled else off),
            sl.field(L(t"Following"), self._subscription_list()),
        )
        description = L(t"Toggle where notifications arrive, and unfollow what you no longer want.")
        suspension_note = self._suspension_note()
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.section(
                sl.heading(L(t"Notifications")),
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
        nodes.extend(
            (
                sl.toggle(
                    L(t"Web inbox"),
                    key="web",
                    on=sl.controlled(self.web_enabled, self._toggle_web),
                    tone=sl.Tone.SUCCESS if self.web_enabled else sl.Tone.NEUTRAL,
                ),
                sl.toggle(
                    L(t"Discord DMs"),
                    key="dm",
                    on=sl.controlled(self.dm_enabled, self._toggle_dm),
                    tone=sl.Tone.SUCCESS if self.dm_enabled else sl.Tone.NEUTRAL,
                ),
            )
        )
        nodes.extend(
            (
                sl.form(
                    L(t"Follow creator"),
                    sl.forms.FormSpec(
                        L(t"Follow a creator"),
                        (
                            sl.forms.TextField(
                                key="creator",
                                label=L(t"Creator profile ID"),
                                maximum=36,
                            ),
                        ),
                    ),
                    key="follow-creator",
                    on_submit=self._follow_creator,
                ),
                sl.form(
                    L(t"Follow record"),
                    sl.forms.FormSpec(
                        L(t"Follow a record"),
                        (
                            sl.forms.TextField(
                                key="competition",
                                label=L(t"Record competition ID"),
                                maximum=36,
                            ),
                        ),
                    ),
                    key="follow-record",
                    on_submit=self._follow_record,
                ),
                sl.form(
                    L(t"Follow matching records"),
                    self._filter_form(),
                    key="follow-filter",
                    on_submit=self._follow_filter,
                ),
            )
        )
        nodes.append(
            sl.action_controls(
                sl.action_control(
                    L(t"Unfollow selected"),
                    self._unfollow,
                    key="unfollow_selected",
                    tone=sl.Tone.DANGER,
                    available=bool(self.selected_ids),
                ),
                sl.action_control(
                    L(t"Close"),
                    self._close,
                    key="close",
                ),
                key="notification-actions",
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

    async def _follow_creator(self, event: sl.SubmitEvent) -> None:
        creator = self._uuid(event.values["creator"])
        if creator is None:
            await event.notice(L(t"Enter a creator profile ID in UUID form."))
            return
        await self._notifications.subscribe(
            self._account_id,
            kind=SubscriptionKind.CREATOR,
            subject_id=creator,
        )
        await self._followed(event, L(t"Following that creator."))

    async def _follow_record(self, event: sl.SubmitEvent) -> None:
        competition = self._uuid(event.values["competition"])
        if competition is None:
            await event.notice(L(t"Enter a record competition ID in UUID form."))
            return
        await self._notifications.subscribe(
            self._account_id,
            kind=SubscriptionKind.RECORD,
            subject_id=competition,
        )
        await self._followed(event, L(t"Following that record."))

    async def _follow_filter(self, event: sl.SubmitEvent) -> None:
        build_kind = self._optional_text(event.values.get("build_kind"))
        record_class = self._optional_text(event.values.get("record_class"))
        version_scope = self._optional_text(event.values.get("version_scope"))
        tag = event.values.get("tag")
        tag_value = self._optional_text(event.values.get("tag_value"))
        if not any((build_kind, record_class, version_scope, tag is not None)):
            await event.notice(L(t"Choose at least one record filter."))
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
        await self._followed(event, L(t"Following records matching that filter."))

    async def _followed(self, event: sl.SubmitEvent, notice: sl.TextLike) -> None:
        await self._refresh()
        await event.notice(notice)

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
            L(t"Follow matching records"),
            (
                sl.forms.TextField(key="build_kind", label=L(t"Build kind"), required=False, maximum=100),
                sl.forms.TextField(key="record_class", label=L(t"Record class"), required=False, maximum=100),
                sl.forms.TextField(key="version_scope", label=L(t"Version scope"), required=False, maximum=100),
                sl.forms.IntField(key="tag", label=L(t"Showcase tag ID"), required=False, minimum=1),
                sl.forms.TextField(key="tag_value", label=L(t"Exact tag value"), required=False, maximum=100),
            ),
        )

    async def _close(self, event: sl.PressEvent) -> None:
        self.closed = True
        await event.finish()

    def _subscription_list(self) -> sl.TextLike:
        if not self._subscriptions:
            return L(t"_Nothing yet._")
        params: dict[str, object] = {}
        lines: list[str] = []
        for index, subscription in enumerate(self.subscriptions):
            label = f"label_{index}"
            params[label] = self.describe(subscription)
            lines.append(f"**{{{label}}}**\n{self.detail(subscription)}")
        hidden = len(self._subscriptions) - len(self.subscriptions)
        if hidden > 0:
            count = hidden
            params["remainder"] = L(t"…and {count} more.")
            lines.append("{remainder}")
        return sl.text.Message("\n".join(lines), params)

    def describe(self, subscription: NotificationSubscription) -> sl.TextLike:
        return _kind_label(subscription.kind)

    def detail(self, subscription: NotificationSubscription) -> str:
        if subscription.record_filter is not None:
            return _filter_text(subscription.record_filter)
        return f"\x60{subscription.subject_id}\x60"

    def _suspension_note(self) -> sl.TextLike | None:
        if self._preferences is None or self._preferences.dm_suspended_at is None:
            return None
        return L(t"Discord rejected a DM, so DMs are suspended until you re-enable them.")


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
