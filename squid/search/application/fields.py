"""Allowlisted search field definitions and value coercion."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from difflib import get_close_matches
from enum import StrEnum

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


class FieldRegistry:
    """Resolve field names and coerce values without exposing persistence identifiers."""

    def __init__(self, fields: Iterable[FieldDefinition]) -> None:
        self._fields: dict[str, FieldDefinition] = {}
        for field in fields:
            for name in (field.name, *field.aliases):
                key = name.casefold()
                if key in self._fields:
                    msg = f"duplicate search field or alias: {name}"
                    raise ValueError(msg)
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
                number = float(raw)
            except ValueError as error:
                msg = f"{field.name} expects a number"
                raise ValueError(msg) from error
            return int(number) if number.is_integer() else number
        if field.value_type is FieldType.TIMESTAMP:
            try:
                return datetime.fromisoformat(raw).isoformat()
            except ValueError as error:
                msg = f"{field.name} expects an ISO-8601 date or timestamp"
                raise ValueError(msg) from error
        lowered = raw.casefold()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
        msg = f"{field.name} expects a boolean"
        raise ValueError(msg)

    @property
    def names(self) -> tuple[str, ...]:
        """Return canonical registered names."""
        return tuple(dict.fromkeys(field.name for field in self._fields.values()))


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
        FieldDefinition("width", FieldType.NUMBER, supports_range=True),
        FieldDefinition("height", FieldType.NUMBER, supports_range=True),
        FieldDefinition("depth", FieldType.NUMBER, supports_range=True),
        FieldDefinition("orientation", FieldType.TEXT),
        FieldDefinition("extender_length", FieldType.NUMBER, supports_range=True),
        FieldDefinition("completion_at", FieldType.TIMESTAMP, supports_range=True, aliases=("completion_date",)),
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
