"""Describe who receives event notifications and how they are delivered.

A subscription follows a creator, a record title, or a filter selecting records.
A matching event produces recipient-specific candidates. Account consent and
channel preferences determine which candidates become stored notifications.
Each stored notification supplies the same message data to the web inbox and,
when enabled, a separate Discord DM delivery.

A source key identifies one event-derived notification for one account, so event
retries can reuse it. A delivery claim temporarily reserves one DM for a sender;
it carries the message snapshot and identifiers needed to report that attempt's
result without overwriting a newer claim.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast, override
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, PositiveInt
from pydantic import ValidationError as SchemaValidationError
from whenever import Instant

from squid.core.errors import DataIntegrityError, ValidationError
from squid.core.i18n import tr
from squid.core.validation import BelowMinimum, BelowMinimumParams, FieldErrorCode, SimpleIssue
from squid_i18n import Text, md_tr

_BUILD_KINDS = frozenset({"door", "entrance", "extender", "utility"})
_RECORD_CRITERIA = frozenset({"first", "fastest", "smallest", "fastest_smallest", "smallest_fastest"})
_VERSION_SCOPES = frozenset({"all_time", "up_to_date"})


class SubscriptionKind(StrEnum):
    """Durable subject types an account may follow."""

    CREATOR = "creator"
    RECORD = "record"
    RECORD_FILTER = "record_filter"


class NotificationKind(StrEnum):
    """Inbox and delivery message categories."""

    BUILD_CONFIRMED = "build_confirmed"
    BUILD_DENIED = "build_denied"
    CREATOR_BUILD_CONFIRMED = "creator_build_confirmed"
    RECORD_GAINED = "record_gained"
    RECORD_RETIRED = "record_retired"
    STAFF_BUILD_SUBMITTED = "staff_build_submitted"


@dataclass(frozen=True, slots=True)
class NotificationSource:
    """Identify the event and notification kind before choosing its recipient.

    `key_for` adds the account ID to make the source key used for deduplication.
    Record-gain notifications also include the build ID because one record-run
    event can produce separate messages for several builds. Account merges use
    the same construction when moving a message to the surviving recipient.
    """

    event_id: int
    kind: NotificationKind
    build_id: int | None = None

    def __post_init__(self) -> None:
        if self.kind is NotificationKind.RECORD_GAINED and (
            isinstance(self.build_id, bool) or not isinstance(self.build_id, int)
        ):
            msg = "A record notification source key cannot be validated without its build identifier."
            raise DataIntegrityError(msg)

    def key_for(self, account_id: int) -> str:
        """Construct the canonical recipient-bound idempotency key."""
        if self.kind is NotificationKind.RECORD_GAINED:
            return f"event:{self.event_id}:record-build:{self.build_id}:account:{account_id}"
        label = {
            NotificationKind.STAFF_BUILD_SUBMITTED: "staff",
            NotificationKind.BUILD_CONFIRMED: "owner",
            NotificationKind.BUILD_DENIED: "owner",
            NotificationKind.CREATOR_BUILD_CONFIRMED: "creator",
            NotificationKind.RECORD_RETIRED: "record-retired",
        }[self.kind]
        return f"event:{self.event_id}:{label}:{account_id}"

    @classmethod
    def parse(
        cls,
        source_key: str,
        *,
        event_id: int,
        kind: NotificationKind,
        account_id: int,
        payload: Mapping[str, object],
    ) -> NotificationSource:
        """Validate the key against its event, payload, and owner before changing its recipient."""
        build_id = payload.get("build_id") if kind is NotificationKind.RECORD_GAINED else None
        source = cls(event_id, kind, build_id=cast(int | None, build_id))
        if source_key != source.key_for(account_id):
            msg = "A notification source key does not match its kind, event, payload, and recipient."
            raise DataIntegrityError(msg, context={"source_key": source_key})
        return source


@dataclass(frozen=True, slots=True)
class NotifiedBuild:
    """A build a notification is about. The title is None when it was not looked up or no longer exists."""

    id: int
    title: str | None = None


@dataclass(frozen=True, slots=True)
class NotifiedRecord:
    """A record named by a notification, with its title when the notification was created."""

    id: UUID
    title: str


class _StoredRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title_id: UUID
    title: str


class _StoredPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=False)
    build_id: PositiveInt | None = Field(default=None, strict=True)
    records: list[_StoredRecord] = Field(default_factory=list)
    creator_ids: list[UUID] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    """What one stored notification says, parsed from its persisted payload.

    Every kind except `RECORD_RETIRED` names a build. Record kinds list at least one
    record. `creator_ids` lists the followed creators of a `CREATOR_BUILD_CONFIRMED` build.
    """

    kind: NotificationKind
    build: NotifiedBuild | None = None
    records: tuple[NotifiedRecord, ...] = ()
    creator_ids: tuple[UUID, ...] = ()

    @classmethod
    def from_payload(
        cls, kind: NotificationKind, payload: Mapping[str, object], build_titles: Mapping[int, str] | None = None
    ) -> NotificationMessage:
        """Parse a stored payload, raising DataIntegrityError when it lacks what its kind requires."""
        try:
            stored = _StoredPayload.model_validate(payload)
        except SchemaValidationError as error:
            msg = "A stored notification payload is malformed."
            raise DataIntegrityError(msg, context={"kind": kind.value}) from error
        names_build = kind is not NotificationKind.RECORD_RETIRED
        lists_records = kind in {NotificationKind.RECORD_GAINED, NotificationKind.RECORD_RETIRED}
        if names_build != (stored.build_id is not None) or lists_records != bool(stored.records):
            msg = "A stored notification payload does not match its kind."
            raise DataIntegrityError(msg, context={"kind": kind.value})
        return cls(
            kind,
            build=None
            if stored.build_id is None
            else NotifiedBuild(stored.build_id, (build_titles or {}).get(stored.build_id)),
            records=tuple(NotifiedRecord(record.title_id, record.title) for record in stored.records),
            creator_ids=tuple(stored.creator_ids),
        )

    @property
    def href(self) -> str | None:
        """The site path this message leads to: its build, or its only record."""
        if self.build is not None:
            return f"/builds/{self.build.id}"
        if len(self.records) == 1:
            return f"/records/{self.records[0].id}"
        return None


@dataclass(frozen=True, slots=True)
class InboxVisibility:
    """The notification kinds one authenticated inbox request may observe."""

    include_staff: bool = False


DEFAULT_INBOX_VISIBILITY = InboxVisibility()


@dataclass(frozen=True, slots=True)
class NotificationCandidate:
    """A possible notification whose recipient preferences have not yet been checked."""

    account_id: int
    source_key: str
    kind: NotificationKind
    payload: Mapping[str, object]
    subscription_ids: tuple[int, ...] | None = None


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    """An account's notification channel switches, and whether it may use them yet.

    consent_pending comes from the account's privacy-consent policy, including any
    exemption, rather than a separate notification notice. Consent permits opt-in;
    it does not enable either channel automatically.
    """

    account_id: int
    consent_pending: bool = True
    web_enabled: bool = False
    dm_enabled: bool = False
    dm_suspended_at: Instant | None = None
    revision: int = 0

    @property
    def has_current_consent(self) -> bool:
        """Whether the account may enable and receive notifications at all."""
        return not self.consent_pending


@dataclass(frozen=True, slots=True)
class TagPredicate:
    """A required build tag, optionally with an exact typed value."""

    tag_id: int
    operator: str = "present"
    value: str | int | float | bool | None = None

    def __post_init__(self) -> None:
        if self.tag_id < 1:
            raise ValidationError(
                tr(t"notifications-tag-id-positive = tag_id must be positive"),
                issues=(BelowMinimum(field="tag_id", params=BelowMinimumParams(minimum=1)),),
            )
        if self.operator not in {"present", "exact"}:
            raise ValidationError(
                tr(t"notifications-tag-predicate-operator = tag predicate operator must be 'present' or 'exact'"),
                issues=(SimpleIssue(field="operator", code=FieldErrorCode.UNKNOWN_OPTION),),
            )
        if self.operator == "present" and self.value is not None:
            raise ValidationError(
                tr(t"notifications-presence-predicates-include = presence predicates cannot include a value"),
                issues=(SimpleIssue(field="value", code=FieldErrorCode.INCOMPATIBLE_FIELDS),),
            )
        if self.operator == "exact" and self.value is None:
            raise ValidationError(
                tr(t"notifications-exact-predicate-value-required = exact predicates require a value"),
                issues=(SimpleIssue(field="value", code=FieldErrorCode.REQUIRED),),
            )

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TagPredicate):
            return NotImplemented
        # JSONB distinguishes booleans from numbers, while Python equates True and 1.
        return (
            self.tag_id == other.tag_id
            and self.operator == other.operator
            and isinstance(self.value, bool) == isinstance(other.value, bool)
            and self.value == other.value
        )

    def summary(self, tag_name: str | None) -> Text:
        """Describe the predicate, naming the tag by ID when its name is unknown."""
        tag_id, value = self.tag_id, self.value
        name = tag_name or tr(t"notifications-filter-unknown-tag = Tag #{tag_id!s}")
        if self.operator == "present":
            return tr(t"notifications-filter-tagged = Tagged {name}")
        return tr(t"notifications-filter-tag-value = {name}: {value!s}")

    def as_dict(self) -> dict[str, JsonValue]:
        """Return the tag ID, operator, and optional value for persistence."""
        result: dict[str, JsonValue] = {"tag_id": self.tag_id, "operator": self.operator}
        if self.value is not None:
            result["value"] = self.value
        return result


@dataclass(frozen=True, slots=True)
class RecordSubscriptionFilter:
    """Conditions for following record gains.

    Empty fields impose no restriction. Nonempty fields must all match; values
    within each field are alternatives, and every tag predicate is required.
    """

    build_kinds: frozenset[str] = frozenset()
    criteria: frozenset[str] = frozenset()
    version_scopes: frozenset[str] = frozenset()
    tags: tuple[TagPredicate, ...] = ()

    def __post_init__(self) -> None:
        if not any((self.build_kinds, self.criteria, self.version_scopes, self.tags)):
            raise ValidationError(
                tr(t"notifications-record-filter-predicate-limit = a record filter must contain at least one predicate")
            )
        _validate_values(self.build_kinds, _BUILD_KINDS, "build kind")
        _validate_values(self.criteria, _RECORD_CRITERIA, "record criterion")
        _validate_values(self.version_scopes, _VERSION_SCOPES, "version scope")
        if len({predicate.tag_id for predicate in self.tags}) != len(self.tags):
            raise ValidationError(
                tr(
                    t"notifications-record-filter-single-predicate = record filters may "
                    t"contain only one predicate per tag"
                )
            )
        object.__setattr__(self, "tags", tuple(sorted(self.tags, key=lambda tag: tag.tag_id)))

    def summary(self, tag_names: Mapping[int, str]) -> Text:
        """Describe the filter for readers, such as "Doors, Extenders · Fastest · Tagged Slim"."""
        groups = [
            Text.join(", ", [label(value) for value in sorted(values)])
            for values, label in (
                (self.build_kinds, _build_kind_label),
                (self.criteria, _criterion_label),
                (self.version_scopes, _version_scope_label),
            )
            if values
        ]
        groups.extend(predicate.summary(tag_names.get(predicate.tag_id)) for predicate in self.tags)
        return Text.join(" · ", groups)

    def as_dict(self) -> dict[str, JsonValue]:
        """Serialize the filter into a stable JSON shape."""
        return {
            "build_kinds": [*sorted(self.build_kinds)],
            "base_titles": [*sorted(self.criteria)],
            "version_scopes": [*sorted(self.version_scopes)],
            "tags": [predicate.as_dict() for predicate in self.tags],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RecordSubscriptionFilter:
        """Parse stored JSON, raising DataIntegrityError for invalid filter data."""
        raw_tags_value = value.get("tags", [])
        if not isinstance(raw_tags_value, list):
            raise DataIntegrityError(tr(t"notifications-tags-list = tags must be a list"))
        raw_tags = cast(list[object], raw_tags_value)
        tags: list[TagPredicate] = []
        for raw in raw_tags:
            if not isinstance(raw, dict):
                raise DataIntegrityError(tr(t"notifications-tag-predicates-objects = tag predicates must be objects"))
            raw_predicate = cast(dict[str, object], raw)
            tag_id = raw_predicate.get("tag_id")
            operator = raw_predicate.get("operator", "present")
            predicate_value = raw_predicate.get("value")
            if isinstance(tag_id, bool) or not isinstance(tag_id, int) or not isinstance(operator, str):
                raise DataIntegrityError(tr(t"notifications-domain-invalid-tag-predicate = invalid tag predicate"))
            if predicate_value is not None and not isinstance(predicate_value, (str, int, float, bool)):
                raise DataIntegrityError(tr(t"notifications-invalid-exact-tag-value = invalid exact tag value"))
            try:
                tags.append(TagPredicate(tag_id=tag_id, operator=operator, value=predicate_value))
            except ValidationError as error:
                raise DataIntegrityError(
                    tr(
                        t"notifications-persisted-tag-predicate = "
                        t"persisted tag predicate violates the notification contract"
                    )
                ) from error
        try:
            return cls(
                build_kinds=_string_set(value.get("build_kinds", []), "build_kinds"),
                criteria=_string_set(value.get("base_titles", []), "criteria"),
                version_scopes=_string_set(value.get("version_scopes", []), "version_scopes"),
                tags=tuple(tags),
            )
        except ValidationError as error:
            raise DataIntegrityError(
                tr(
                    t"notifications-persisted-record-filter = "
                    t"persisted record filter violates the notification contract"
                )
            ) from error


@dataclass(frozen=True, slots=True)
class SubscriptionTarget:
    """The subject or filter that identifies an equivalent follow within one account."""

    kind: SubscriptionKind
    subject_id: UUID | None = None
    record_filter: RecordSubscriptionFilter | None = None

    def __post_init__(self) -> None:
        if self.kind is SubscriptionKind.RECORD_FILTER:
            if self.subject_id is not None or self.record_filter is None:
                message = "A record-filter follow requires only a structured filter."
                raise ValidationError(message)
        elif self.subject_id is None or self.record_filter is not None:
            message = "A creator or record follow requires only a subject ID."
            raise ValidationError(message)

    @staticmethod
    def require_unique(targets: Iterable[SubscriptionTarget]) -> None:
        seen: set[SubscriptionTarget] = set()
        for target in targets:
            if target in seen:
                message = "An equivalent follow is already enabled."
                raise ValidationError(message)
            seen.add(target)


@dataclass(frozen=True, slots=True)
class PendingNotificationDelivery:
    """A claimed Discord DM with its recipient and message snapshot.

    A sender passes this value back when reporting success or failure. The stored
    generation and claim_token must still match, so a slow sender cannot overwrite
    a later attempt or a delivery reassigned during an account merge.

    The generation changes on account merge; the token changes on each claim.
    The nonce is supplied to Discord and stays the same across ordinary retries,
    but changes when accounts merge. It does not guarantee exactly-once delivery.
    """

    id: int
    generation: int
    discord_id: int
    nonce: UUID
    claim_token: UUID
    attempts: int
    kind: NotificationKind
    payload: dict[str, object]

    @property
    def message(self) -> NotificationMessage:
        return NotificationMessage.from_payload(self.kind, self.payload)


def _build_kind_label(kind: str) -> Text:
    return tr(t"""
        notifications-filter-build-kind = {{ {kind} ->
            [door] Doors
            [entrance] Entrances
            [extender] Extenders
           *[utility] Utilities
        }}
    """)


def _criterion_label(criterion: str) -> Text:
    return tr(t"""
        notifications-filter-criterion = {{ {criterion} ->
            [first] First
            [fastest] Fastest
            [smallest] Smallest
            [fastest_smallest] Fastest smallest
           *[smallest_fastest] Smallest fastest
        }}
    """)


def _version_scope_label(scope: str) -> Text:
    return tr(t"""
        notifications-filter-version-scope = {{ {scope} ->
            [all_time] All time
           *[up_to_date] Up to date
        }}
    """)


def _string_set(value: object, name: str) -> frozenset[str]:
    if not isinstance(value, list):
        raise DataIntegrityError(
            md_tr(t"notifications-list-nonempty-strings = {name} must be a list of non-empty strings")
        )
    items = cast(list[object], value)
    if not all(isinstance(item, str) and item for item in items):
        raise DataIntegrityError(
            md_tr(t"notifications-list-nonempty-strings = {name} must be a list of non-empty strings")
        )
    return frozenset(cast(str, item) for item in items)


def _validate_values(values: frozenset[str], allowed: frozenset[str], name: str) -> None:
    invalid = values - allowed
    if invalid:
        value = sorted(invalid)[0]
        raise ValidationError(md_tr(t"notifications-domain-unsupported = unsupported {name}: {value}"))
