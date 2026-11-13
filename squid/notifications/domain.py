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

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from uuid import UUID

from whenever import Instant

from squid.core.errors import DataIntegrityError, ValidationError
from squid.core.i18n import tr
from squid_i18n import md_tr

_BUILD_KINDS = frozenset({"door", "entrance", "extender", "utility"})
_BASE_TITLES = frozenset({"first", "fastest", "smallest", "fastest_smallest", "smallest_fastest"})
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
            raise ValidationError(tr(t"notifications-tag-id-positive = tag_id must be positive"))
        if self.operator not in {"present", "exact"}:
            raise ValidationError(
                tr(t"notifications-tag-predicate-operator = tag predicate operator must be 'present' or 'exact'")
            )
        if self.operator == "present" and self.value is not None:
            raise ValidationError(
                tr(t"notifications-presence-predicates-include = presence predicates cannot include a value")
            )
        if self.operator == "exact" and self.value is None:
            raise ValidationError(
                tr(t"notifications-exact-predicate-value-required = exact predicates require a value")
            )

    def as_dict(self) -> dict[str, object]:
        """Return the tag ID, operator, and optional value for persistence."""
        result: dict[str, object] = {"tag_id": self.tag_id, "operator": self.operator}
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
    base_titles: frozenset[str] = frozenset()
    version_scopes: frozenset[str] = frozenset()
    tags: tuple[TagPredicate, ...] = ()

    def __post_init__(self) -> None:
        if not any((self.build_kinds, self.base_titles, self.version_scopes, self.tags)):
            raise ValidationError(
                tr(t"notifications-record-filter-predicate-limit = a record filter must contain at least one predicate")
            )
        _validate_values(self.build_kinds, _BUILD_KINDS, "build kind")
        _validate_values(self.base_titles, _BASE_TITLES, "base title")
        _validate_values(self.version_scopes, _VERSION_SCOPES, "version scope")
        if len({predicate.tag_id for predicate in self.tags}) != len(self.tags):
            raise ValidationError(
                tr(
                    t"notifications-record-filter-single-predicate = record filters may "
                    t"contain only one predicate per tag"
                )
            )

    def as_dict(self) -> dict[str, object]:
        """Serialize the filter into a stable JSON shape."""
        return {
            "build_kinds": sorted(self.build_kinds),
            "base_titles": sorted(self.base_titles),
            "version_scopes": sorted(self.version_scopes),
            "tags": [predicate.as_dict() for predicate in sorted(self.tags, key=lambda item: item.tag_id)],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RecordSubscriptionFilter:
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
                base_titles=_string_set(value.get("base_titles", []), "base_titles"),
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
