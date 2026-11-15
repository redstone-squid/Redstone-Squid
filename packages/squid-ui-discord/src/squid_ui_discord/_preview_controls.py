"""HTML for Discord controls, retaining binding metadata without dispatching actions."""

from html import escape

from squid_ui_html.safety import attribute, safe_url

from squid_ui import scene
from squid_ui.entity import EntityKind, EntityRef


def _binding(node: scene.Button | scene.RoutedButton | scene.Select | scene.RoutedSelect | scene.EntitySelect) -> str:
    if isinstance(node, scene.RoutedButton | scene.RoutedSelect):
        return f'data-route-id="{attribute(node.route_id)}"'
    entity = f'data-squid-entity="{node.entity_type.value}" ' if isinstance(node, scene.EntitySelect) else ""
    return f'data-squid-action="{attribute(node.action)}" {entity}data-squid-mode="{node.mode.value}"'


def _label(node: scene.Button | scene.RoutedButton | scene.Link) -> str:
    icon = f'<span class="squid-button__emoji">{escape(node.emoji.name)}</span> ' if node.emoji else ""
    return f"{icon}{escape(node.label or '')}"


def button(node: scene.Button | scene.RoutedButton | scene.PremiumButton) -> str:
    if isinstance(node, scene.PremiumButton):
        return (
            f'<button type="button" class="squid-button squid-premium" disabled '
            f'data-sku-id="{node.sku_id}">Premium</button>'
        )
    disabled = " disabled" if node.disabled else ""
    return (
        f'<button type="button" class="squid-button squid-button--{node.style.value}" '
        f"{_binding(node)}{disabled}>{_label(node)}</button>"
    )


def link(node: scene.Link) -> str:
    url = safe_url(node.url)
    if url is None or node.disabled:
        return f'<span class="squid-button squid-link" aria-disabled="true">{_label(node)}</span>'
    return f'<a class="squid-button squid-link" href="{attribute(url)}" rel="noopener noreferrer">{_label(node)}</a>'


def select(node: scene.Select | scene.RoutedSelect | scene.EntitySelect) -> str:
    disabled = " disabled" if node.disabled else ""
    multiple = " multiple" if node.max_values > 1 else ""
    prompt_selected = not isinstance(node, scene.EntitySelect) or not node.default_values
    prompt = (
        f'<option value="" disabled{" selected" if prompt_selected else ""}>{escape(node.placeholder)}</option>'
        if node.placeholder is not None
        else ""
    )
    if isinstance(node, scene.EntitySelect):
        # Discord fills entity pickers from the guild. Preview only the selected mentions.
        options = "".join(
            f'<option value="{attribute(str(ref.id))}" selected>{escape(_mention(ref))}</option>'
            for ref in node.default_values
        )
    else:
        options = "".join(
            f'<option value="{attribute(option.value)}"{" selected" if option.default else ""}>'
            f"{escape((option.emoji.name + ' ') if option.emoji else '')}{escape(option.label)}</option>"
            for option in node.options
        )
    return (
        f'<select class="squid-select" {_binding(node)} '
        f'data-squid-min="{node.min_values}" data-squid-max="{node.max_values}"'
        f"{multiple}{disabled}>{prompt}{options}</select>"
    )


def _mention(ref: EntityRef) -> str:
    prefix = {EntityKind.USER: "@", EntityKind.ROLE: "@&", EntityKind.CONVERSATION: "#"}[ref.kind]
    return f"<{prefix}{ref.id}>"
