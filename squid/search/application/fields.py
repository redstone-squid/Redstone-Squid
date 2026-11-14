"""Allowlisted search field definitions and value coercion."""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches

from squid.core.errors import InvalidStateError, ValidationError
from squid.core.i18n import tr
from squid.records.domain.models import BaseTitle, VersionScope
from squid.search.domain.fields import FieldDefinition, FieldType
from squid.search.domain.query import ScalarValue
from squid_i18n import md_tr


class FieldRegistry:
    """Define the field names, aliases, and value types accepted in search queries.

    Built-in fields cover properties such as width and status. Approved data tags
    can add fields with a public query name and a stable storage key such as tag:42.
    The parser uses this registry to resolve names and convert values; the backend
    uses the resulting field definitions for filtering and explicit sorting.
    """

    def __init__(self, fields: Iterable[FieldDefinition]) -> None:
        self._fields: dict[str, FieldDefinition] = {}
        for field in fields:
            for name in (field.name, *field.aliases):
                key = name.casefold()
                if key in self._fields:
                    raise InvalidStateError(
                        md_tr(
                            t"search-duplicate-search-field = duplicate search field or alias: {name}",
                        )
                    )
                self._fields[key] = field

    def resolve(self, name: str) -> FieldDefinition | None:
        """Resolve a field name or alias without regard to case; return None if unknown."""
        return self._fields.get(name.casefold())

    def suggestions(self, name: str, *, limit: int = 3) -> tuple[str, ...]:
        """Suggest canonical public names for a misspelled field."""
        matches = get_close_matches(name.casefold(), self._fields, n=limit, cutoff=0.55)
        return tuple(dict.fromkeys(self._fields[match].name for match in matches))

    @staticmethod
    def coerce(field: FieldDefinition, raw: str) -> ScalarValue:
        """Convert a literal to the field's value type.

        Numbers accept the field's unit suffixes and must be finite and aligned
        to its numeric step. Timestamps return normalized ISO strings. Booleans
        accept true/false, yes/no, and 1/0 without regard to case.

        Raises:
            ValidationError: If the value does not meet the field's requirements.
        """
        if field.value_type is FieldType.TEXT:
            return raw
        if field.value_type is FieldType.NUMBER:
            try:
                number = _coerce_decimal(raw, field)
            except (InvalidOperation, ValueError) as error:
                field_name = field.name
                raise ValidationError(md_tr(t"search-expects-number = {field_name} expects a number")) from error
            return number
        if field.value_type is FieldType.TIMESTAMP:
            try:
                return datetime.fromisoformat(raw).isoformat()
            except ValueError as error:
                field_name = field.name
                raise ValidationError(
                    md_tr(
                        t"search-expects-iso-date = {field_name} expects an ISO-8601 date or timestamp",
                    )
                ) from error
        lowered = raw.casefold()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
        field_name = field.name
        raise ValidationError(md_tr(t"search-expects-boolean = {field_name} expects a boolean"))

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
        FieldDefinition("title", FieldType.TEXT, ranked=True),
        FieldDefinition("description", FieldType.TEXT, ranked=True),
        FieldDefinition("tag", FieldType.TEXT, ranked=True),
        FieldDefinition("restriction", FieldType.TEXT, ranked=True),
        FieldDefinition("type", FieldType.TEXT, ranked=True),
        FieldDefinition("pattern", FieldType.TEXT, ranked=True),
        FieldDefinition("creator", FieldType.TEXT, ranked=True),
        FieldDefinition("version", FieldType.TEXT),
        FieldDefinition("status", FieldType.TEXT),
        FieldDefinition("kind", FieldType.TEXT),
        FieldDefinition("base_title", FieldType.TEXT, suggested_values=tuple(title.value for title in BaseTitle)),
        FieldDefinition("version_scope", FieldType.TEXT, suggested_values=tuple(scope.value for scope in VersionScope)),
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
        msg = tr(t"search-search-numbers-finite = Search numbers must be finite")
        raise ValidationError(msg)
    if field.numeric_step is not None and value % field.numeric_step != 0:
        field_name = field.name
        step = str(field.numeric_step)
        raise ValidationError(md_tr(t"search-align-increments = {field_name} must align to increments of {step}"))
    return value
