"""Allowlisted search field definitions and value coercion."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
from enum import StrEnum

from squid.core.errors import InvalidStateError, ValidationError
from squid.core.i18n import tr
from squid.core.records.domain.models import BuildKind, RecordCriterion, VersionScope
from squid.core.search.domain.fields import FieldDefinition, FieldGroup, FieldType, FreeTextWeight, TextMatch
from squid.core.search.domain.query import CREATOR_ID_STORAGE, ScalarValue
from squid.core.search.domain.scope import CATALOGUE_SCOPES, SearchScope
from squid_i18n import Text, md_tr, plain


class BuildSearchStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RecordState(StrEnum):
    """Whether a record has competitors. Retired records only appear when a query asks for them."""

    CURRENT = "current"
    RETIRED = "retired"


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
        self._all = tuple(fields)
        self._fields: dict[str, FieldDefinition] = {}
        self._stored: dict[str, FieldDefinition] = {}
        for field in self._all:
            self._stored.setdefault(field.storage, field)
            if not field.queryable:
                continue
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
        if scope in field.unranked_scopes:
            field = replace(field, ranked=False)
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

    def display_name(self, name: str) -> Text:
        """Label a field by name, alias or storage key, falling back to the name itself."""
        field = self._fields.get(name.casefold()) or self._stored.get(name)
        return field.display_name if field is not None else plain(name.replace("_", " ").capitalize())

    def stored(self, storage: str) -> FieldDefinition | None:
        """Find the field whose values are indexed under `storage`, queryable or not."""
        return self._stored.get(storage)

    def for_scope(self, scope: SearchScope) -> FieldRegistry:
        """Return the field names and value definitions accepted in this search."""
        if scope not in CATALOGUE_SCOPES:
            return self
        return FieldRegistry(
            (
                *(
                    resolved
                    for field in self.definitions
                    if (resolved := self.resolve(field.name, scope=scope)) is not None
                ),
                *(field for field in self._all if not field.queryable),
            )
        )

    def extended(self, fields: Iterable[FieldDefinition]) -> FieldRegistry:
        """Return a registry with these fields added, keeping the indexed-only ones."""
        return FieldRegistry((*self._all, *fields))

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
            if field.value_enum is BuildSearchStatus:
                value = {"confirmed": "approved", "denied": "rejected"}.get(value, value)
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


_GAME_TICK_UNITS = (("gt", Decimal(1)), ("s", Decimal(20)))


def _status_label(value: str) -> Text:
    return tr(t"""
        search-choice-status = {{ {value} ->
            [pending] Pending
            [approved] Approved
           *[rejected] Rejected
        }}
    """)


def _kind_label(value: str) -> Text:
    return tr(t"""
        search-choice-kind = {{ {value} ->
            [door] Door
            [entrance] Entrance
            [extender] Extender
            [utility] Utility
           *[other] Other
        }}
    """)


def _criterion_label(value: str) -> Text:
    return tr(t"""
        search-choice-criterion = {{ {value} ->
            [first] First
            [fastest] Fastest
            [smallest] Smallest
            [fastest_smallest] Fastest smallest
           *[smallest_fastest] Smallest fastest
        }}
    """)


def _version_scope_label(value: str) -> Text:
    return tr(t"""
        search-choice-version-scope = {{ {value} ->
            [all_time] All time
           *[up_to_date] Up to date
        }}
    """)


def _record_state_label(value: str) -> Text:
    return tr(t"""
        search-choice-record-state = {{ {value} ->
            [current] Current
           *[retired] Retired
        }}
    """)


def _timing(name: str, label: Text, description: Text, *, sortable: bool = False) -> FieldDefinition:
    return FieldDefinition(
        name,
        FieldType.NUMBER,
        supports_range=True,
        scopes=CATALOGUE_SCOPES,
        holder_scoped=True,
        supports_sort=sortable,
        unit_scales=_GAME_TICK_UNITS,
        unit="gt",
        label=label,
        description=description,
        group=FieldGroup.TIMING,
        examples=(f"{name}<20", f"{name}:[1s TO 2s]"),
    )


def _dimension(name: str, label: Text, description: Text, *, scopes: frozenset[SearchScope]) -> FieldDefinition:
    return FieldDefinition(
        name,
        FieldType.NUMBER,
        supports_range=True,
        supports_sort=True,
        scopes=scopes,
        label=label,
        description=description,
        group=FieldGroup.SIZE,
        examples=(f"{name}:3", f"{name}<=5"),
    )


_WORDS = TextMatch.WORDS
_RECORDS = frozenset({SearchScope.RECORDS})
_BUILDS = frozenset({SearchScope.BUILDS})

DEFAULT_FIELD_REGISTRY = FieldRegistry(
    (
        FieldDefinition(
            "title",
            FieldType.TEXT,
            ranked=True,
            supports_sort=True,
            scopes=CATALOGUE_SCOPES,
            text_match=_WORDS,
            free_text=FreeTextWeight.A,
            label=tr(t"search-field-title = Title"),
            description=tr(t"search-field-title-description = Words in the build or record title."),
            examples=("title:piston", 'title:"flush door"'),
        ),
        FieldDefinition(
            "name",
            FieldType.TEXT,
            ranked=True,
            text_match=_WORDS,
            free_text=FreeTextWeight.A,
            label=tr(t"search-field-name = Name"),
            description=tr(t"search-field-name-description = Words in the build's own name."),
            examples=("name:cobble",),
        ),
        FieldDefinition(
            "subtitle",
            FieldType.TEXT,
            ranked=True,
            scopes=CATALOGUE_SCOPES,
            text_match=_WORDS,
            free_text=FreeTextWeight.B,
            label=tr(t"search-field-subtitle = Subtitle"),
            description=tr(t"search-field-subtitle-description = Words in the line shown under the title."),
            examples=("subtitle:seamless",),
        ),
        FieldDefinition(
            "description",
            FieldType.TEXT,
            ranked=True,
            text_match=_WORDS,
            free_text=FreeTextWeight.D,
            label=tr(t"search-field-description = Description"),
            description=tr(t"search-field-description-description = Words in the build's description."),
            examples=("description:tileable",),
        ),
        FieldDefinition(
            "tag",
            FieldType.TEXT,
            ranked=True,
            free_text=FreeTextWeight.B,
            label=tr(t"search-field-tag = Tag"),
            description=tr(t"search-field-tag-description = A showcase tag on the build."),
            group=FieldGroup.CLASSIFICATION,
            examples=("tag:tileable",),
        ),
        FieldDefinition(
            "restriction",
            FieldType.TEXT,
            ranked=True,
            scopes=CATALOGUE_SCOPES,
            unranked_scopes=_RECORDS,
            free_text=FreeTextWeight.B,
            label=tr(t"search-field-restriction = Restriction"),
            description=tr(
                t"search-field-restriction-description = A component or design restriction the build follows."
            ),
            group=FieldGroup.CLASSIFICATION,
            examples=("restriction:observerless", "NOT restriction:slimeless"),
        ),
        FieldDefinition(
            "type",
            FieldType.TEXT,
            ranked=True,
            scopes=CATALOGUE_SCOPES,
            unranked_scopes=_RECORDS,
            free_text=FreeTextWeight.B,
            label=tr(t"search-field-type = Type"),
            description=tr(t"search-field-type-description = The build's type, such as flush or seamless."),
            group=FieldGroup.CLASSIFICATION,
            examples=("type:flush",),
        ),
        FieldDefinition(
            "pattern",
            FieldType.TEXT,
            ranked=True,
            free_text=FreeTextWeight.B,
            label=tr(t"search-field-pattern = Pattern"),
            description=tr(t"search-field-pattern-description = The door's opening pattern."),
            group=FieldGroup.CLASSIFICATION,
            examples=("pattern:regular",),
        ),
        FieldDefinition(
            "creator",
            FieldType.TEXT,
            ranked=True,
            scopes=CATALOGUE_SCOPES,
            free_text=FreeTextWeight.C,
            label=tr(t"search-field-creator = Creator"),
            description=tr(t"search-field-creator-description = A credited creator's name or public creator ID."),
            group=FieldGroup.CREDIT,
            examples=("creator:Steve",),
        ),
        FieldDefinition(CREATOR_ID_STORAGE, FieldType.TEXT, queryable=False),
        FieldDefinition(
            "version",
            FieldType.TEXT,
            free_text=FreeTextWeight.D,
            label=tr(t"search-field-version = Version"),
            description=tr(t"search-field-version-description = A Minecraft version the build works in."),
            group=FieldGroup.CLASSIFICATION,
            examples=("version:1.21",),
        ),
        FieldDefinition(
            "status",
            FieldType.TEXT,
            scopes=_BUILDS,
            scoped_enums=((SearchScope.BUILDS, BuildSearchStatus),),
            label=tr(t"search-field-status = Status"),
            description=tr(t"search-field-status-description = The build's review status."),
            group=FieldGroup.CLASSIFICATION,
            examples=("status:approved",),
            choice_label=_status_label,
        ),
        FieldDefinition(
            "kind",
            FieldType.TEXT,
            scopes=CATALOGUE_SCOPES,
            scoped_enums=((SearchScope.BUILDS, SearchBuildKind), (SearchScope.RECORDS, SearchBuildKind)),
            value_scopes=((SearchBuildKind.OTHER, SearchScope.BUILDS),),
            label=tr(t"search-field-kind = Kind"),
            description=tr(
                t"search-field-kind-description = What the build is: a door, entrance, extender or utility."
            ),
            group=FieldGroup.CLASSIFICATION,
            examples=("kind:door",),
            choice_label=_kind_label,
        ),
        FieldDefinition(
            "criterion",
            FieldType.TEXT,
            aliases=("base_title",),
            storage_name="base_title",
            suggested_values=tuple(title.value for title in RecordCriterion),
            scopes=_RECORDS,
            value_enum=RecordCriterion,
            free_text=FreeTextWeight.B,
            label=tr(t"search-field-base-title = Record criterion"),
            description=tr(
                t"search-field-criterion-description = What the record measures, such as fastest or smallest."
            ),
            group=FieldGroup.RECORD,
            examples=("criterion:fastest", "fastest 2x2 door"),
            choice_label=_criterion_label,
        ),
        FieldDefinition(
            "version_scope",
            FieldType.TEXT,
            suggested_values=tuple(scope.value for scope in VersionScope),
            scopes=_RECORDS,
            value_enum=VersionScope,
            label=tr(t"search-field-version-scope = Version scope"),
            description=tr(
                t"search-field-version-scope-description = Whether the record compares builds from every version or only those that work today."
            ),
            group=FieldGroup.RECORD,
            examples=("version_scope:up_to_date",),
            choice_label=_version_scope_label,
        ),
        FieldDefinition(
            "record_state",
            FieldType.TEXT,
            scopes=_RECORDS,
            suggested_values=tuple(state.value for state in RecordState),
            value_enum=RecordState,
            label=tr(t"search-field-record-state = Record state"),
            description=tr(
                t"search-field-record-state-description = Whether a record is current or retired. Retired records only appear when asked for."
            ),
            group=FieldGroup.RECORD,
            examples=("record_state:retired",),
            choice_label=_record_state_label,
        ),
        # Holders are matched by build ID; their titles and credits only answer bare text,
        # and rank below the record's own category text.
        FieldDefinition("holder", FieldType.TEXT, queryable=False),
        FieldDefinition("holder_title", FieldType.TEXT, queryable=False, free_text=FreeTextWeight.D),
        FieldDefinition("holder_creator", FieldType.TEXT, queryable=False, free_text=FreeTextWeight.D),
        FieldDefinition(
            "former_holder",
            FieldType.NUMBER,
            scopes=_RECORDS,
            label=tr(t"search-field-former-holder = Former holder"),
            description=tr(t"search-field-former-holder-description = The ID of a build that used to hold the record."),
            group=FieldGroup.RECORD,
            examples=("former_holder:42",),
        ),
        FieldDefinition(
            "tied",
            FieldType.NUMBER,
            scopes=_RECORDS,
            label=tr(t"search-field-tied = Tied build"),
            description=tr(t"search-field-tied-description = The ID of a build that shares the record."),
            group=FieldGroup.RECORD,
            examples=("tied:42",),
        ),
        FieldDefinition("category_tag_id", FieldType.NUMBER, queryable=False),
        FieldDefinition(
            "volume",
            FieldType.NUMBER,
            supports_range=True,
            supports_sort=True,
            scopes=CATALOGUE_SCOPES,
            holder_scoped=True,
            label=tr(t"search-field-volume = Volume"),
            description=tr(t"search-field-volume-description = The number of blocks in the build's bounding box."),
            group=FieldGroup.SIZE,
            examples=("volume<1000",),
        ),
        _dimension(
            "width",
            tr(t"search-field-width = Width"),
            tr(t"search-field-width-description = The build's width in blocks."),
            scopes=_BUILDS,
        ),
        _dimension(
            "height",
            tr(t"search-field-height = Height"),
            tr(t"search-field-height-description = The build's height in blocks."),
            scopes=_BUILDS,
        ),
        _dimension(
            "depth",
            tr(t"search-field-depth = Depth"),
            tr(t"search-field-depth-description = The build's depth in blocks."),
            scopes=_BUILDS,
        ),
        _dimension(
            "door_width",
            tr(t"search-field-door-width = Door width"),
            tr(t"search-field-door-width-description = The width of the doorway in blocks."),
            scopes=CATALOGUE_SCOPES,
        ),
        _dimension(
            "door_height",
            tr(t"search-field-door-height = Door height"),
            tr(t"search-field-door-height-description = The height of the doorway in blocks."),
            scopes=CATALOGUE_SCOPES,
        ),
        _dimension(
            "door_depth",
            tr(t"search-field-door-depth = Door depth"),
            tr(t"search-field-door-depth-description = The depth of the doorway in blocks."),
            scopes=CATALOGUE_SCOPES,
        ),
        FieldDefinition(
            "orientation",
            FieldType.TEXT,
            scopes=CATALOGUE_SCOPES,
            label=tr(t"search-field-orientation = Orientation"),
            description=tr(
                t"search-field-orientation-description = How the door is placed, such as door, trapdoor or skydoor."
            ),
            group=FieldGroup.CLASSIFICATION,
            examples=("orientation:trapdoor",),
        ),
        FieldDefinition(
            "extender_length",
            FieldType.NUMBER,
            supports_range=True,
            scopes=CATALOGUE_SCOPES,
            label=tr(t"search-field-extender-length = Extender length"),
            description=tr(t"search-field-extender-length-description = How many blocks the extender reaches."),
            group=FieldGroup.SIZE,
            examples=("extender_length>=10",),
        ),
        FieldDefinition(
            "completion_at",
            FieldType.TIMESTAMP,
            supports_range=True,
            aliases=("completion_date",),
            label=tr(t"search-field-completion-at = Completed"),
            description=tr(t"search-field-completion-at-description = When the creators finished the build."),
            group=FieldGroup.DATE,
            examples=("completion_at>=2024-01-01",),
        ),
        FieldDefinition(
            "created_at",
            FieldType.TIMESTAMP,
            supports_range=True,
            supports_sort=True,
            label=tr(t"search-field-created-at = Submitted"),
            description=tr(t"search-field-created-at-description = When the build was submitted."),
            group=FieldGroup.DATE,
            examples=("created_at:[2025-01-01 TO 2025-06-30]",),
        ),
        FieldDefinition(
            "updated_at",
            FieldType.TIMESTAMP,
            supports_range=True,
            supports_sort=True,
            label=tr(t"search-field-updated-at = Updated"),
            description=tr(t"search-field-updated-at-description = When the build was last changed."),
            group=FieldGroup.DATE,
            examples=("updated_at>=2025-01-01",),
        ),
        _timing(
            "opening_time",
            tr(t"search-field-opening-time = Opening time"),
            tr(t"search-field-opening-time-description = Ticks from input until the door is open."),
            sortable=True,
        ),
        _timing(
            "visible_opening_time",
            tr(t"search-field-visible-opening-time = Visible opening time"),
            tr(t"search-field-visible-opening-time-description = Ticks from input until the door looks open."),
        ),
        _timing(
            "closing_time",
            tr(t"search-field-closing-time = Closing time"),
            tr(t"search-field-closing-time-description = Ticks from input until the door is closed."),
        ),
        _timing(
            "visible_closing_time",
            tr(t"search-field-visible-closing-time = Visible closing time"),
            tr(t"search-field-visible-closing-time-description = Ticks from input until the door looks closed."),
        ),
        _timing(
            "opening_reset_time",
            tr(t"search-field-opening-reset-time = Opening reset time"),
            tr(
                t"search-field-opening-reset-time-description = Ticks after opening until the door accepts input again."
            ),
        ),
        _timing(
            "closing_reset_time",
            tr(t"search-field-closing-reset-time = Closing reset time"),
            tr(
                t"search-field-closing-reset-time-description = Ticks after closing until the door accepts input again."
            ),
        ),
        _timing(
            "extension_time",
            tr(t"search-field-extension-time = Extension time"),
            tr(t"search-field-extension-time-description = Ticks from input until the extender is extended."),
        ),
        _timing(
            "retraction_time",
            tr(t"search-field-retraction-time = Retraction time"),
            tr(t"search-field-retraction-time-description = Ticks from input until the extender is retracted."),
        ),
        _timing(
            "extension_reset_time",
            tr(t"search-field-extension-reset-time = Extension reset time"),
            tr(
                t"search-field-extension-reset-time-description = Ticks after extending until the extender accepts input again."
            ),
        ),
        _timing(
            "retraction_reset_time",
            tr(t"search-field-retraction-reset-time = Retraction reset time"),
            tr(
                t"search-field-retraction-reset-time-description = Ticks after retracting until the extender accepts input again."
            ),
        ),
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
