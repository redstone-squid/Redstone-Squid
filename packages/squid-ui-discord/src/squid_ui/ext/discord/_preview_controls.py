"""HTML for Discord controls, retaining binding metadata without dispatching actions."""

import htpy as h

from squid_ui import scene
from squid_ui.entity import EntityKind, EntityRef
from squid_ui.ext.html.safety import safe_url


def _binding(
    node: scene.Button | scene.RoutedButton | scene.Select | scene.RoutedSelect | scene.EntitySelect,
) -> dict[str, h.Attribute]:
    if isinstance(node, scene.RoutedButton | scene.RoutedSelect):
        return {"data-route-id": node.route_id}
    attributes: dict[str, h.Attribute] = {"data-squid-action": node.action}
    if isinstance(node, scene.EntitySelect):
        attributes["data-squid-entity"] = node.entity_type.value
    attributes["data-squid-mode"] = node.mode.value
    return attributes


def _label(node: scene.Button | scene.RoutedButton | scene.Link) -> h.Node:
    return (
        (h.span(class_="squid-button__emoji")[node.emoji.name], " ") if node.emoji else None,
        node.label or "",
    )


def button(node: scene.Button | scene.RoutedButton | scene.PremiumButton) -> h.Element:
    if isinstance(node, scene.PremiumButton):
        return h.button(type="button", class_="squid-button squid-premium", disabled=True, data_sku_id=node.sku_id)[
            "Premium"
        ]
    return h.button(
        type="button",
        class_=f"squid-button squid-button--{node.style.value}",
        **_binding(node),
        disabled=node.disabled,
    )[_label(node)]


def link(node: scene.Link) -> h.Element:
    url = safe_url(node.url)
    if url is None or node.disabled:
        return h.span(class_="squid-button squid-link", aria_disabled="true")[_label(node)]
    return h.a(class_="squid-button squid-link", href=url, rel="noopener noreferrer")[_label(node)]


def select(node: scene.Select | scene.RoutedSelect | scene.EntitySelect) -> h.Element:
    prompt_selected = not isinstance(node, scene.EntitySelect) or not node.default_values
    prompt = (
        h.option(value="", disabled=True, selected=prompt_selected)[node.placeholder]
        if node.placeholder is not None
        else None
    )
    if isinstance(node, scene.EntitySelect):
        # Discord fills entity pickers from the guild. Preview only the selected mentions.
        options = [h.option(value=str(ref.id), selected=True)[_mention(ref)] for ref in node.default_values]
    else:
        options = [
            h.option(value=option.value, selected=option.default)[
                (option.emoji.name + " ") if option.emoji else "", option.label
            ]
            for option in node.options
        ]
    return h.select(
        class_="squid-select",
        **_binding(node),
        data_squid_min=node.min_values,
        data_squid_max=node.max_values,
        multiple=node.max_values > 1,
        disabled=node.disabled,
    )[prompt, options]


def _mention(ref: EntityRef) -> str:
    prefix = {EntityKind.USER: "@", EntityKind.ROLE: "@&", EntityKind.CONVERSATION: "#"}[ref.kind]
    return f"<{prefix}{ref.id}>"
