"""Authenticated notification preference, subscription, and inbox schemas."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from squid.notifications import NotificationPreferences, RecordSubscriptionFilter, SubscriptionKind, TagPredicate
from squid.notifications.models import InboxNotification, NotificationSubscription


class NotificationPreferencesDetail(BaseModel):
    """Channel preferences and the account's privacy-consent status.

    Consent_pending refers to the same notice as UserMe. Notifications do not have
    a separate consent notice.
    """

    model_config = ConfigDict(extra="forbid")

    consent_pending: bool
    web_enabled: bool
    dm_enabled: bool
    dm_suspended: bool

    @classmethod
    def from_domain(cls, preferences: NotificationPreferences) -> NotificationPreferencesDetail:
        return cls(
            consent_pending=preferences.consent_pending,
            web_enabled=preferences.web_enabled,
            dm_enabled=preferences.dm_enabled,
            dm_suspended=preferences.dm_suspended_at is not None,
        )


class NotificationPreferenceUpdate(BaseModel):
    """Replacement settings for the web inbox and Discord direct messages.

    An omitted channel defaults to disabled; this model is not a partial update.
    """

    model_config = ConfigDict(extra="forbid")

    web_enabled: bool = False
    dm_enabled: bool = False


class TagPredicateInput(BaseModel):
    """A required tag presence or exact typed value."""

    model_config = ConfigDict(extra="forbid")

    tag_id: int = Field(ge=1)
    operator: Literal["present", "exact"] = "present"
    value: Annotated[str, Field(max_length=128)] | int | float | bool | None = None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        TagPredicate(tag_id=self.tag_id, operator=self.operator, value=self.value)
        return self

    def to_domain(self) -> TagPredicate:
        return TagPredicate(tag_id=self.tag_id, operator=self.operator, value=self.value)


class RecordFilterInput(BaseModel):
    """Broad structured predicates for record-gain subscriptions."""

    model_config = ConfigDict(extra="forbid")

    build_kinds: set[Literal["door", "entrance", "extender", "utility"]] = Field(default_factory=set)
    base_titles: set[Literal["first", "fastest", "smallest", "fastest_smallest", "smallest_fastest"]] = Field(
        default_factory=set
    )
    version_scopes: set[Literal["all_time", "up_to_date"]] = Field(default_factory=set)
    tags: list[TagPredicateInput] = Field(default_factory=list, max_length=8)

    def to_domain(self) -> RecordSubscriptionFilter:
        return RecordSubscriptionFilter(
            build_kinds=frozenset(self.build_kinds),
            base_titles=frozenset(self.base_titles),
            version_scopes=frozenset(self.version_scopes),
            tags=tuple(tag.to_domain() for tag in self.tags),
        )


class NotificationSubscriptionCreate(BaseModel):
    """A subscription to a creator, a record title, or a record filter.

    Creator and record subscriptions require subject_id and forbid filter.
    Record-filter subscriptions require filter and forbid subject_id.
    """

    model_config = ConfigDict(extra="forbid")

    kind: SubscriptionKind
    subject_id: UUID | None = None
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


class NotificationSubscriptionDetail(BaseModel):
    """One enabled caller-owned subscription."""

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: SubscriptionKind
    subject_id: UUID | None
    filter: dict[str, object] | None

    @classmethod
    def from_domain(cls, subscription: NotificationSubscription) -> NotificationSubscriptionDetail:
        return cls(
            id=subscription.id,
            kind=subscription.kind,
            subject_id=subscription.subject_id,
            filter=None if subscription.record_filter is None else subscription.record_filter.as_dict(),
        )


class InboxNotificationDetail(BaseModel):
    """One web inbox item."""

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: str
    payload: dict[str, object]
    created_at: datetime
    read_at: datetime | None

    @classmethod
    def from_domain(cls, notification: InboxNotification) -> InboxNotificationDetail:
        return cls(
            id=notification.id,
            kind=notification.kind.value,
            payload=notification.payload,
            created_at=notification.created_at.to_stdlib(),
            read_at=None if notification.read_at is None else notification.read_at.to_stdlib(),
        )
