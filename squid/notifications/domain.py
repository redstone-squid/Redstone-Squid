"""Notification domain values and subscription validation."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from uuid import UUID

from whenever import Instant

from squid.core.errors import DataIntegrityError, ValidationError
from squid.core.i18n import tr

_BUILD_KINDS = frozenset({"door", "entrance", "extender", "utility"})
_RECORD_CLASSES = frozenset({"first", "fastest", "smallest", "fastest_smallest", "smallest_fastest"})
_VERSION_SCOPES = frozenset({"all_time", "current"})


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
class InboxVisibility:
    """The notification kinds one authenticated inbox request may observe."""

    include_staff: bool = False


DEFAULT_INBOX_VISIBILITY = InboxVisibility()


@dataclass(frozen=True, slots=True)
class NotificationCandidate:
    """One recipient-specific notification awaiting preference materialization."""

    account_id: int
    source_key: str
    kind: NotificationKind
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    """An account's notification channel switches, and whether it may use them yet.

    There is no notification-specific notice any more. Notifications are described by the one
    privacy notice, so `consent_pending` is the account's answer to that, read from the account
    rather than stored again here. The channel switches stay independent: accepting the notice
    permits notifications, it does not turn any on.
    """

    account_id: int
    consent_pending: bool = True
    web_enabled: bool = False
    dm_enabled: bool = False
    dm_suspended_at: Instant | None = None

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
            raise ValidationError(tr(t"tag_id must be positive"))
        if self.operator not in {"present", "exact"}:
            raise ValidationError(tr(t"tag predicate operator must be 'present' or 'exact'"))
        if self.operator == "present" and self.value is not None:
            raise ValidationError(tr(t"presence predicates cannot include a value"))
        if self.operator == "exact" and self.value is None:
            raise ValidationError(tr(t"exact predicates require a value"))

    def as_dict(self) -> dict[str, object]:
        """Serialize the predicate into JSON-safe persistence data."""
        result: dict[str, object] = {"tag_id": self.tag_id, "operator": self.operator}
        if self.value is not None:
            result["value"] = self.value
        return result


@dataclass(frozen=True, slots=True)
class RecordSubscriptionFilter:
    """Structured record-gain predicates; omitted fields are wildcards."""

    build_kinds: frozenset[str] = frozenset()
    record_classes: frozenset[str] = frozenset()
    version_scopes: frozenset[str] = frozenset()
    tags: tuple[TagPredicate, ...] = ()

    def __post_init__(self) -> None:
        if not any((self.build_kinds, self.record_classes, self.version_scopes, self.tags)):
            raise ValidationError(tr(t"a record filter must contain at least one predicate"))
        _validate_values(self.build_kinds, _BUILD_KINDS, "build kind")
        _validate_values(self.record_classes, _RECORD_CLASSES, "record class")
        _validate_values(self.version_scopes, _VERSION_SCOPES, "version scope")
        if len({predicate.tag_id for predicate in self.tags}) != len(self.tags):
            raise ValidationError(tr(t"record filters may contain only one predicate per tag"))

    def as_dict(self) -> dict[str, object]:
        """Serialize the filter into a stable JSON shape."""
        return {
            "build_kinds": sorted(self.build_kinds),
            "record_classes": sorted(self.record_classes),
            "version_scopes": sorted(self.version_scopes),
            "tags": [predicate.as_dict() for predicate in sorted(self.tags, key=lambda item: item.tag_id)],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RecordSubscriptionFilter:
        """Parse trusted persisted JSON, raising when it no longer matches the contract."""
        raw_tags_value = value.get("tags", [])
        if not isinstance(raw_tags_value, list):
            raise DataIntegrityError(tr(t"tags must be a list"))
        raw_tags = cast(list[object], raw_tags_value)
        tags: list[TagPredicate] = []
        for raw in raw_tags:
            if not isinstance(raw, dict):
                raise DataIntegrityError(tr(t"tag predicates must be objects"))
            raw_predicate = cast(dict[str, object], raw)
            tag_id = raw_predicate.get("tag_id")
            operator = raw_predicate.get("operator", "present")
            predicate_value = raw_predicate.get("value")
            if isinstance(tag_id, bool) or not isinstance(tag_id, int) or not isinstance(operator, str):
                raise DataIntegrityError(tr(t"invalid tag predicate"))
            if predicate_value is not None and not isinstance(predicate_value, (str, int, float, bool)):
                raise DataIntegrityError(tr(t"invalid exact tag value"))
            tags.append(TagPredicate(tag_id=tag_id, operator=operator, value=predicate_value))
        return cls(
            build_kinds=_string_set(value.get("build_kinds", []), "build_kinds"),
            record_classes=_string_set(value.get("record_classes", []), "record_classes"),
            version_scopes=_string_set(value.get("version_scopes", []), "version_scopes"),
            tags=tuple(tags),
        )


@dataclass(frozen=True, slots=True)
class NotificationSubscription:
    """One enabled subscription owned by an account."""

    id: int
    account_id: int
    kind: SubscriptionKind
    subject_id: UUID | None
    record_filter: RecordSubscriptionFilter | None
    created_at: Instant


@dataclass(frozen=True, slots=True)
class InboxNotification:
    """One materialized inbox item."""

    id: int
    kind: NotificationKind
    payload: dict[str, object]
    created_at: Instant
    read_at: Instant | None = None


@dataclass(frozen=True, slots=True)
class PendingNotificationDelivery:
    """One fenced Discord DM claim with its materialized message data."""

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
        raise DataIntegrityError(tr(t"{name} must be a list of non-empty strings"))
    items = cast(list[object], value)
    if not all(isinstance(item, str) and item for item in items):
        raise DataIntegrityError(tr(t"{name} must be a list of non-empty strings"))
    return frozenset(cast(str, item) for item in items)


def _validate_values(values: frozenset[str], allowed: frozenset[str], name: str) -> None:
    invalid = values - allowed
    if invalid:
        value = sorted(invalid)[0]
        raise ValidationError(tr(t"unsupported {name}: {value}"))
