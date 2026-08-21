"""Declarative modals: specs in, clamped discord.py modals out.

discord.py validates none of a modal's string lengths, so an oversized title or — the classic
crash — a `default` joined from user data fails at `send_modal` time with HTTP 50035.
`build_modal` runs every spec through the conform gate, making that unrepresentable.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

import discord

from squid_layouts.discord.conform import conform_modal
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.forms import (
    BoolField,
    ChoiceField,
    DateField,
    DurationField,
    ExtensionField,
    FloatField,
    FormField,
    FormSpec,
    IntField,
    TextAreaField,
    TextField,
)
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.text import NEUTRAL, Localization, TextLike, resolve_text

logger = logging.getLogger(__name__)

type SubmitHandler = Callable[[discord.Interaction, dict[str, str]], Awaitable[None]]
type FormSubmitHandler = Callable[[discord.Interaction, dict[str, object]], Awaitable[None]]


class EntityType(StrEnum):
    """Discord entity picker families available inside a modal."""

    USER = "user"
    ROLE = "role"
    CHANNEL = "channel"
    MENTIONABLE = "mentionable"


@dataclass(frozen=True, slots=True)
class EntityField(ExtensionField[object]):
    """A Discord user, role, channel, or mentionable picker."""

    entity_type: EntityType = EntityType.USER
    minimum: int = 1
    maximum: int = 1
    placeholder: TextLike | None = None
    capability: ClassVar[str] = "forms.discord.entity"

    def parse(self, raw: object) -> object | None:
        values = tuple(raw) if isinstance(raw, list | tuple) else (() if raw is None else (raw,))
        if not values:
            if self.required:
                message = "This field is required."
                raise ValueError(message)
            return None if self.maximum == 1 else ()
        return values[0] if self.maximum == 1 else values


@dataclass(frozen=True, slots=True)
class FileField(ExtensionField[object]):
    """One or more Discord attachments uploaded through a modal."""

    minimum: int = 1
    maximum: int = 1
    capability: ClassVar[str] = "forms.discord.file"

    def parse(self, raw: object) -> object | None:
        values = tuple(raw) if isinstance(raw, list | tuple) else (() if raw is None else (raw,))
        if not values:
            if self.required:
                message = "This field is required."
                raise ValueError(message)
            return None if self.maximum == 1 else ()
        return values[0] if self.maximum == 1 else values


@dataclass(frozen=True, slots=True)
class TextInputSpec:
    label: str
    key: str | None = None
    default: str | None = None
    placeholder: str | None = None
    required: bool = True
    long: bool = False
    min_length: int | None = None
    max_length: int | None = None


@dataclass(frozen=True, slots=True)
class LabelSpec:
    text: str
    input: TextInputSpec
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ModalSpec:
    title: str
    labels: tuple[LabelSpec, ...]


class _SpecModal(discord.ui.Modal):
    def __init__(self, spec: ModalSpec, on_submit: SubmitHandler | None, timeout: float | None) -> None:
        super().__init__(title=spec.title, timeout=timeout)
        self._handler = on_submit
        self._inputs: dict[str, discord.ui.TextInput] = {}
        for label in spec.labels:
            field = label.input
            style = discord.TextStyle.paragraph if field.long else discord.TextStyle.short
            text_input: discord.ui.TextInput = discord.ui.TextInput(
                label=field.label,
                style=style,
                default=field.default,
                placeholder=field.placeholder,
                required=field.required,
                min_length=field.min_length,
                max_length=field.max_length,
            )
            self._inputs[field.key or field.label] = text_input
            self.add_item(discord.ui.Label(text=label.text, description=label.description, component=text_input))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self._handler is None:
            return
        values = {key: text_input.value for key, text_input in self._inputs.items()}
        await self._handler(interaction, values)


class _FormModal(discord.ui.Modal):
    def __init__(
        self,
        spec: FormSpec,
        on_submit: FormSubmitHandler,
        timeout: float | None,
        localization: Localization,
    ) -> None:
        super().__init__(title=_resolve(spec.title, localization), timeout=timeout)
        self._handler = on_submit
        self._readers: dict[str, Callable[[], object]] = {}
        for field in spec.fields:
            component, reader = _form_component(field, spec.prefill_for(field), localization)
            label = _resolve(field.label, localization) if field.label is not None else field.key
            description = _resolve(field.description, localization) if field.description is not None else None
            self._readers[field.key] = reader
            self.add_item(discord.ui.Label(text=label, description=description, component=component))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction, {key: reader() for key, reader in self._readers.items()})


def _resolve(value: TextLike, localization: Localization) -> str:
    return resolve_text(value, localization).content


def _text_input(
    field: TextField | IntField | FloatField | DurationField | DateField,
    prefill: object,
    localization: Localization,
) -> discord.ui.TextInput:
    placeholder = _resolve(field.placeholder, localization) if field.placeholder is not None else None
    maximum = field.maximum if isinstance(field, TextField) else None
    return discord.ui.TextInput(
        custom_id=field.key,
        style=discord.TextStyle.paragraph if isinstance(field, TextAreaField) else discord.TextStyle.short,
        default=None if prefill is None else str(prefill),
        placeholder=placeholder,
        required=field.required,
        min_length=field.minimum if isinstance(field, TextField) else None,
        max_length=maximum,
    )


def _form_component(
    field: FormField[Any],
    prefill: object,
    localization: Localization,
) -> tuple[discord.ui.Item[Any], Callable[[], object]]:
    if isinstance(field, TextField | IntField | FloatField | DurationField | DateField):
        component = _text_input(field, prefill, localization)
        return component, lambda: component.value
    if isinstance(field, ChoiceField):
        if not 2 <= len(field.options) <= 10:
            message = f"Discord choice field {field.key!r} needs 2-10 options"
            raise LayoutInvariantError(message)
        selected = field.format_prefill(prefill)
        component = discord.ui.RadioGroup(
            custom_id=field.key,
            required=field.required,
            options=[
                discord.RadioGroupOption(
                    label=_resolve(option.label, localization),
                    value=option.key,
                    description=(
                        _resolve(option.description, localization) if option.description is not None else None
                    ),
                    default=option.key == selected,
                )
                for option in field.options
            ],
        )
        return component, lambda: component.value
    if isinstance(field, BoolField):
        component = discord.ui.Checkbox(custom_id=field.key, default=bool(prefill))
        return component, lambda: component.value
    if isinstance(field, EntityField):
        select_type = {
            EntityType.USER: discord.ui.UserSelect,
            EntityType.ROLE: discord.ui.RoleSelect,
            EntityType.CHANNEL: discord.ui.ChannelSelect,
            EntityType.MENTIONABLE: discord.ui.MentionableSelect,
        }[field.entity_type]
        placeholder = _resolve(field.placeholder, localization) if field.placeholder is not None else None
        defaults = list(prefill) if isinstance(prefill, list | tuple) else ([] if prefill is None else [prefill])
        component = select_type(
            custom_id=field.key,
            placeholder=placeholder,
            min_values=field.minimum,
            max_values=field.maximum,
            required=field.required,
            default_values=defaults,
        )
        return component, lambda: component.values
    if isinstance(field, FileField):
        component = discord.ui.FileUpload(
            custom_id=field.key,
            required=field.required,
            min_values=field.minimum,
            max_values=field.maximum,
        )
        return component, lambda: component.values
    message = f"Discord has no adapter for form field {type(field).__name__}"
    raise LayoutInvariantError(message)


def build_modal(
    spec: ModalSpec,
    *,
    on_submit: SubmitHandler | None = None,
    timeout: float | None = None,
    limits: V2Limits = LIMITS,
    strict: bool = False,
) -> discord.ui.Modal:
    """Build a modal from a spec, clamped so `send_modal` can never 50035 on lengths.

    ``on_submit`` receives the input values keyed by each field's ``key`` (or label).
    """
    modal = _SpecModal(spec, on_submit, timeout)
    interventions = conform_modal(modal, strict=strict, limits=limits)
    if interventions:
        logger.warning("modal clamped: %s", "; ".join(interventions))
    return modal


def build_form_modal(
    spec: FormSpec,
    *,
    on_submit: FormSubmitHandler,
    timeout: float | None = None,
    localization: Localization = NEUTRAL,
    limits: V2Limits = LIMITS,
    strict: bool = False,
) -> discord.ui.Modal:
    """Build a Discord modal from a portable form schema."""
    adapted = spec.adapt(
        frozenset({"forms.modal", EntityField.capability, FileField.capability}),
        maximum_fields=limits.modal_components,
    )
    modal = _FormModal(adapted, on_submit, timeout, localization)
    interventions = conform_modal(modal, strict=strict, limits=limits)
    if interventions:
        logger.warning("form modal clamped: %s", "; ".join(interventions))
    return modal
