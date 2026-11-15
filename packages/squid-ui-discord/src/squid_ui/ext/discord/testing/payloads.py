"""Inspect serialized component payloads and check supported Discord limits."""

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import discord
from squid_ui.ext.discord.limits import COMPONENT_LIMITS, LIMITS, ComponentLimits, V2Limits
from squid_ui.selectors import query as query_tree
from squid_ui.selectors import select

type ComponentPayload = dict[str, Any]


type BuiltView = discord.ui.LayoutView | discord.ui.View | list[ComponentPayload]
"""A built native view or an already serialized payload."""


def _payload_children(node: object) -> Iterator[tuple[str, object]]:
    """Serialized component edges; options and gallery items are metadata, not components."""
    if not isinstance(node, dict):
        return
    for index, component in enumerate(node.get("components", [])):
        yield f"components.{index}", component
    for name in ("accessory", "component"):
        if (nested := node.get(name)) is not None:
            yield name, nested


def iter_component_payloads(components: list[ComponentPayload]) -> Iterator[ComponentPayload]:
    """Yield every serialized component occurrence in depth-first order.

    Only component edges are followed. Repeated references count at each placement,
    while ancestor cycles raise ValueError instead of recursing indefinitely.
    """
    for match in query_tree(components, select(dict), children=_payload_children).all():
        yield match.node


def _check_string(problems: list[str], component: ComponentPayload, key: str, limit: int, where: str) -> None:
    value = component.get(key)
    if isinstance(value, str) and len(value) > limit:
        problems.append(f"{where} {key} is {len(value)} chars (limit {limit})")


def payload_problems(components: list[ComponentPayload], *, limits: V2Limits = LIMITS) -> list[str]:
    """Report the component counts and string lengths checked by this helper."""
    problems: list[str] = []
    flattened = list(iter_component_payloads(components))

    if len(flattened) > limits.total_components:
        problems.append(f"{len(flattened)} components (limit {limits.total_components})")

    total_text = sum(len(c.get("content", "")) for c in flattened if c.get("type") == 10)
    if total_text > limits.total_text:
        problems.append(f"total display text is {total_text} chars (limit {limits.total_text})")

    for component in flattened:
        match component.get("type"):
            case 2:
                _check_string(problems, component, "label", limits.components.button_label, "button")
                _check_string(problems, component, "custom_id", limits.components.custom_id, "button")
            case 3:
                _check_string(problems, component, "placeholder", limits.components.select_placeholder, "select")
                options = component.get("options", [])
                if len(options) > limits.components.select_options:
                    problems.append(f"{len(options)} select options (limit {limits.components.select_options})")
                for option in options:
                    _check_string(problems, option, "label", limits.components.option_label, "option")
                    _check_string(problems, option, "value", limits.components.option_value, "option")
                    _check_string(problems, option, "description", limits.components.option_description, "option")
            case 4:
                _check_string(
                    problems, component, "placeholder", limits.components.text_input_placeholder, "text input"
                )
                _check_string(problems, component, "value", limits.components.text_input_value, "text input")
                max_length = component.get("max_length")
                if max_length is not None and max_length > limits.components.text_input_value:
                    problems.append(f"text input max_length {max_length} (limit {limits.components.text_input_value})")
            case 5 | 6 | 7 | 8:
                _check_string(problems, component, "placeholder", limits.components.select_placeholder, "select")
            case 9:
                texts = component.get("components", [])
                if len(texts) > limits.section_texts:
                    problems.append(f"section holds {len(texts)} texts (limit {limits.section_texts})")
            case 12:
                items = component.get("items", [])
                if len(items) > limits.gallery_items:
                    problems.append(f"{len(items)} gallery items (limit {limits.gallery_items})")
                for item in items:
                    _check_string(problems, item, "description", limits.gallery_item_description, "gallery item")
            case 18:
                _check_string(problems, component, "label", limits.components.label_text, "label")
                _check_string(problems, component, "description", limits.components.label_description, "label")
            case _:
                pass

    return problems


def modal_problems(payload: dict[str, Any], *, limits: ComponentLimits = COMPONENT_LIMITS) -> list[str]:
    """Report supported modal size checks; this is not complete Discord schema validation."""
    problems: list[str] = []
    title = payload.get("title", "")
    if len(title) > limits.modal_title:
        problems.append(f"modal title is {len(title)} chars (limit {limits.modal_title})")
    components = payload.get("components", [])
    if len(components) > limits.modal_components:
        problems.append(f"modal holds {len(components)} components (limit {limits.modal_components})")
    # A modal holds no gallery or section, so only the component half of the V2
    # table can matter; carry the caller's over so an overridden cap still applies.
    problems.extend(payload_problems(components, limits=replace(LIMITS, components=limits)))
    return problems


def assert_within_limits(built: discord.ui.LayoutView | discord.ui.Modal, *, limits: V2Limits = LIMITS) -> None:
    """Assert that the serialized view or modal passes the supported size checks."""
    if isinstance(built, discord.ui.Modal):
        problems = modal_problems(built.to_dict(), limits=limits.components)
    else:
        problems = payload_problems(built.to_components(), limits=limits)
    assert not problems, "; ".join(problems)


def _payloads(built: BuiltView) -> list[ComponentPayload]:
    components = built if isinstance(built, list) else built.to_components()
    return list(iter_component_payloads(components))


def payload_texts(built: BuiltView) -> list[str]:
    """Every display text Discord will receive, in order.

    Reads the serialized payload rather than walking `discord.ui` objects. The two agree on
    content, but only this one keeps agreeing when discord.py changes how it builds the tree --
    and, unlike matching a substring against `str(view.to_components())`, it cannot pass
    because the string turned up in a custom id or a placeholder.
    """
    return [component["content"] for component in _payloads(built) if component.get("type") == 10]


def payload_labels(built: BuiltView) -> list[str]:
    """Every button label Discord will receive, in order."""
    return [component["label"] for component in _payloads(built) if component.get("type") == 2 and "label" in component]


def payload_custom_ids(built: BuiltView) -> list[str]:
    """Every custom id Discord will receive, in order, across every interactive component."""
    return [component["custom_id"] for component in _payloads(built) if "custom_id" in component]
