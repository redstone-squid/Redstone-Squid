"""Submission form definitions and validation, drawn however the client chooses."""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from squid.core.errors import JSONValue, ValidationError
from squid.core.i18n import tr

_STABLE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class SubmissionOrigin(StrEnum):
    """A transport that can own or finalize a submission draft."""

    DISCORD = "discord"
    WEB = "web"
    CLI = "cli"
    PAPER = "paper"
    FABRIC = "fabric"


class ControlKind(StrEnum):
    """The small set of controls every client knows how to draw."""

    TEXT = "text"
    NUMBER = "number"
    CHOICE = "choice"
    MULTI_CHOICE = "multi_choice"
    DURATION = "duration"
    BOOLEAN = "boolean"


class ValueKind(StrEnum):
    """Canonical JSON value expected from a field."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"
    GAME_TICKS = "game_ticks"


class VisibilityOperator(StrEnum):
    """Operators supported by the deliberately narrow visibility language."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"


@dataclass(frozen=True, slots=True)
class ChoiceOption:
    """One stable option value and its already-localized label."""

    value: str
    label: str

    def __post_init__(self) -> None:
        if not self.value.strip() or self.value != self.value.strip() or len(self.value) > 120:
            msg = tr(t"choice values must be 1-120 characters without surrounding whitespace")
            raise ValidationError(msg)
        if not self.label.strip():
            msg = tr(t"choice labels cannot be blank")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class VisibilityRule:
    """A condition controlling whether a field participates in validation."""

    field_id: str
    operator: VisibilityOperator
    value: JSONValue

    def __post_init__(self) -> None:
        _require_stable_id(self.field_id, "visibility field ID")

    def matches(self, answers: Mapping[str, JSONValue]) -> bool:
        """Return whether the supplied answers make the dependent field visible."""
        actual = answers.get(self.field_id)
        match self.operator:
            case VisibilityOperator.EQUALS:
                return actual == self.value
            case VisibilityOperator.NOT_EQUALS:
                return actual != self.value
            case VisibilityOperator.IN:
                return (
                    isinstance(self.value, Sequence)
                    and not isinstance(self.value, str | bytes)
                    and actual in self.value
                )
        msg = f"unhandled visibility operator: {self.operator}"
        raise AssertionError(msg)


@dataclass(frozen=True, slots=True)
class FieldConstraints:
    """Validation limits understood identically by every renderer and the server."""

    minimum: int | float | None = None
    maximum: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    must_equal: JSONValue = None

    def __post_init__(self) -> None:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            msg = tr(t"minimum cannot exceed maximum")
            raise ValidationError(msg)
        for name in ("min_length", "max_length", "min_items", "max_items"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValidationError(tr(t"{name} cannot be negative"))
        if self.min_length is not None and self.max_length is not None and self.min_length > self.max_length:
            msg = tr(t"min_length cannot exceed max_length")
            raise ValidationError(msg)
        if self.min_items is not None and self.max_items is not None and self.min_items > self.max_items:
            msg = tr(t"min_items cannot exceed max_items")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class FormField:
    """One stable form field rendered by every supported submission surface."""

    id: str
    label: str
    control: ControlKind
    value_kind: ValueKind
    required: bool = False
    help_text: str | None = None
    constraints: FieldConstraints = field(default_factory=FieldConstraints)
    options: tuple[ChoiceOption, ...] = ()
    option_source: str | None = None
    visible_when: VisibilityRule | None = None
    default: JSONValue = None
    repeatable: bool = False
    required_capability: str | None = None
    origins: frozenset[SubmissionOrigin] = field(default_factory=lambda: frozenset(SubmissionOrigin))

    def __post_init__(self) -> None:
        _require_stable_id(self.id, "field ID")
        field_id = self.id
        if not self.label.strip():
            raise ValidationError(tr(t"field {field_id} has a blank label"))
        if self.option_source is not None:
            _require_stable_id(self.option_source.replace(":", "_"), "option source")
        if self.options and self.option_source is not None:
            raise ValidationError(tr(t"field {field_id} cannot have inline and dynamic options"))
        if self.control in {ControlKind.CHOICE, ControlKind.MULTI_CHOICE} and not (self.options or self.option_source):
            raise ValidationError(tr(t"choice field {field_id} requires options"))
        if self.repeatable and self.value_kind is not ValueKind.STRING_LIST:
            raise ValidationError(tr(t"repeatable field {field_id} must use string_list values"))
        if not self.origins:
            raise ValidationError(tr(t"field {field_id} must apply to at least one origin"))

    def is_visible(self, answers: Mapping[str, JSONValue], origin: SubmissionOrigin) -> bool:
        """Return whether this field participates for the supplied draft context."""
        return origin in self.origins and (self.visible_when is None or self.visible_when.matches(answers))


@dataclass(frozen=True, slots=True)
class FormSection:
    """Ordered fields presented together when a renderer has enough space."""

    id: str
    title: str
    fields: tuple[FormField, ...]

    def __post_init__(self) -> None:
        _require_stable_id(self.id, "section ID")
        if not self.title.strip():
            section_id = self.id
            raise ValidationError(tr(t"section {section_id} has a blank title"))
        _require_unique((item.id for item in self.fields), f"section {self.id} field IDs")


@dataclass(frozen=True, slots=True)
class CategoryForm:
    """Fields specific to one stable build category."""

    code: str
    label: str
    sections: tuple[FormSection, ...]

    def __post_init__(self) -> None:
        _require_stable_id(self.code, "category code")
        if not self.label.strip():
            category = self.code
            raise ValidationError(tr(t"category {category} has a blank label"))
        _require_unique((section.id for section in self.sections), f"category {self.code} section IDs")
        _require_unique(
            (item.id for section in self.sections for item in section.fields), f"category {self.code} field IDs"
        )

    @property
    def fields(self) -> tuple[FormField, ...]:
        """Return every category field in renderer order."""
        return tuple(item for section in self.sections for item in section.fields)


@dataclass(frozen=True, slots=True)
class FormManifest:
    """One immutable, protocol-bounded submission form revision."""

    schema_id: str
    revision: int
    minimum_protocol: int
    maximum_protocol: int
    common_sections: tuple[FormSection, ...]
    categories: tuple[CategoryForm, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", self.schema_id):
            msg = tr(t"schema_id must be a stable lowercase identifier")
            raise ValidationError(msg)
        if self.revision < 1 or self.minimum_protocol < 1 or self.maximum_protocol < self.minimum_protocol:
            msg = tr(t"form revision and protocol bounds must be positive and ordered")
            raise ValidationError(msg)
        _require_unique((section.id for section in self.common_sections), "common section IDs")
        _require_unique((category.code for category in self.categories), "category codes")
        common_ids = tuple(item.id for section in self.common_sections for item in section.fields)
        _require_unique(common_ids, "common field IDs")
        for category in self.categories:
            overlap = set(common_ids).intersection(item.id for item in category.fields)
            if overlap:
                category_code = category.code
                fields = sorted(overlap)
                raise ValidationError(tr(t"category {category_code} duplicates common fields: {fields}"))

    def category(self, code: str) -> CategoryForm:
        """Resolve a category by stable code."""
        for category in self.categories:
            if category.code == code:
                return category
        category = code
        raise ValidationError(
            tr(t"unknown submission category: {category}"),
            resource="submission_category",
            public_context={"category": code},
        )

    def fields_for(self, category: str) -> tuple[FormField, ...]:
        """Return common and category fields in renderer order."""
        return (
            tuple(item for section in self.common_sections for item in section.fields) + self.category(category).fields
        )

    def unsupported_required_capabilities(
        self,
        category: str,
        capabilities: frozenset[str],
        origin: SubmissionOrigin,
    ) -> tuple[str, ...]:
        """Return required renderer capabilities unavailable to a client."""
        missing = {
            item.required_capability
            for item in self.fields_for(category)
            if item.required and origin in item.origins and item.required_capability not in {None, *capabilities}
        }
        return tuple(sorted(capability for capability in missing if capability is not None))

    def validate_answers(
        self,
        category: str,
        answers: Mapping[str, JSONValue],
        *,
        origin: SubmissionOrigin,
        require_complete: bool = True,
    ) -> dict[str, str]:
        """Validate submitted values and return stable field-to-error mappings."""
        answers = self.apply_defaults(category, answers, origin=origin)
        fields = self.fields_for(category)
        definitions = {item.id: item for item in fields}
        errors = {field_id: "unknown_field" for field_id in answers.keys() - definitions.keys()}
        for item in fields:
            if not item.is_visible(answers, origin):
                continue
            value = answers.get(item.id)
            if value is None:
                if item.required and require_complete:
                    errors[item.id] = "required"
                continue
            error = _validate_value(item, value)
            if error is not None:
                errors[item.id] = error
        return errors

    def apply_defaults(
        self,
        category: str,
        answers: Mapping[str, JSONValue],
        *,
        origin: SubmissionOrigin,
    ) -> dict[str, JSONValue]:
        """Return a copy with visible server-authored defaults filled in."""
        normalized = dict(answers)
        for item in self.fields_for(category):
            if item.id not in normalized and item.default is not None and item.is_visible(normalized, origin):
                normalized[item.id] = item.default
        return normalized


def _validate_value(field: FormField, value: JSONValue) -> str | None:
    if not _matches_kind(value, field.value_kind):
        return "wrong_type"
    constraints = field.constraints
    if constraints.must_equal is not None and value != constraints.must_equal:
        return "required_value"
    if field.value_kind in {ValueKind.INTEGER, ValueKind.NUMBER, ValueKind.GAME_TICKS}:
        assert isinstance(value, int | float) and not isinstance(value, bool)
        if constraints.minimum is not None and value < constraints.minimum:
            return "below_minimum"
        if constraints.maximum is not None and value > constraints.maximum:
            return "above_maximum"
    if field.value_kind is ValueKind.STRING:
        assert isinstance(value, str)
        if constraints.min_length is not None and len(value) < constraints.min_length:
            return "too_short"
        if constraints.max_length is not None and len(value) > constraints.max_length:
            return "too_long"
    if field.value_kind is ValueKind.STRING_LIST:
        assert isinstance(value, Sequence) and not isinstance(value, str | bytes)
        if constraints.min_items is not None and len(value) < constraints.min_items:
            return "too_few_items"
        if constraints.max_items is not None and len(value) > constraints.max_items:
            return "too_many_items"
        if constraints.min_length is not None and any(
            isinstance(item, str) and len(item) < constraints.min_length for item in value
        ):
            return "too_short"
        if constraints.max_length is not None and any(
            isinstance(item, str) and len(item) > constraints.max_length for item in value
        ):
            return "too_long"
    if field.options:
        allowed = {option.value for option in field.options}
        supplied = value if isinstance(value, Sequence) and not isinstance(value, str | bytes) else (value,)
        if any(item not in allowed for item in supplied):
            return "unknown_option"
    return None


def _matches_kind(value: JSONValue, kind: ValueKind) -> bool:
    match kind:
        case ValueKind.STRING:
            return isinstance(value, str)
        case ValueKind.INTEGER | ValueKind.GAME_TICKS:
            return isinstance(value, int) and not isinstance(value, bool)
        case ValueKind.NUMBER:
            return isinstance(value, int | float) and not isinstance(value, bool)
        case ValueKind.BOOLEAN:
            return isinstance(value, bool)
        case ValueKind.STRING_LIST:
            return (
                isinstance(value, Sequence)
                and not isinstance(value, str | bytes)
                and all(isinstance(item, str) for item in value)
            )
    msg = f"unhandled value kind: {kind}"
    raise AssertionError(msg)


def _require_stable_id(value: str, label: str) -> None:
    if _STABLE_ID.fullmatch(value) is None:
        raise ValidationError(
            tr(t"{label} must start with a letter and contain only lowercase letters, digits, or underscores")
        )


def _require_unique(values: Iterable[str], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValidationError(tr(t"{label} must be unique"))
