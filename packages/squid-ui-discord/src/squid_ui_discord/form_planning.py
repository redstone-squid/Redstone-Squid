"""Choose Discord modal controls and validate them before allocating SDK items.

Plans contain resolved presentation values, not mutable controls or submission handlers.
A renderer can instantiate the same plan repeatedly without sharing interaction state.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal

import discord

from squid_ui.capabilities import Capability
from squid_ui.emoji import EmojiLike
from squid_ui.entity import EntityKind, EntityRef, EntityType
from squid_ui.errors import LayoutInvariantError, LimitViolationError
from squid_ui.forms import (
    BoolField,
    ChoiceField,
    DateField,
    DateTimeField,
    DurationField,
    FloatField,
    FormField,
    FormSpec,
    FormText,
    IntField,
    MultiChoiceField,
    ScaleField,
    TextAreaField,
    TextField,
    TimeField,
    ZonedDateTimeField,
)
from squid_ui.planning.limits import COMPONENT_LIMITS, ComponentLimits
from squid_ui.text import NEUTRAL, Localization, TextLike, resolve_text
from squid_ui_discord.conformance import trim
from squid_ui_discord.form_fields import CheckboxGroupField, EntityField, FileField

logger = logging.getLogger(__name__)

FORM_CAPABILITIES = frozenset(
    {
        Capability.FORMS_MODAL,
        EntityField.capability,
        FileField.capability,
        CheckboxGroupField.capability,
    }
)


@dataclass(frozen=True, slots=True)
class TextControl:
    paragraph: bool = False
    default: str | None = None
    placeholder: str | None = None
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True, slots=True)
class OptionPlan:
    key: str
    label: str
    description: str | None = None
    default: bool = False
    emoji: EmojiLike | None = None


@dataclass(frozen=True, slots=True)
class ChoiceControl:
    kind: Literal["radio", "select", "checkboxes"]
    options: tuple[OptionPlan, ...]
    multiple: bool = False
    minimum: int = 1
    maximum: int = 1


@dataclass(frozen=True, slots=True)
class BooleanControl:
    default: bool


@dataclass(frozen=True, slots=True)
class EntityControl:
    entity_type: EntityType
    defaults: tuple[tuple[int, discord.SelectDefaultValueType], ...]
    placeholder: str | None
    minimum: int
    maximum: int


@dataclass(frozen=True, slots=True)
class FileControl:
    minimum: int
    maximum: int


type FormControl = TextControl | ChoiceControl | BooleanControl | EntityControl | FileControl


@dataclass(frozen=True, slots=True)
class FieldPlan:
    key: str
    label: str
    description: str | None
    required: bool
    control: FormControl


@dataclass(frozen=True, slots=True)
class StaticTextPlan:
    content: str


@dataclass(frozen=True, slots=True)
class DiscordFormPlan:
    """A resolved modal and its input environment, retained for safe presentation reuse."""

    spec: FormSpec
    localization: Localization
    limits: ComponentLimits
    strict: bool
    title: str
    items: tuple[FieldPlan | StaticTextPlan, ...]
    interventions: tuple[str, ...] = ()


class _Planner:
    def __init__(self, localization: Localization, limits: ComponentLimits) -> None:
        self.localization = localization
        self.limits = limits
        self.interventions: list[str] = []

    def text(self, value: TextLike, maximum: int, path: str) -> str:
        resolved = resolve_text(value, self.localization).content
        if len(resolved) > maximum:
            self.interventions.append(f"{path} {len(resolved)} > {maximum}")
            return trim(resolved, maximum)
        return resolved

    def optional_text(self, value: TextLike | None, maximum: int, path: str) -> str | None:
        return None if value is None else self.text(value, maximum, path)

    def identifier(self, value: str, maximum: int, path: str) -> None:
        # Truncating identifiers would break the schema's submission mapping.
        if not 1 <= len(value) <= maximum:
            msg = f"{path} must contain 1-{maximum} characters"
            raise LayoutInvariantError(msg)

    def control(self, field: FormField[Any], prefill: object) -> FormControl:
        limits = self.limits
        path = f"form field {field.key!r}"
        if isinstance(
            field,
            TextField
            | IntField
            | FloatField
            | DurationField
            | DateField
            | TimeField
            | DateTimeField
            | ZonedDateTimeField,
        ):
            maximum = field.maximum if isinstance(field, TextField) else None
            minimum = field.minimum if isinstance(field, TextField) else None
            if maximum is not None and maximum > limits.text_input_value:
                self.interventions.append(f"{path} max_length {maximum} > {limits.text_input_value}")
                maximum = limits.text_input_value
            if minimum is not None and not 0 <= minimum <= (
                maximum if maximum is not None else limits.text_input_value
            ):
                msg = f"{path} has incompatible text length bounds"
                raise LayoutInvariantError(msg)
            if maximum is not None and maximum < 1:
                msg = f"{path} max_length must be positive"
                raise LayoutInvariantError(msg)
            return TextControl(
                paragraph=isinstance(field, TextAreaField),
                default=self.optional_text(
                    None if prefill is None else str(prefill),
                    maximum if maximum is not None else limits.text_input_value,
                    f"{path} default",
                ),
                placeholder=self.optional_text(field.placeholder, limits.text_input_placeholder, f"{path} placeholder"),
                minimum=minimum,
                maximum=maximum,
            )
        if isinstance(field, ScaleField):
            selected = None if prefill is None else str(field.format(prefill))
            if 2 <= len(field.points) <= 10:
                return ChoiceControl(
                    "radio",
                    tuple(
                        OptionPlan(
                            str(point),
                            self.text(field.label_for(point), limits.option_label, f"{path} option label"),
                            default=str(point) == selected,
                        )
                        for point in field.points
                    ),
                )
            return TextControl(
                default=self.optional_text(selected, limits.text_input_value, f"{path} default"),
                placeholder=self.text(
                    f"{field.minimum}\N{EN DASH}{field.maximum}", limits.text_input_placeholder, f"{path} placeholder"
                ),
            )
        if isinstance(field, ChoiceField | MultiChoiceField | CheckboxGroupField):
            multiple = not isinstance(field, ChoiceField)
            if isinstance(field, CheckboxGroupField):
                kind = "checkboxes"
                cap = 10
            elif (
                isinstance(field, ChoiceField)
                and 2 <= len(field.options) <= 10
                and not any(option.emoji is not None for option in field.options)
            ):
                kind = "radio"
                cap = 10
            else:
                kind = "select"
                cap = limits.select_options
            if not 1 <= len(field.options) <= cap:
                msg = f"Discord choice field {field.key!r} needs 1-{cap} options"
                raise LayoutInvariantError(msg)
            selected = field.format(prefill)
            options: list[OptionPlan] = []
            for option in field.options:
                self.identifier(option.key, limits.option_value, f"{path} option key {option.key!r}")
                options.append(
                    OptionPlan(
                        option.key,
                        self.text(option.label, limits.option_label, f"{path} option label"),
                        self.optional_text(option.description, limits.option_description, f"{path} option description"),
                        option.key in selected if isinstance(selected, tuple) else option.key == selected,
                        option.emoji,
                    )
                )
            minimum = field.minimum if isinstance(field, MultiChoiceField | CheckboxGroupField) else int(field.required)
            maximum = (
                (len(field.options) if field.maximum is None else field.maximum)
                if isinstance(field, MultiChoiceField | CheckboxGroupField)
                else 1
            )
            if not 0 <= minimum <= maximum <= len(options) or maximum < 1:
                msg = f"{path} has incompatible selection bounds"
                raise LayoutInvariantError(msg)
            return ChoiceControl(kind, tuple(options), multiple, minimum, maximum)
        if isinstance(field, BoolField):
            return BooleanControl(bool(prefill))
        if isinstance(field, EntityField):
            if not 0 <= field.minimum <= field.maximum <= limits.select_options or field.maximum < 1:
                msg = f"{path} has incompatible entity selection bounds"
                raise LayoutInvariantError(msg)
            defaults = _entity_defaults(prefill, field.entity_type)
            if len(defaults) > field.maximum:
                msg = f"{path} has more defaults than max_values"
                raise LayoutInvariantError(msg)
            return EntityControl(
                field.entity_type,
                tuple((value.id, value.type) for value in defaults),
                self.optional_text(field.placeholder, limits.select_placeholder, f"{path} placeholder"),
                field.minimum,
                field.maximum,
            )
        if isinstance(field, FileField):
            if not 0 <= field.minimum <= field.maximum <= 10 or field.maximum < 1:
                msg = f"Discord file field {field.key!r} needs 0-10 files with a positive maximum"
                raise LayoutInvariantError(msg)
            return FileControl(field.minimum, field.maximum)
        msg = f"Discord has no adapter for form field {field.key!r} ({type(field).__name__})"
        raise LayoutInvariantError(msg)


def plan_form(
    spec: FormSpec,
    *,
    localization: Localization = NEUTRAL,
    limits: ComponentLimits = COMPONENT_LIMITS,
    strict: bool = False,
) -> DiscordFormPlan:
    """Resolve a schema into controls, rejecting structures Discord cannot represent.

    Display text is trimmed unless strict mode forbids it. Field and option identifiers
    are never repaired, because submissions must still match the original schema.
    """
    adapted = spec.adapt(FORM_CAPABILITIES, maximum_fields=limits.modal_components)
    planner = _Planner(localization, limits)
    title = planner.text(adapted.title, limits.modal_title, "modal title")
    items: list[FieldPlan | StaticTextPlan] = []
    for field in adapted.items:
        if isinstance(field, FormText):
            items.append(StaticTextPlan(planner.text(field.content, limits.modal_text, "modal text display")))
            continue
        planner.identifier(field.key, limits.custom_id, f"form field {field.key!r} key")
        description = field.description
        if description is None and isinstance(field, ZonedDateTimeField):
            description = field.timezone
        items.append(
            FieldPlan(
                field.key,
                planner.text(
                    field.label if field.label is not None else field.key,
                    limits.label_text,
                    f"field {field.key!r} label",
                ),
                planner.optional_text(description, limits.label_description, f"field {field.key!r} description"),
                field.required,
                planner.control(field, adapted.prefill_for(field)),
            )
        )
    if strict and planner.interventions:
        raise LimitViolationError(planner.interventions)
    return DiscordFormPlan(spec, localization, limits, strict, title, tuple(items), tuple(planner.interventions))


_ENTITY_DEFAULT_TYPES: dict[EntityKind, discord.SelectDefaultValueType] = {
    EntityKind.USER: discord.SelectDefaultValueType.user,
    EntityKind.ROLE: discord.SelectDefaultValueType.role,
    EntityKind.CONVERSATION: discord.SelectDefaultValueType.channel,
}

_FIELD_DEFAULT_TYPES: dict[EntityType, discord.SelectDefaultValueType] = {
    EntityType.USER: discord.SelectDefaultValueType.user,
    EntityType.ROLE: discord.SelectDefaultValueType.role,
    EntityType.CONVERSATION: discord.SelectDefaultValueType.channel,
}


def _entity_defaults(prefill: object, entity_type: EntityType) -> list[discord.SelectDefaultValue]:
    """Narrow a stored entity prefill to the preselected values discord.py accepts.

    `EntityField` is an `ExtensionField[object]`, so a prefill arrives as whatever the store
    round-tripped: the discord entities the select handed back last time, portable `EntityRef`s,
    or bare snowflake ids. Everything resolves to an explicit `SelectDefaultValue` rather than a
    bare `discord.Object`, because discord.py can only infer a missing type from the select's own
    kind and rejects an untyped object outright on a mentionable select. A value whose kind cannot
    be determined is dropped: a preselection is a convenience, and guessing it wrong preselects the
    wrong entity.
    """
    values = prefill if isinstance(prefill, list | tuple) else (() if prefill is None else (prefill,))
    defaults: list[discord.SelectDefaultValue] = []
    for value in values:
        match value:
            case discord.SelectDefaultValue():
                defaults.append(value)
            case EntityRef(id=int() as entity_id):
                defaults.append(discord.SelectDefaultValue(id=entity_id, type=_ENTITY_DEFAULT_TYPES[value.kind]))
            case discord.Role():
                defaults.append(discord.SelectDefaultValue(id=value.id, type=discord.SelectDefaultValueType.role))
            case discord.Member() | discord.User() | discord.ClientUser():
                defaults.append(discord.SelectDefaultValue(id=value.id, type=discord.SelectDefaultValueType.user))
            case discord.Thread() | discord.abc.GuildChannel():
                defaults.append(discord.SelectDefaultValue(id=value.id, type=discord.SelectDefaultValueType.channel))
            case int() if entity_type in _FIELD_DEFAULT_TYPES:
                defaults.append(discord.SelectDefaultValue(id=value, type=_FIELD_DEFAULT_TYPES[entity_type]))
            case _:
                logger.warning("dropping unrecognized %s prefill of type %s", entity_type, type(value).__name__)
    return defaults
