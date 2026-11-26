"""Authenticated notification preference, subscription, and inbox schemas."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from squid.api.pagination import PageAnchor
from squid.core.notifications import NotificationPreferences, RecordSubscriptionFilter, SubscriptionKind, TagPredicate
from squid.core.notifications.application import FollowedSubject, Inbox, InboxEntry
from squid.core.notifications.domain import NotificationKind, NotifiedBuild, NotifiedRecord


class NotificationPreferencesDetail(BaseModel):
    """Channel preferences and the account's privacy-consent status.

    Consent_pending refers to the same notice as UserMe. Notifications do not have
    a separate consent notice.
    """

    model_config = ConfigDict(extra="forbid")

    revision: int
    consent_pending: bool
    web_enabled: bool
    dm_enabled: bool
    dm_suspended: bool

    @classmethod
    def from_domain(cls, preferences: NotificationPreferences) -> NotificationPreferencesDetail:
        return cls(
            revision=preferences.revision,
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
    criteria: set[Literal["first", "fastest", "smallest", "fastest_smallest", "smallest_fastest"]] = Field(
        default_factory=set
    )
    version_scopes: set[Literal["all_time", "up_to_date"]] = Field(default_factory=set)
    tags: list[TagPredicateInput] = Field(default_factory=list, max_length=8)

    def to_domain(self) -> RecordSubscriptionFilter:
        return RecordSubscriptionFilter(
            build_kinds=frozenset(self.build_kinds),
            criteria=frozenset(self.criteria),
            version_scopes=frozenset(self.version_scopes),
            tags=tuple(tag.to_domain() for tag in self.tags),
        )


class NotificationSubscriptionCreate(BaseModel):
    """A subscription to a creator, a record, or a record filter.

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
    """One caller-owned subscription and how to show it.

    subject_label and subject_href are null for filters, and for a subject that no
    longer exists. filter_summary is a localized description of a filter's predicates.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    kind: SubscriptionKind
    subject_id: UUID | None
    filter: dict[str, object] | None
    subject_label: str | None
    subject_href: str | None
    filter_summary: str | None

    @classmethod
    def from_domain(cls, follow: FollowedSubject) -> NotificationSubscriptionDetail:
        subscription = follow.subscription
        criteria_filter = None if subscription.record_filter is None else subscription.record_filter.as_dict()
        if criteria_filter is not None:
            criteria_filter["criteria"] = criteria_filter.pop("base_titles")
        return cls(
            id=subscription.id,
            kind=subscription.kind,
            subject_id=subscription.subject_id,
            filter=criteria_filter,
            subject_label=follow.label,
            subject_href=follow.href,
            filter_summary=None if follow.filter_summary is None else follow.filter_summary.resolve(),
        )


class NotifiedBuildDetail(BaseModel):
    """A build a notification names; title is null when the build no longer exists."""

    model_config = ConfigDict(extra="forbid")

    id: int
    title: str | None

    @classmethod
    def from_domain(cls, build: NotifiedBuild) -> NotifiedBuildDetail:
        return cls(id=build.id, title=build.title)


class NotifiedRecordDetail(BaseModel):
    """A record a notification names, titled as it was when the notification was created."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str

    @classmethod
    def from_domain(cls, record: NotifiedRecord) -> NotifiedRecordDetail:
        return cls(id=record.id, title=record.title)


class BuildOutcomeNotification(BaseModel):
    """The caller's submission was approved or rejected."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[NotificationKind.BUILD_CONFIRMED, NotificationKind.BUILD_DENIED]
    build: NotifiedBuildDetail


class CreatorBuildNotification(BaseModel):
    """A build by followed creators was approved for the first time."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[NotificationKind.CREATOR_BUILD_CONFIRMED]
    build: NotifiedBuildDetail
    creator_ids: list[UUID]


class StaffSubmissionNotification(BaseModel):
    """A submitted build is awaiting staff review."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[NotificationKind.STAFF_BUILD_SUBMITTED]
    build: NotifiedBuildDetail


class RecordGainedNotification(BaseModel):
    """A build newly holds one or more records."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[NotificationKind.RECORD_GAINED]
    build: NotifiedBuildDetail
    records: list[NotifiedRecordDetail] = Field(min_length=1)


class RecordRetiredNotification(BaseModel):
    """Followed records no longer have any eligible builds."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[NotificationKind.RECORD_RETIRED]
    records: list[NotifiedRecordDetail] = Field(min_length=1)


type NotificationPayload = Annotated[
    BuildOutcomeNotification
    | CreatorBuildNotification
    | StaffSubmissionNotification
    | RecordGainedNotification
    | RecordRetiredNotification,
    Field(discriminator="kind"),
]


class InboxNotificationDetail(BaseModel):
    """One inbox item. href is the site path it links to, if any."""

    model_config = ConfigDict(extra="forbid")

    id: int
    revision: int
    created_at: datetime
    read_at: datetime | None
    href: str | None
    payload: NotificationPayload

    @classmethod
    def from_domain(cls, entry: InboxEntry) -> InboxNotificationDetail:
        message = entry.message
        build = None if message.build is None else NotifiedBuildDetail.from_domain(message.build)
        records = [NotifiedRecordDetail.from_domain(record) for record in message.records]
        payload: NotificationPayload
        match message.kind:
            case NotificationKind.BUILD_CONFIRMED | NotificationKind.BUILD_DENIED:
                assert build is not None
                payload = BuildOutcomeNotification(kind=message.kind, build=build)
            case NotificationKind.CREATOR_BUILD_CONFIRMED:
                assert build is not None
                payload = CreatorBuildNotification(
                    kind=message.kind, build=build, creator_ids=list(message.creator_ids)
                )
            case NotificationKind.STAFF_BUILD_SUBMITTED:
                assert build is not None
                payload = StaffSubmissionNotification(kind=message.kind, build=build)
            case NotificationKind.RECORD_GAINED:
                assert build is not None
                payload = RecordGainedNotification(kind=message.kind, build=build, records=records)
            case NotificationKind.RECORD_RETIRED:
                payload = RecordRetiredNotification(kind=message.kind, records=records)
        return cls(
            id=entry.id,
            revision=entry.revision,
            created_at=entry.created_at.to_stdlib(),
            read_at=None if entry.read_at is None else entry.read_at.to_stdlib(),
            href=message.href,
            payload=payload,
        )


class InboxPage(BaseModel):
    """One inbox page. unread_count covers the whole inbox, whatever the filter."""

    model_config = ConfigDict(extra="forbid")

    items: list[InboxNotificationDetail]
    total: int
    next: PageAnchor | None
    prev: PageAnchor | None
    unread_count: int

    @classmethod
    def from_domain(cls, inbox: Inbox) -> InboxPage:
        page = inbox.page
        return cls(
            items=[InboxNotificationDetail.from_domain(entry) for entry in page.items],
            total=page.total,
            next=None if page.next is None else PageAnchor.from_domain(page.next),
            prev=None if page.prev is None else PageAnchor.from_domain(page.prev),
            unread_count=inbox.unread_count,
        )
