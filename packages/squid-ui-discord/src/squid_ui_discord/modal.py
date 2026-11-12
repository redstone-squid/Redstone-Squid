"""Build native Discord modals from declarative input specs or portable form schemas.

Builders clamp supported text lengths and check supported structure. They are not a
complete validator for every Discord payload constraint. Submission callbacks receive
raw control values; schema evaluation belongs to the caller.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, override

import discord

from squid_ui.entity import EntityType
from squid_ui.forms import FormSpec, FormText, UploadedFile
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.limits import COMPONENT_LIMITS, ComponentLimits
from squid_ui.target_types import DiscordPyAdapter
from squid_ui.text import NEUTRAL, Localization, TextLike, resolve_text
from squid_ui_discord.adapter import DISCORD_PY_27_ADAPTER, require_discord_py_capability
from squid_ui_discord.conformance import conform_modal
from squid_ui_discord.emoji import discord_emoji
from squid_ui_discord.form_fields import CheckboxGroupField as CheckboxGroupField
from squid_ui_discord.form_fields import EntityField as EntityField
from squid_ui_discord.form_fields import FileField as FileField
from squid_ui_discord.form_planning import (
    BooleanControl,
    ChoiceControl,
    DiscordFormPlan,
    EntityControl,
    FieldPlan,
    FileControl,
    StaticTextPlan,
    TextControl,
    plan_form,
)
from squid_ui_discord.form_planning import _entity_defaults as _entity_defaults

logger = logging.getLogger(__name__)

type SubmitHandler = Callable[[discord.Interaction, dict[str, str]], Awaitable[None]]
type FormSubmitHandler = Callable[[discord.Interaction, dict[str, object]], Awaitable[None]]


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
    text: TextLike
    input: TextInputSpec
    description: TextLike | None = None


@dataclass(frozen=True, slots=True)
class ModalSpec:
    title: TextLike
    items: tuple[LabelSpec | FormText, ...]


class _SpecModal(discord.ui.Modal):
    def __init__(self, spec: ModalSpec, on_submit: SubmitHandler | None, timeout: float | None) -> None:
        super().__init__(title=_resolve(spec.title, NEUTRAL), timeout=timeout)
        self._handler = on_submit
        self._inputs: dict[str, discord.ui.TextInput] = {}
        for label in spec.items:
            if isinstance(label, FormText):
                self.add_item(discord.ui.TextDisplay(_resolve(label.content, NEUTRAL)))
                continue
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
            description = _resolve(label.description, NEUTRAL) if label.description is not None else None
            self.add_item(
                discord.ui.Label(text=_resolve(label.text, NEUTRAL), description=description, component=text_input)
            )

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self._handler is None:
            return
        values = {key: text_input.value for key, text_input in self._inputs.items()}
        await self._handler(interaction, values)


class _FormModal(discord.ui.Modal):
    def __init__(self, plan: DiscordFormPlan, on_submit: FormSubmitHandler, timeout: float | None) -> None:
        super().__init__(title=plan.title, timeout=timeout)
        self._handler = on_submit
        self._readers: dict[str, Callable[[], object]] = {}
        for item in plan.items:
            if isinstance(item, StaticTextPlan):
                self.add_item(discord.ui.TextDisplay(item.content))
                continue
            component, reader = _render_control(item)
            self._readers[item.key] = reader
            self.add_item(discord.ui.Label(text=item.label, description=item.description, component=component))

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction, {key: reader() for key, reader in self._readers.items()})


def _resolve(value: TextLike, localization: Localization) -> str:
    return resolve_text(value, localization).content


def _render_control(field: FieldPlan) -> tuple[discord.ui.Item[Any], Callable[[], object]]:
    control = field.control
    if isinstance(control, TextControl):
        component = discord.ui.TextInput(
            custom_id=field.key,
            required=field.required,
            style=discord.TextStyle.paragraph if control.paragraph else discord.TextStyle.short,
            default=control.default,
            placeholder=control.placeholder,
            min_length=control.minimum,
            max_length=control.maximum,
        )
        return component, lambda: component.value
    if isinstance(control, ChoiceControl):
        if control.kind == "select":
            component = discord.ui.Select(
                custom_id=field.key,
                required=field.required,
                min_values=control.minimum,
                max_values=control.maximum,
                options=[
                    discord.SelectOption(
                        label=option.label,
                        value=option.key,
                        description=option.description,
                        default=option.default,
                        emoji=discord_emoji(option.emoji),
                    )
                    for option in control.options
                ],
            )
            if control.multiple:
                return component, lambda: component.values
            return component, lambda: component.values[0] if component.values else None
        if control.kind == "radio":
            component = discord.ui.RadioGroup(
                custom_id=field.key,
                required=field.required,
                options=[
                    discord.RadioGroupOption(
                        label=option.label,
                        value=option.key,
                        description=option.description,
                        default=option.default,
                    )
                    for option in control.options
                ],
            )
            return component, lambda: component.value
        component = discord.ui.CheckboxGroup(
            custom_id=field.key,
            required=field.required,
            min_values=control.minimum,
            max_values=control.maximum,
            options=[
                discord.CheckboxGroupOption(
                    label=option.label,
                    value=option.key,
                    description=option.description,
                    default=option.default,
                )
                for option in control.options
            ],
        )
        return component, lambda: component.values
    if isinstance(control, BooleanControl):
        component = discord.ui.Checkbox(custom_id=field.key, default=control.default)
        return component, lambda: component.value
    if isinstance(control, EntityControl):
        select_type = {
            EntityType.USER: discord.ui.UserSelect,
            EntityType.ROLE: discord.ui.RoleSelect,
            EntityType.CONVERSATION: discord.ui.ChannelSelect,
            EntityType.MENTIONABLE: discord.ui.MentionableSelect,
        }[control.entity_type]
        component = select_type(
            custom_id=field.key,
            required=field.required,
            placeholder=control.placeholder,
            min_values=control.minimum,
            max_values=control.maximum,
            default_values=[discord.SelectDefaultValue(id=key, type=kind) for key, kind in control.defaults],
        )
        return component, lambda: component.values
    assert isinstance(control, FileControl)
    component = discord.ui.FileUpload(
        custom_id=field.key,
        required=field.required,
        min_values=control.minimum,
        max_values=control.maximum,
    )
    return component, lambda: tuple(_uploaded_file(attachment) for attachment in component.values)


def _uploaded_file(attachment: discord.Attachment) -> UploadedFile:
    return UploadedFile(
        name=attachment.filename,
        media_type=attachment.content_type or "application/octet-stream",
        size=attachment.size,
        url=attachment.url,
        read=attachment.read,
    )


def build_modal(
    spec: ModalSpec,
    *,
    on_submit: SubmitHandler | None = None,
    timeout: float | None = None,
    limits: ComponentLimits = COMPONENT_LIMITS,
    strict: bool = False,
    adapter: AdapterProfile[DiscordPyAdapter] = DISCORD_PY_27_ADAPTER,
) -> discord.ui.Modal:
    """Build a modal, truncating supported text fields to their Discord limits.

    Strict mode rejects repairs instead. Use unique effective input keys: an omitted key
    uses the input label, and duplicate keys overwrite entries in the submission mapping.
    """
    require_discord_py_capability(adapter, AdapterCapability.MODAL_FORMS, "build a modal form")
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
    limits: ComponentLimits = COMPONENT_LIMITS,
    strict: bool = False,
    adapter: AdapterProfile[DiscordPyAdapter] = DISCORD_PY_27_ADAPTER,
) -> discord.ui.Modal:
    """Build native controls for a portable form schema and adapter capabilities.

    The callback receives raw values from the field readers. Evaluate the FormSpec before
    using them as validated application values; mounted form dispatch does this for you.
    """
    require_discord_py_capability(adapter, AdapterCapability.MODAL_FORMS, "build a modal form")
    plan = plan_form(spec, localization=localization, limits=limits, strict=strict)
    return render_form_modal(plan, on_submit=on_submit, timeout=timeout)


def render_form_modal(
    plan: DiscordFormPlan,
    *,
    on_submit: FormSubmitHandler,
    timeout: float | None = None,
) -> discord.ui.Modal:
    """Instantiate a validated plan with fresh controls and submission readers."""
    if plan.interventions:
        logger.warning("form modal clamped: %s", "; ".join(plan.interventions))
    return _FormModal(plan, on_submit, timeout)
