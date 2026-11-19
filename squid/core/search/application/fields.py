"""Allowlisted search field definitions and value coercion."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
from enum import StrEnum

from squid.core.errors import InvalidStateError, ValidationError
from squid.core.i18n import tr
from squid.core.records.domain.models import BaseTitle, BuildKind, ResolutionStatus, VersionScope
from squid.core.search.domain.fields import FieldDefinition, FieldType
from squid.core.search.domain.query import ScalarValue
from squid.core.search.domain.scope import CATALOGUE_SCOPES, SearchScope
from squid_i18n import md_tr


class BuildSearchStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DENIED = "denied"


class SearchBuildKind(StrEnum):
    DOOR = BuildKind.DOOR.value
    ENTRANCE = BuildKind.ENTRANCE.value
    EXTENDER = BuildKind.EXTENDER.value
    UTILITY = BuildKind.UTILITY.value
    OTHER = "other"


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

    def resolve(self, name: str, *, scope: SearchScope = SearchScope.ALL) -> FieldDefinition | None:
        """Resolve a field name or alias without regard to case; return None if unknown."""
        field = self._fields.get(name.casefold())
        if field is None or not field.applies_to(scope):
            return None
        for enum_scope, value_enum in field.scoped_enums:
            if enum_scope is scope:
                return replace(
                    field,
                    scopes=field.scopes
                    if all(enum is value_enum for _, enum in field.scoped_enums)
                    else frozenset({scope}),
                    value_enum=value_enum,
                    suggested_values=tuple(member.value for member in value_enum),
                    scoped_enums=(),
                )
        return field

    def for_scope(self, scope: SearchScope) -> FieldRegistry:
        """Return the field names and value definitions accepted in this search."""
        if scope not in CATALOGUE_SCOPES:
            return self
        return FieldRegistry(
            resolved for field in self.definitions if (resolved := self.resolve(field.name, scope=scope)) is not None
        )

    def suggestions(self, name: str, *, limit: int = 3) -> tuple[str, ...]:
        """Suggest canonical public names for a misspelled field."""
        matches = get_close_matches(name.casefold(), self._fields, n=limit, cutoff=0.55)
        return tuple(dict.fromkeys(self._fields[match].name for match in matches))

    @staticmethod
    def coerce(field: FieldDefinition, raw: str, *, scope: SearchScope = SearchScope.ALL) -> ScalarValue:
        """Convert a literal to the field's value type.

        Numbers accept the field's unit suffixes and must be finite and aligned
        to its numeric step. Timestamps return normalized ISO strings. Booleans
        accept true/false, yes/no, and 1/0 without regard to case.

        Raises:
            ValidationError: If the value does not meet the field's requirements.
        """
        if field.value_enum is not None:
            value = raw.casefold()
            if value not in field.value_enum or not field.applies_to(scope, value):
                choices = ", ".join(member.value for member in field.value_enum if field.applies_to(scope, member))
                field_name = field.name
                raise ValidationError(md_tr(t"search-field-expects-choice = {field_name} expects one of {choices}."))
            return field.value_enum(value)
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
        FieldDefinition("title", FieldType.TEXT, ranked=True, scopes=CATALOGUE_SCOPES),
        FieldDefinition("description", FieldType.TEXT, ranked=True),
        FieldDefinition("tag", FieldType.TEXT, ranked=True),
        FieldDefinition("restriction", FieldType.TEXT, ranked=True),
        FieldDefinition("type", FieldType.TEXT, ranked=True),
        FieldDefinition("pattern", FieldType.TEXT, ranked=True),
        FieldDefinition("creator", FieldType.TEXT, ranked=True),
        FieldDefinition("version", FieldType.TEXT),
        FieldDefinition(
            "status",
            FieldType.TEXT,
            scopes=CATALOGUE_SCOPES,
            scoped_enums=((SearchScope.BUILDS, BuildSearchStatus), (SearchScope.RECORDS, ResolutionStatus)),
        ),
        FieldDefinition(
            "kind",
            FieldType.TEXT,
            scopes=CATALOGUE_SCOPES,
            scoped_enums=((SearchScope.BUILDS, SearchBuildKind), (SearchScope.RECORDS, SearchBuildKind)),
            value_scopes=((SearchBuildKind.OTHER, SearchScope.BUILDS),),
        ),
        FieldDefinition(
            "base_title",
            FieldType.TEXT,
            suggested_values=tuple(title.value for title in BaseTitle),
            scopes=frozenset({SearchScope.RECORDS}),
            value_enum=BaseTitle,
        ),
        FieldDefinition(
            "version_scope",
            FieldType.TEXT,
            suggested_values=tuple(scope.value for scope in VersionScope),
            scopes=frozenset({SearchScope.RECORDS}),
            value_enum=VersionScope,
        ),
        FieldDefinition("record_state", FieldType.TEXT, scopes=frozenset({SearchScope.RECORDS})),
        FieldDefinition("volume", FieldType.NUMBER, supports_range=True),
        FieldDefinition("width", FieldType.NUMBER, supports_range=True, supports_sort=True),
        FieldDefinition("height", FieldType.NUMBER, supports_range=True, supports_sort=True),
        FieldDefinition("depth", FieldType.NUMBER, supports_range=True, supports_sort=True),
        FieldDefinition("door_width", FieldType.NUMBER, supports_range=True, supports_sort=True),
        FieldDefinition("door_height", FieldType.NUMBER, supports_range=True, supports_sort=True),
        FieldDefinition("door_depth", FieldType.NUMBER, supports_range=True, supports_sort=True),
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
