"""Notification domain values and subscription validation."""

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
    CREATOR = "creator"
    RECORD = "record"
    RECORD_FILTER = "record_filter"


class NotificationKind(StrEnum):
    BUILD_CONFIRMED = "build_confirmed"
    BUILD_DENIED = "build_denied"
    CREATOR_BUILD_CONFIRMED = "creator_build_confirmed"
    RECORD_GAINED = "record_gained"
    STAFF_BUILD_SUBMITTED = "staff_build_submitted"


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    """An account's channel switches.

    `consent_pending` is the account's privacy-notice state, read from the account rather than
    stored here. Accepting the notice permits notifications; it turns none on.
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
    """A required build tag, optionally pinned to an exact value; raises `ValidationError` when malformed."""

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
        result: dict[str, object] = {"tag_id": self.tag_id, "operator": self.operator}
        if self.value is not None:
            result["value"] = self.value
        return result


@dataclass(frozen=True, slots=True)
class RecordSubscriptionFilter:
    """Record-gain predicates; omitted fields are wildcards. Raises `ValidationError` when empty or malformed."""

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
        """Serialize with sorted members so equal filters compare equal under the database's unique index."""
        return {
            "build_kinds": sorted(self.build_kinds),
            "record_classes": sorted(self.record_classes),
            "version_scopes": sorted(self.version_scopes),
            "tags": [predicate.as_dict() for predicate in sorted(self.tags, key=lambda item: item.tag_id)],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RecordSubscriptionFilter:
        """Parse persisted JSON; raises `DataIntegrityError` on a bad shape and `ValidationError` on bad values."""
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
    id: int
    account_id: int
    kind: SubscriptionKind
    subject_id: UUID | None
    record_filter: RecordSubscriptionFilter | None
    created_at: Instant


@dataclass(frozen=True, slots=True)
class InboxNotification:
    id: int
    kind: NotificationKind
    payload: dict[str, object]
    created_at: Instant
    read_at: Instant | None = None


@dataclass(frozen=True, slots=True)
class PendingNotificationDelivery:
    """A claimed DM; `generation` and `claim_token` fence its completion and failure updates."""

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
