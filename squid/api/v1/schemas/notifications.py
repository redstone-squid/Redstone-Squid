"""Authenticated notification preference, subscription, and inbox schemas."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from squid.api.schema import ApiSchema
from squid.notifications import (
    InboxNotification,
    NotificationPreferences,
    NotificationSubscription,
    RecordSubscriptionFilter,
    SubscriptionKind,
    TagPredicate,
)


class NotificationPreferencesDetail(ApiSchema):
    """Independent channel switches, and whether the account may use them yet."""

    model_config = ConfigDict(extra="forbid")

    consent_pending: bool = Field(
        description="True while the account still owes the current privacy notice, which refuses every attempt to "
        "enable a channel. The same fact reported by `UserMe`; there is no notification-specific notice."
    )
    web_enabled: bool = Field(description="Whether new notifications reach the web inbox.")
    dm_enabled: bool = Field(description="Whether new notifications are sent as Discord direct messages.")
    dm_suspended: bool = Field(
        description="True after a direct message failed to deliver, which also turns `dm_enabled` off. Enabling DMs "
        "again clears it."
    )

    @classmethod
    def from_domain(cls, preferences: NotificationPreferences) -> NotificationPreferencesDetail:
        return cls(
            consent_pending=preferences.consent_pending,
            web_enabled=preferences.web_enabled,
            dm_enabled=preferences.dm_enabled,
            dm_suspended=preferences.dm_suspended_at is not None,
        )


class NotificationPreferenceUpdate(ApiSchema):
    """A complete pair of independently configurable notification channels.

    Both switches are replaced, so an omitted one defaults to false and turns that channel off.
    """

    model_config = ConfigDict(extra="forbid")

    web_enabled: bool = False
    dm_enabled: bool = False


class TagPredicateInput(ApiSchema):
    """A required tag presence or exact typed value."""

    model_config = ConfigDict(extra="forbid")

    tag_id: int = Field(ge=1, description="Id of a tag published by `/v1/tags`.")
    operator: Literal["present", "exact"] = Field(
        default="present",
        description="`present` matches any assignment of the tag; `exact` additionally requires `value` to equal the "
        "assigned value.",
    )
    value: Annotated[str, Field(max_length=128)] | int | float | bool | None = Field(
        default=None, description="Required when `operator` is `exact`, and rejected when it is `present`."
    )

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        TagPredicate(tag_id=self.tag_id, operator=self.operator, value=self.value)
        return self

    def to_domain(self) -> TagPredicate:
        return TagPredicate(tag_id=self.tag_id, operator=self.operator, value=self.value)


class RecordFilterInput(ApiSchema):
    """Broad structured predicates for record-gain subscriptions.

    An empty set is a wildcard over that facet, but at least one of the four must be non-empty. A
    record matches when it satisfies every non-empty facet and every tag predicate.
    """

    model_config = ConfigDict(extra="forbid")

    build_kinds: set[Literal["door", "entrance", "extender", "utility"]] = Field(default_factory=set)
    record_classes: set[Literal["first", "fastest", "smallest", "fastest_smallest", "smallest_fastest"]] = Field(
        default_factory=set
    )
    version_scopes: set[Literal["all_time", "current"]] = Field(
        default_factory=set,
        description="`all_time` records span every version; `current` records are scoped to the newest one.",
    )
    tags: list[TagPredicateInput] = Field(
        default_factory=list, max_length=8, description="At most one predicate per `tag_id`."
    )

    def to_domain(self) -> RecordSubscriptionFilter:
        return RecordSubscriptionFilter(
            build_kinds=frozenset(self.build_kinds),
            record_classes=frozenset(self.record_classes),
            version_scopes=frozenset(self.version_scopes),
            tags=tuple(tag.to_domain() for tag in self.tags),
        )


class NotificationSubscriptionCreate(ApiSchema):
    """A creator, exact-record, or record-filter subscription request.

    `subject_id` and `filter` are mutually exclusive: `creator` and `record` require the former,
    `record_filter` requires the latter, and sending both or neither is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    kind: SubscriptionKind
    subject_id: UUID | None = Field(
        default=None,
        description="A creator's `id` for `creator`, or a record's `competition_id` for `record`. The target must "
        "already exist.",
    )
    filter: RecordFilterInput | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind is SubscriptionKind.RECORD_FILTER:
            if self.subject_id is not None or self.filter is None:
                msg = "record_filter requires filter and forbids subject_id"
                raise ValueError(msg)
            self.filter.to_domain()
        elif self.subject_id is None or self.filter is not None:
            msg = "creator and record require subject_id and forbid filter"
            raise ValueError(msg)
        return self


class NotificationSubscriptionDetail(ApiSchema):
    """One enabled caller-owned subscription."""

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: SubscriptionKind
    subject_id: UUID | None = Field(description="Null exactly when `kind` is `record_filter`.")
    filter: dict[str, object] | None = Field(
        description="The stored `RecordFilterInput` with every member sorted. Null unless `kind` is `record_filter`."
    )

    @classmethod
    def from_domain(cls, subscription: NotificationSubscription) -> NotificationSubscriptionDetail:
        return cls(
            id=subscription.id,
            kind=subscription.kind,
            subject_id=subscription.subject_id,
            filter=None if subscription.record_filter is None else subscription.record_filter.as_dict(),
        )


class InboxNotificationDetail(ApiSchema):
    """One web inbox item."""

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: str = Field(
        description="One of `build_confirmed`, `build_denied`, `creator_build_confirmed`, `record_gained` or "
        "`staff_build_submitted`, which decides the shape of `payload`."
    )
    payload: dict[str, object] = Field(description="Kind-specific fields; treat unknown keys as additive.")
    created_at: datetime
    read_at: datetime | None = Field(description="Null while the item is unread.")

    @classmethod
    def from_domain(cls, notification: InboxNotification) -> InboxNotificationDetail:
        return cls(
            id=notification.id,
            kind=notification.kind.value,
            payload=notification.payload,
            created_at=notification.created_at.to_stdlib(),
            read_at=None if notification.read_at is None else notification.read_at.to_stdlib(),
        )
