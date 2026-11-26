"""Definitions of the fields that search queries can filter and sort by."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from squid.core.search.domain.scope import CATALOGUE_SCOPES, SearchScope
from squid.core.vocabulary import SEARCH_FIELDS
from squid_i18n import Text, plain


class FieldType(StrEnum):
    """Storage-independent field value types."""

    TEXT = "text"
    NUMBER = "number"
    TIMESTAMP = "timestamp"
    BOOLEAN = "boolean"


class TextMatch(StrEnum):
    """How a clause on a text field compares its value."""

    WORDS = "words"
    """Terms anywhere in a value, a quoted phrase in order, and close spellings of long words."""
    KEYWORD = "keyword"
    """The whole value after normalization, as in `restriction:"No Entities"`."""


class FreeTextWeight(StrEnum):
    """How strongly a bare-text match on a field ranks, from A (strongest) to D."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class FieldGroup(StrEnum):
    """The topic a field is listed under in field references and filter panels."""

    TEXT = "text"
    CLASSIFICATION = "classification"
    CREDIT = "credit"
    SIZE = "size"
    TIMING = "timing"
    DATE = "date"
    RECORD = "record"
    TAG = "tag"


@dataclass(frozen=True, slots=True)
class FieldChoice:
    """One accepted value of an enumerated field and the catalogue tabs that accept it."""

    value: str
    scopes: frozenset[SearchScope]


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    """A queryable field exposed by the search API."""

    name: str
    value_type: FieldType
    supports_range: bool = False
    aliases: tuple[str, ...] = ()
    storage_name: str | None = None
    """Storage key such as `tag:42`. Defaults to the public field name."""
    supports_sort: bool = False
    ranked: bool = False
    """Whether the value of a clause on this field also counts as text for relevance ranking."""
    holder_scoped: bool = False
    """In Records, compare this field against one published holder."""
    unranked_scopes: frozenset[SearchScope] = frozenset()
    unit_scales: tuple[tuple[str, Decimal], ...] = ()
    """Accepted suffixes and multipliers that convert values to the canonical unit."""
    numeric_step: Decimal | None = None
    suggested_values: tuple[str, ...] | None = None
    """Fixed text completions; None reads indexed values, and an empty tuple disables suggestions.

    These are completion hints, not a restriction on accepted query values.
    """
    scopes: frozenset[SearchScope] = frozenset({SearchScope.BUILDS})
    """Catalogue tabs that can filter on this field."""
    value_enum: type[StrEnum] | None = None
    scoped_enums: tuple[tuple[SearchScope, type[StrEnum]], ...] = ()
    value_scopes: tuple[tuple[str, SearchScope], ...] = ()
    text_match: TextMatch = TextMatch.KEYWORD
    free_text: FreeTextWeight | None = None
    """Bare query text also searches this field's values, ranked by this weight; None leaves it out."""
    queryable: bool = True
    """False for values that are indexed for bare text but have no field syntax, such as a holder's title."""
    unit: str | None = None
    """The unit of unsuffixed query values, such as `gt` for game ticks."""
    # Presentation does not change what a field matches, so it stays out of equality.
    label: Text | None = field(default=None, compare=False)
    """The data tag's name. Built-in fields are labelled by `SEARCH_FIELDS` in the vocabulary."""
    group: FieldGroup = field(default=FieldGroup.TEXT, compare=False)
    examples: tuple[str, ...] = field(default=(), compare=False)
    """Complete queries that use the field, such as `opening_time<1s`."""
    vocabulary: str | None = field(default=None, compare=False)
    """The `VOCABULARY` group that labels the field's choices."""

    @property
    def storage(self) -> str:
        """The key the field's values are indexed under."""
        return self.storage_name or self.name

    @property
    def display_name(self) -> Text:
        """The tag or vocabulary label, or the name in sentence case when there is none."""
        if self.label is not None:
            return self.label
        return SEARCH_FIELDS.get(self.name) or plain(self.name.replace("_", " ").capitalize())

    def value_choices(self) -> tuple[FieldChoice, ...] | None:
        """List the values of an enumerated field across scopes; None for free-form fields."""
        enums = (
            *((self.value_enum,) if self.value_enum is not None else ()),
            *(value_enum for _, value_enum in self.scoped_enums),
        )
        if not enums:
            return None
        scopes: dict[str, set[SearchScope]] = {}
        for scope in sorted(self.scopes):
            allowed = self.value_enum if self.value_enum is not None else dict(self.scoped_enums).get(scope)
            for member in allowed or ():
                if self.applies_to(scope, member.value):
                    scopes.setdefault(member.value, set()).add(scope)
        return tuple(FieldChoice(value, frozenset(allowed)) for value, allowed in scopes.items())

    def applies_to(self, scope: SearchScope, value: object = None) -> bool:
        """Check the field and any scope restriction on a particular value."""
        if scope not in CATALOGUE_SCOPES:
            return True
        return scope in self.scopes and all(
            value != restricted or scope is allowed for restricted, allowed in self.value_scopes
        )

    def choices(self, scope: SearchScope) -> tuple[str, ...] | None:
        if self.suggested_values is None:
            return None
        return tuple(value for value in self.suggested_values if self.applies_to(scope, value))

    def accepts(self, value: object, scope: SearchScope) -> bool:
        """Validate a value on a resolved field when constructing a query tree."""
        if self.scoped_enums or not self.applies_to(scope, value):
            return False
        if self.value_enum is not None:
            return isinstance(value, self.value_enum)
        match self.value_type:
            case FieldType.TEXT | FieldType.TIMESTAMP:
                return isinstance(value, str)
            case FieldType.NUMBER:
                return isinstance(value, int | float | Decimal) and not isinstance(value, bool)
            case FieldType.BOOLEAN:
                return isinstance(value, bool)
