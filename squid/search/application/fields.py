"""Allowlisted search field definitions and value coercion."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
from enum import StrEnum

from squid.core.errors import InvalidStateError, ValidationError
from squid.core.i18n import _
from squid.search.domain.query import ScalarValue


class FieldType(StrEnum):
    """Storage-independent field value types."""

    TEXT = "text"
    NUMBER = "number"
    TIMESTAMP = "timestamp"
    BOOLEAN = "boolean"


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    """A queryable field exposed by the search API."""

    name: str
    value_type: FieldType
    supports_range: bool = False
    aliases: tuple[str, ...] = ()
    storage_name: str | None = None
    supports_sort: bool = False
    unit_scales: tuple[tuple[str, Decimal], ...] = ()
    numeric_step: Decimal | None = None


class FieldRegistry:
    """Resolve field names and coerce values without exposing persistence identifiers."""

    def __init__(self, fields: Iterable[FieldDefinition]) -> None:
        self._fields: dict[str, FieldDefinition] = {}
        for field in fields:
            for name in (field.name, *field.aliases):
                key = name.casefold()
                if key in self._fields:
                    msg = _("duplicate search field or alias: {name}")
                    raise InvalidStateError(msg, message_params={"name": name})
                self._fields[key] = field

    def resolve(self, name: str) -> FieldDefinition | None:
        """Resolve a public field name or alias."""
        return self._fields.get(name.casefold())

    def suggestions(self, name: str, *, limit: int = 3) -> tuple[str, ...]:
        """Suggest canonical public names for a misspelled field."""
        matches = get_close_matches(name.casefold(), self._fields, n=limit, cutoff=0.55)
        return tuple(dict.fromkeys(self._fields[match].name for match in matches))

    @staticmethod
    def coerce(field: FieldDefinition, raw: str) -> ScalarValue:
        """Coerce a parsed literal according to an allowlisted field type."""
        if field.value_type is FieldType.TEXT:
            return raw
        if field.value_type is FieldType.NUMBER:
            try:
                number = _coerce_decimal(raw, field)
            except (InvalidOperation, ValueError) as error:
                msg = _("{field_name} expects a number")
                raise ValidationError(msg, message_params={"field_name": field.name}) from error
            return number
        if field.value_type is FieldType.TIMESTAMP:
            try:
                return datetime.fromisoformat(raw).isoformat()
            except ValueError as error:
                msg = _("{field_name} expects an ISO-8601 date or timestamp")
                raise ValidationError(msg, message_params={"field_name": field.name}) from error
        lowered = raw.casefold()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
        msg = _("{field_name} expects a boolean")
        raise ValidationError(msg, message_params={"field_name": field.name})

    @property
    def names(self) -> tuple[str, ...]:
        """Return canonical registered names."""
        return tuple(dict.fromkeys(field.name for field in self._fields.values()))

    @property
    def definitions(self) -> tuple[FieldDefinition, ...]:
        """Return canonical field definitions without alias duplicates."""
        return tuple(dict.fromkeys(self._fields.values()))


DEFAULT_FIELD_REGISTRY = FieldRegistry(
    (
        FieldDefinition("title", FieldType.TEXT),
        FieldDefinition("description", FieldType.TEXT),
        FieldDefinition("tag", FieldType.TEXT),
        FieldDefinition("restriction", FieldType.TEXT),
        FieldDefinition("type", FieldType.TEXT),
        FieldDefinition("pattern", FieldType.TEXT),
        FieldDefinition("creator", FieldType.TEXT),
        FieldDefinition("version", FieldType.TEXT),
        FieldDefinition("status", FieldType.TEXT),
        FieldDefinition("kind", FieldType.TEXT),
        FieldDefinition("record_class", FieldType.TEXT),
        FieldDefinition("version_scope", FieldType.TEXT),
        FieldDefinition("record_state", FieldType.TEXT),
        FieldDefinition("volume", FieldType.NUMBER, supports_range=True),
        FieldDefinition("width", FieldType.NUMBER, supports_range=True, supports_sort=True),
        FieldDefinition("height", FieldType.NUMBER, supports_range=True, supports_sort=True),
        FieldDefinition("depth", FieldType.NUMBER, supports_range=True, supports_sort=True),
        FieldDefinition("orientation", FieldType.TEXT),
        FieldDefinition("extender_length", FieldType.NUMBER, supports_range=True),
        FieldDefinition("completion_at", FieldType.TIMESTAMP, supports_range=True, aliases=("completion_date",)),
        FieldDefinition("created_at", FieldType.TIMESTAMP, supports_range=True, supports_sort=True),
        FieldDefinition("updated_at", FieldType.TIMESTAMP, supports_range=True, supports_sort=True),
        FieldDefinition("opening_time", FieldType.NUMBER, supports_range=True),
        FieldDefinition("visible_opening_time", FieldType.NUMBER, supports_range=True),
        FieldDefinition("closing_time", FieldType.NUMBER, supports_range=True),
        FieldDefinition("visible_closing_time", FieldType.NUMBER, supports_range=True),
        FieldDefinition("opening_reset_time", FieldType.NUMBER, supports_range=True),
        FieldDefinition("closing_reset_time", FieldType.NUMBER, supports_range=True),
        FieldDefinition("extension_time", FieldType.NUMBER, supports_range=True),
        FieldDefinition("retraction_time", FieldType.NUMBER, supports_range=True),
        FieldDefinition("extension_reset_time", FieldType.NUMBER, supports_range=True),
        FieldDefinition("retraction_reset_time", FieldType.NUMBER, supports_range=True),
    )
)


def _coerce_decimal(raw: str, field: FieldDefinition) -> Decimal:
    normalized = raw.strip().casefold()
    if not field.unit_scales:
        value = Decimal(normalized)
    else:
        matches = sorted(field.unit_scales, key=lambda item: len(item[0]), reverse=True)
        for suffix, scale in matches:
            if normalized.endswith(suffix.casefold()):
                number = normalized[: -len(suffix)].strip()
                value = Decimal(number) * scale
                break
        else:
            value = Decimal(normalized)
    if not value.is_finite():
        msg = _("Search numbers must be finite")
        raise ValidationError(msg)
    if field.numeric_step is not None and value % field.numeric_step != 0:
        msg = _("{field_name} must align to increments of {step}")
        raise ValidationError(msg, message_params={"field_name": field.name, "step": field.numeric_step})
    return value
