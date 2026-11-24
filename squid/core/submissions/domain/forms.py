"""Shared form definitions for clients that edit submission drafts.

A FormManifest combines fields common to all builds with fields for a category,
such as doors. Clients use it to render their own controls; the server uses the
same definitions to validate answers. Drafts retain the manifest's revision so
a later form change does not silently change the meaning of existing answers.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from squid.core.builds.domain.credits import read_credits
from squid.core.builds.domain.showcase import read_showcase_tags
from squid.core.errors import JSONValue, ValidationError
from squid.core.i18n import tr
from squid.core.validation import (
    AboveMaximum,
    AboveMaximumParams,
    BelowMinimum,
    BelowMinimumParams,
    FieldErrorCode,
    RequiredValue,
    RequiredValueParams,
    RuleIssue,
    RuleParams,
    SimpleIssue,
    TooFewItems,
    TooFewItemsParams,
    TooLong,
    TooLongParams,
    TooManyItems,
    TooManyItemsParams,
    TooShort,
    TooShortParams,
    ValidationIssue,
)
from squid_i18n import md_tr

if TYPE_CHECKING:
    from squid.core.submissions.domain.finalization import SubmissionAttentionReason


_STABLE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OPTION_VALUE_TYPES = frozenset({None, "text", "numeric", "boolean"})


class SubmissionOrigin(StrEnum):
    """The client or platform from which a submission draft originated."""

    DISCORD = "discord"
    WEB = "web"
    CLI = "cli"
    PAPER = "paper"
    FABRIC = "fabric"


class FormPurpose(StrEnum):
    """The task a projection of the build form serves.

    One manifest describes every build field. Submitting a new build and editing a saved one use
    most fields alike, but some only make sense for one task: a rights attestation for a new
    submission, media links for a saved build. `FormManifest.for_purpose` returns the view for one
    task.
    """

    SUBMIT = "submit"
    EDIT = "edit"


class ControlKind(StrEnum):
    """Control types a submission client may render."""

    TEXT = "text"
    NUMBER = "number"
    CHOICE = "choice"
    MULTI_CHOICE = "multi_choice"
    DURATION = "duration"
    BOOLEAN = "boolean"
    CREDITS = "credits"
    SHOWCASE_TAGS = "showcase_tags"


class ValueKind(StrEnum):
    """Canonical JSON value expected from a field."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"
    GAME_TICKS = "game_ticks"
    CREDIT_LIST = "credit_list"
    SHOWCASE_TAG_LIST = "showcase_tag_list"
    """Showcase tags as `{"key": ..., "value": ...}` objects; see `read_showcase_tags`."""


class VisibilityOperator(StrEnum):
    """Comparisons supported by conditional field visibility."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"


@dataclass(frozen=True, slots=True)
class ChoiceOption:
    """One stable option value and its already-localized label."""

    value: str
    label: str
    value_type: str | None = None
    """What a showcase tag option takes with it: "text", "numeric", "boolean", or None for nothing."""

    def __post_init__(self) -> None:
        if not self.value.strip() or self.value != self.value.strip() or len(self.value) > 120:
            msg = tr(
                t"submissions-choice-characters-without = choice "
                t"values must be 1-120 characters without surrounding whitespace"
            )
            raise ValidationError(msg)
        if not self.label.strip():
            msg = tr(t"submissions-choice-labels-blank = choice labels cannot be blank")
            raise ValidationError(msg)
        if self.value_type not in _OPTION_VALUE_TYPES:
            msg = tr(t"submissions-choice-value-type = choice value types must be text, numeric, boolean, or absent")
            raise ValidationError(msg)


def match_choice(name: str, choices: Sequence[ChoiceOption]) -> str | None:
    """Return the value of the one choice whose value or label equals `name`, ignoring case.

    Return None when no choice matches, or when several do: guessing between two tags would
    attach the wrong one.
    """
    folded = name.casefold()
    matches = {choice.value for choice in choices if folded in {choice.value.casefold(), choice.label.casefold()}}
    return next(iter(matches)) if len(matches) == 1 else None


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
    """Limits published to clients and checked when the server validates answers."""

    minimum: int | float | None = None
    maximum: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    must_equal: JSONValue = None

    def __post_init__(self) -> None:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            msg = tr(t"submissions-minimum-exceed-maximum = minimum cannot exceed maximum")
            raise ValidationError(msg)
        for name in ("min_length", "max_length", "min_items", "max_items"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValidationError(md_tr(t"submissions-negative = {name} cannot be negative"))
        if self.min_length is not None and self.max_length is not None and self.min_length > self.max_length:
            msg = tr(t"submissions-min-length-exceed = min_length cannot exceed max_length")
            raise ValidationError(msg)
        if self.min_items is not None and self.max_items is not None and self.min_items > self.max_items:
            msg = tr(t"submissions-min-items-exceed = min_items cannot exceed max_items")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class EditRules:
    """How a field differs when it edits a saved build rather than a new submission.

    A saved build can hold values that the submission rules no longer allow, such as a longer
    description or no creators, so editing uses the limits of the stored build instead.
    """

    required: bool | None = None
    """Replaces `FormField.required` when not None."""
    constraints: FieldConstraints | None = None
    """Replaces `FormField.constraints` when not None."""


@dataclass(frozen=True, slots=True)
class FieldRule:
    """An additional check reported on its declaring field.

    The predicate receives that field's value and the values of the fields in
    `depends_on`. It runs when every input is visible and has a value of the
    declared kind. The rule ID identifies the check independently of its reason.
    """

    id: str
    reason: SubmissionAttentionReason
    accepts: Callable[[JSONValue, Mapping[str, JSONValue]], bool]
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_stable_id(self.id, "rule ID")
        _require_unique(self.depends_on, "rule input fields")


@dataclass(frozen=True, slots=True)
class _ValidationIssue:
    public: ValidationIssue
    inputs: frozenset[str]
    rule_id: str | None = None


@dataclass(frozen=True, slots=True)
class FormField:
    """A form field with its value type, presentation, and validation rules."""

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
    purposes: frozenset[FormPurpose] = field(default_factory=lambda: frozenset(FormPurpose))
    """The tasks that show this field; see `FormPurpose`."""
    edit_rules: EditRules | None = None
    """Overrides applied in the edit projection."""
    rules: tuple[FieldRule, ...] = ()

    def __post_init__(self) -> None:
        _require_stable_id(self.id, "field ID")
        field_id = self.id
        _require_unique((rule.id for rule in self.rules), f"rules for {field_id}")
        if not self.label.strip():
            raise ValidationError(
                md_tr(
                    t"submissions-field-blank-label = field {field_id!s} has a blank label",
                )
            )
        if self.option_source is not None:
            _require_stable_id(self.option_source.replace(":", "_"), "option source")
        if self.options and self.option_source is not None:
            raise ValidationError(
                md_tr(
                    t"submissions-field-inline-dynamic = field {field_id!s} cannot have inline and dynamic options",
                )
            )
        if self.control in {ControlKind.CHOICE, ControlKind.MULTI_CHOICE, ControlKind.SHOWCASE_TAGS} and not (
            self.options or self.option_source
        ):
            raise ValidationError(
                md_tr(
                    t"submissions-choice-options-required = choice field {field_id!s} requires options",
                )
            )
        if self.repeatable and self.value_kind is not ValueKind.STRING_LIST:
            raise ValidationError(
                md_tr(
                    t"submissions-repeatable-field-use = repeatable field {field_id!s} must use string_list values",
                )
            )
        if not self.purposes:
            raise ValidationError(
                md_tr(
                    t"submissions-field-purpose = field {field_id!s} must apply to at least one purpose",
                )
            )
        if not self.origins:
            raise ValidationError(
                md_tr(
                    t"submissions-field-apply = field {field_id!s} must apply to at least one origin",
                )
            )

    def is_visible(self, answers: Mapping[str, JSONValue], origin: SubmissionOrigin) -> bool:
        """Return whether this field participates for the supplied draft context."""
        return origin in self.origins and (self.visible_when is None or self.visible_when.matches(answers))

    def for_purpose(self, purpose: FormPurpose) -> FormField:
        """Return this field with the overrides for one form purpose applied."""
        rules = self.edit_rules
        if purpose is not FormPurpose.EDIT or rules is None:
            return self
        return replace(
            self,
            required=self.required if rules.required is None else rules.required,
            constraints=self.constraints if rules.constraints is None else rules.constraints,
        )

    def validate_value(self, value: JSONValue) -> ValidationIssue | None:
        """Return a field issue or None, without checking dynamic options."""
        if not _matches_kind(value, self.value_kind):
            return SimpleIssue(field=self.id, code=FieldErrorCode.WRONG_TYPE)
        constraints = self.constraints
        if constraints.must_equal is not None and value != constraints.must_equal:
            return RequiredValue(
                field=self.id, params=RequiredValueParams.model_validate({"expected": constraints.must_equal})
            )
        if self.value_kind in {ValueKind.INTEGER, ValueKind.NUMBER, ValueKind.GAME_TICKS}:
            assert isinstance(value, int | float) and not isinstance(value, bool)
            if constraints.minimum is not None and value < constraints.minimum:
                return BelowMinimum(field=self.id, params=BelowMinimumParams(minimum=constraints.minimum))
            if constraints.maximum is not None and value > constraints.maximum:
                return AboveMaximum(field=self.id, params=AboveMaximumParams(maximum=constraints.maximum))
        if self.value_kind is ValueKind.STRING:
            assert isinstance(value, str)
            if constraints.min_length is not None and len(value) < constraints.min_length:
                return TooShort(field=self.id, params=TooShortParams(min_length=constraints.min_length))
            if constraints.max_length is not None and len(value) > constraints.max_length:
                return TooLong(field=self.id, params=TooLongParams(max_length=constraints.max_length))
        if self.value_kind in {ValueKind.STRING_LIST, ValueKind.CREDIT_LIST, ValueKind.SHOWCASE_TAG_LIST}:
            assert isinstance(value, Sequence) and not isinstance(value, str | bytes)
            if constraints.min_items is not None and len(value) < constraints.min_items:
                return TooFewItems(field=self.id, params=TooFewItemsParams(min_items=constraints.min_items))
            if constraints.max_items is not None and len(value) > constraints.max_items:
                return TooManyItems(field=self.id, params=TooManyItemsParams(max_items=constraints.max_items))
            if constraints.min_length is not None and any(
                isinstance(item, str) and len(item) < constraints.min_length for item in value
            ):
                return TooShort(field=self.id, params=TooShortParams(min_length=constraints.min_length))
            if constraints.max_length is not None and any(
                isinstance(item, str) and len(item) > constraints.max_length for item in value
            ):
                return TooLong(field=self.id, params=TooLongParams(max_length=constraints.max_length))
        if self.options:
            allowed = {option.value for option in self.options}
            supplied: Sequence[JSONValue]
            if self.value_kind is ValueKind.SHOWCASE_TAG_LIST:
                supplied = tuple(tag.key for tag in read_showcase_tags(value))
            elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
                supplied = value
            else:
                supplied = (value,)
            if any(item not in allowed for item in supplied):
                return SimpleIssue(field=self.id, code=FieldErrorCode.UNKNOWN_OPTION)
        return None


@dataclass(frozen=True, slots=True)
class FormSection:
    """Ordered fields presented together when a renderer has enough space."""

    id: str
    title: str
    fields: tuple[FormField, ...]
    edit_title: str | None = None
    """Replaces `title` in the edit projection, for a section whose wording is about the submission."""

    def __post_init__(self) -> None:
        _require_stable_id(self.id, "section ID")
        if not self.title.strip():
            section_id = self.id
            raise ValidationError(
                md_tr(
                    t"submissions-section-blank-title = section {section_id!s} has a blank title",
                )
            )
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
            raise ValidationError(md_tr(t"submissions-category-blank-label = category {category} has a blank label"))
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
    """One form definition, identified by schema_id and revision.

    Each category combines common_sections with its own sections. Field IDs are
    the keys in a draft's answers, while labels and controls describe how clients
    can present those fields. Protocol bounds describe which client protocol
    versions can use this definition.
    """

    schema_id: str
    revision: int
    minimum_protocol: int
    maximum_protocol: int
    common_sections: tuple[FormSection, ...]
    categories: tuple[CategoryForm, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", self.schema_id):
            msg = tr(t"submissions-schema-id-stable = schema_id must be a stable lowercase identifier")
            raise ValidationError(msg)
        if self.revision < 1 or self.minimum_protocol < 1 or self.maximum_protocol < self.minimum_protocol:
            msg = tr(
                t"submissions-form-revision-protocol = form revision and protocol bounds must be positive and ordered"
            )
            raise ValidationError(msg)
        _require_unique((section.id for section in self.common_sections), "common section IDs")
        _require_unique((category.code for category in self.categories), "category codes")
        common_fields = tuple(item for section in self.common_sections for item in section.fields)
        common_ids = tuple(item.id for item in common_fields)
        _require_unique(common_ids, "common field IDs")
        for category in self.categories:
            category_fields = category.fields
            category_ids = {item.id for item in category_fields}
            overlap = set(common_ids) & category_ids
            if overlap:
                category_code = category.code
                fields = sorted(overlap)
                raise ValidationError(
                    md_tr(
                        t"submissions-category-duplicates-common = category {category_code} duplicates "
                        t"common fields: {fields}",
                    )
                )

            known_fields = category_ids.union(common_ids)
            for item in (*common_fields, *category_fields):
                for rule in item.rules:
                    unknown = set(rule.depends_on) - known_fields
                    if unknown or item.id in rule.depends_on:
                        message = f"Invalid input fields for {category.code}.{item.id}.{rule.id}: {rule.depends_on!r}"
                        raise ValidationError(message)

    def for_purpose(self, purpose: FormPurpose) -> Self:
        """Return the form for one task: only its fields, with edit rules applied when editing.

        Sections left without fields are dropped, so a submission client never sees an empty
        "Links and location" section.
        """
        return replace(
            self,
            common_sections=_sections_for(self.common_sections, purpose),
            categories=tuple(
                replace(category, sections=_sections_for(category.sections, purpose)) for category in self.categories
            ),
        )

    def category(self, code: str) -> CategoryForm:
        """Resolve a category by stable code."""
        for category in self.categories:
            if category.code == code:
                return category
        category = code
        raise ValidationError(
            md_tr(t"submissions-unknown-submission-category = unknown submission category: {category}"),
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
        """First reason per field for the form renderer."""
        return self._first_reasons(
            self.answer_issues(category, answers, origin=origin, require_complete=require_complete)
        )

    @staticmethod
    def _first_reasons(issues: tuple[ValidationIssue, ...]) -> dict[str, str]:
        errors: dict[str, str] = {}
        for issue in issues:
            errors.setdefault(issue.field, issue.params.reason if isinstance(issue, RuleIssue) else issue.code.value)
        return errors

    def answer_issues(
        self,
        category: str,
        answers: Mapping[str, JSONValue],
        *,
        origin: SubmissionOrigin,
        require_complete: bool = True,
    ) -> tuple[ValidationIssue, ...]:
        """Return every issue in validation order.

        Apply visible defaults first. Unknown field IDs are errors, but values of
        known hidden fields are ignored. With require_complete=False, missing
        required answers are allowed. Dynamic options must be checked separately;
        only inline options are validated here.
        """
        issues = self._validation_issues(
            self.fields_for(category),
            self.apply_defaults(category, answers, origin=origin),
            origin=origin,
            require_complete=require_complete,
        )
        return tuple(issue.public for issue in issues)

    @staticmethod
    def _validation_issues(
        fields: tuple[FormField, ...],
        answers: Mapping[str, JSONValue],
        *,
        origin: SubmissionOrigin,
        require_complete: bool = True,
    ) -> Iterable[_ValidationIssue]:
        unknown = answers.keys() - {item.id for item in fields}
        for field_id in sorted(unknown):
            yield _ValidationIssue(
                SimpleIssue(field=field_id, code=FieldErrorCode.UNKNOWN_FIELD), frozenset({field_id})
            )

        visible = {item.id: item for item in fields if item.is_visible(answers, origin)}
        present = {field_id: value for field_id in visible if (value := answers.get(field_id)) is not None}
        reasons = {field_id: visible[field_id].validate_value(value) for field_id, value in present.items()}
        typed = present.keys() - {
            field_id
            for field_id, reason in reasons.items()
            if reason is not None and reason.code == FieldErrorCode.WRONG_TYPE
        }
        visibility = {
            field_id: item.visible_when.field_id for field_id, item in visible.items() if item.visible_when is not None
        }
        for field_id, item in visible.items():
            inputs = frozenset({field_id, visibility.get(field_id, field_id)})
            if field_id in typed:
                for rule in item.rules:
                    if typed.issuperset(rule.depends_on) and not rule.accepts(
                        present[field_id], {dependency: present[dependency] for dependency in rule.depends_on}
                    ):
                        rule_inputs = inputs.union(
                            rule.depends_on, (visibility[key] for key in rule.depends_on if key in visibility)
                        )
                        public = RuleIssue(
                            field=field_id,
                            params=RuleParams(rule=rule.id, reason=rule.reason.value),
                        )
                        yield _ValidationIssue(public, rule_inputs, rule.id)
            reason = reasons.get(field_id)
            if field_id not in present and item.required and require_complete:
                reason = SimpleIssue(field=field_id, code=FieldErrorCode.REQUIRED)
            if reason is not None:
                yield _ValidationIssue(reason, inputs)

    def validate_transition(
        self,
        category: str,
        before: Mapping[str, JSONValue],
        after: Mapping[str, JSONValue],
        *,
        origin: SubmissionOrigin,
    ) -> dict[str, str]:
        return self._first_reasons(self.transition_issues(category, before, after, origin=origin))

    def transition_issues(
        self,
        category: str,
        before: Mapping[str, JSONValue],
        after: Mapping[str, JSONValue],
        *,
        origin: SubmissionOrigin,
    ) -> tuple[ValidationIssue, ...]:
        """Report new issues and issues whose inputs changed during an edit."""
        before = self.apply_defaults(category, before, origin=origin)
        after = self.apply_defaults(category, after, origin=origin)
        fields = self.fields_for(category)
        existing = {
            (issue.public.field, issue.public.code, issue.rule_id)
            for issue in self._validation_issues(fields, before, origin=origin)
        }
        return tuple(
            issue.public
            for issue in self._validation_issues(fields, after, origin=origin)
            if (issue.public.field, issue.public.code, issue.rule_id) not in existing
            or any(before.get(key) != after.get(key) for key in issue.inputs)
        )

    def apply_defaults(
        self,
        category: str,
        answers: Mapping[str, JSONValue],
        *,
        origin: SubmissionOrigin,
    ) -> dict[str, JSONValue]:
        """Fill missing visible fields in manifest order and return a shallow copy.

        An explicit None is retained. Earlier defaults can affect the visibility
        of later fields.
        """
        normalized = dict(answers)
        for item in self.fields_for(category):
            if item.id not in normalized and item.default is not None and item.is_visible(normalized, origin):
                normalized[item.id] = item.default
        return normalized

    def visible_answers(
        self,
        category: str,
        answers: Mapping[str, JSONValue],
        *,
        origin: SubmissionOrigin,
    ) -> dict[str, JSONValue]:
        """Apply defaults, then keep only answers to fields visible for these answers and origin.

        A hidden field's stale answer, such as a license kept after the schematic was made
        private, or a source version a client was never asked for, must not reach the build.
        """
        normalized = self.apply_defaults(category, answers, origin=origin)
        visible = {item.id for item in self.fields_for(category) if item.is_visible(normalized, origin)}
        return {field_id: value for field_id, value in normalized.items() if field_id in visible}


def _sections_for(sections: tuple[FormSection, ...], purpose: FormPurpose) -> tuple[FormSection, ...]:
    projected: list[FormSection] = []
    for section in sections:
        fields = tuple(item.for_purpose(purpose) for item in section.fields if purpose in item.purposes)
        if not fields:
            continue
        title = section.edit_title if purpose is FormPurpose.EDIT and section.edit_title is not None else section.title
        projected.append(replace(section, title=title, fields=fields))
    return tuple(projected)


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
        case ValueKind.CREDIT_LIST:
            try:
                read_credits(value)
            except ValidationError:
                return False
            return True
        case ValueKind.SHOWCASE_TAG_LIST:
            try:
                read_showcase_tags(value)
            except ValidationError:
                return False
            return True
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
            md_tr(
                t"submissions-field-name-format = {label} must start with a "
                t"letter and contain only lowercase letters, digits, or underscores",
            )
        )


def _require_unique(values: Iterable[str], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ValidationError(md_tr(t"submissions-unique = {label} must be unique"))
