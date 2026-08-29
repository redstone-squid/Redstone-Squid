"""Declarative modals: specs in, clamped discord.py modals out.

discord.py validates none of a modal's string lengths, so an oversized title or — the classic
crash — a `default` joined from user data fails at `send_modal` time with HTTP 50035.
`build_modal` runs every spec through the conform gate, making that unrepresentable.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import discord

from squid_layouts.discord.conform import conform_modal
from squid_layouts.planning.limits import LIMITS, V2Limits

logger = logging.getLogger(__name__)

type SubmitHandler = Callable[[discord.Interaction, dict[str, str]], Awaitable[None]]


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
