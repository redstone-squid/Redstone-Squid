"""Boundary gate that keeps built views inside Discord's limits.

Defense in depth: the layout engine should never produce an oversized view, but a measurement
bug or a discord.py serialization change would otherwise surface as HTTP 50035 at send time.
discord.py validates child *counts* locally and no string lengths at all, so this gate walks a
built view, clamps every length it can, and reports each intervention. Tests treat any clamp as
a failure; production degrades to an ugly-but-delivered message.
"""

import discord
from discord.ui.select import BaseSelect

from squid_layouts.planning.limits import ELLIPSIS, LIMITS, V2Limits


class LimitViolationError(Exception):
    """A built view exceeds a Discord limit and the caller forbade clamping."""

    def __init__(self, interventions: list[str]) -> None:
        super().__init__("; ".join(interventions))
        self.interventions = interventions


def trim(text: str, limit: int) -> str:
    """Trim to at most ``limit`` characters, marking any cut with a trailing ellipsis."""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return ELLIPSIS if limit == 1 else ""
    return text[: limit - 1].rstrip() + ELLIPSIS


def conform(view: discord.ui.LayoutView, *, strict: bool = False, limits: V2Limits = LIMITS) -> list[str]:
    """Clamp ``view`` in place to Discord's limits and describe every clamp applied.

    Args:
        view: The built view; mutated when anything exceeds a limit.
        strict: Raise :class:`LimitViolationError` after clamping instead of returning quietly.
        limits: The limit table to enforce.

    Returns:
        One human-readable description per intervention; empty when the view already fit.
    """
    interventions: list[str] = []
    text_displays: list[discord.ui.TextDisplay] = []  # pyrefly: ignore  # generic Item variance

    children = list(view.walk_children())
    if len(children) > limits.total_components:
        # Structural overflow cannot be clamped without redesigning the view; report only.
        interventions.append(f"{len(children)} components exceed {limits.total_components} (not clampable)")

    for item in children:
        if isinstance(item, discord.ui.TextDisplay):
            text_displays.append(item)
        elif isinstance(item, discord.ui.Button):
            _conform_button(item, limits, interventions)
        elif isinstance(item, BaseSelect):
            _conform_select(item, limits, interventions)
        elif isinstance(item, discord.ui.MediaGallery):
            _conform_gallery(item, limits, interventions)

    _conform_text_budget(text_displays, limits, interventions)

    if strict and interventions:
        raise LimitViolationError(interventions)
    return interventions


def conform_modal(modal: discord.ui.Modal, *, strict: bool = False, limits: V2Limits = LIMITS) -> list[str]:
    """Clamp ``modal`` in place to Discord's limits; same contract as :func:`conform`."""
    interventions: list[str] = []

    if len(modal.title) > limits.modal_title:
        interventions.append(f"modal title {len(modal.title)} > {limits.modal_title}")
        modal.title = trim(modal.title, limits.modal_title)

    for child in modal.children:
        text_input: discord.ui.TextInput | None = None
        if isinstance(child, discord.ui.Label):
            if len(child.text) > limits.label_text:
                interventions.append(f"label text {len(child.text)} > {limits.label_text}")
                child.text = trim(child.text, limits.label_text)
            if child.description is not None and len(child.description) > limits.label_description:
                interventions.append(f"label description {len(child.description)} > {limits.label_description}")
                child.description = trim(child.description, limits.label_description)
            if isinstance(child.component, discord.ui.TextInput):
                text_input = child.component
        elif isinstance(child, discord.ui.TextInput):
            text_input = child
        if text_input is not None:
            _conform_text_input(text_input, limits, interventions)

    if strict and interventions:
        raise LimitViolationError(interventions)
    return interventions


def _report_custom_id(item: discord.ui.Button | BaseSelect, limits: V2Limits, interventions: list[str]) -> None:
    """Report an over-budget custom id, never clamp it.

    Every other string here degrades gracefully when trimmed. A custom id does not: a
    shortened one routes to a different handler or to none, so the only safe outcome is to
    say so. `Route.id` refuses the same thing earlier and with a better message, but a
    `RoutedButton` built by hand or read back through the codec never passes through it, and
    an invalid state that only Discord rejects is exactly what this gate exists to catch.
    """
    custom_id = getattr(item, "custom_id", None)
    if isinstance(custom_id, str) and len(custom_id) > limits.custom_id:
        interventions.append(f"custom id {len(custom_id)} > {limits.custom_id} (not clampable): {custom_id[:32]!r}...")


def _conform_button(button: discord.ui.Button, limits: V2Limits, interventions: list[str]) -> None:
    _report_custom_id(button, limits, interventions)
    if button.label is not None and len(button.label) > limits.button_label:
        interventions.append(f"button label {len(button.label)} > {limits.button_label}")
        button.label = trim(button.label, limits.button_label)


def _conform_select(select: BaseSelect, limits: V2Limits, interventions: list[str]) -> None:
    _report_custom_id(select, limits, interventions)
    if select.placeholder is not None and len(select.placeholder) > limits.select_placeholder:
        interventions.append(f"select placeholder {len(select.placeholder)} > {limits.select_placeholder}")
        select.placeholder = trim(select.placeholder, limits.select_placeholder)
    if not isinstance(select, discord.ui.Select):
        return
    options = select.options
    if len(options) > limits.select_options:
        interventions.append(f"{len(options)} select options exceed {limits.select_options}")
        options = options[: limits.select_options]
    for option in options:
        if len(option.label) > limits.option_label:
            interventions.append(f"option label {len(option.label)} > {limits.option_label}")
            option.label = trim(option.label, limits.option_label)
        if len(option.value) > limits.option_value:
            # Values are identifiers: a marker would corrupt them no less than the cut does.
            interventions.append(f"option value {len(option.value)} > {limits.option_value}")
            option.value = option.value[: limits.option_value]
        if option.description is not None and len(option.description) > limits.option_description:
            interventions.append(f"option description {len(option.description)} > {limits.option_description}")
            option.description = trim(option.description, limits.option_description)
    select.options = options


def _conform_gallery(gallery: discord.ui.MediaGallery, limits: V2Limits, interventions: list[str]) -> None:
    items = gallery.items
    if len(items) > limits.gallery_items:
        interventions.append(f"{len(items)} gallery items exceed {limits.gallery_items}")
        items = items[: limits.gallery_items]
    changed = False
    for media_item in items:
        if media_item.description is not None and len(media_item.description) > limits.gallery_item_description:
            interventions.append(
                f"gallery item description {len(media_item.description)} > {limits.gallery_item_description}"
            )
            media_item.description = trim(media_item.description, limits.gallery_item_description)
            changed = True
    if changed or len(gallery.items) != len(items):
        gallery.items = items


def _conform_text_input(text_input: discord.ui.TextInput, limits: V2Limits, interventions: list[str]) -> None:
    cap = limits.text_input_value
    if text_input.max_length is not None and text_input.max_length > cap:
        interventions.append(f"text input max_length {text_input.max_length} > {cap}")
        text_input.max_length = cap
    effective = text_input.max_length if text_input.max_length is not None else cap
    if text_input.default is not None and len(text_input.default) > effective:
        interventions.append(f"text input default {len(text_input.default)} > {effective}")
        text_input.default = trim(text_input.default, effective)
    if text_input.placeholder is not None and len(text_input.placeholder) > limits.text_input_placeholder:
        interventions.append(f"text input placeholder {len(text_input.placeholder)} > {limits.text_input_placeholder}")
        text_input.placeholder = trim(text_input.placeholder, limits.text_input_placeholder)


def _conform_text_budget(
    text_displays: list[discord.ui.TextDisplay], limits: V2Limits, interventions: list[str]
) -> None:
    total = sum(len(td.content) for td in text_displays)
    if total <= limits.total_text:
        return
    interventions.append(f"total display text {total} > {limits.total_text}")
    # Allocate front to back so earlier content survives; every later node is still reserved
    # one character so no TextDisplay is ever emptied (Discord rejects empty content).
    used = 0
    for index, td in enumerate(text_displays):
        reserved_for_rest = len(text_displays) - index - 1
        allowed = max(1, limits.total_text - used - reserved_for_rest)
        if len(td.content) > allowed:
            td.content = trim(td.content, allowed)
        used += len(td.content)
