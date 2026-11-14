"""Discord formatting and control constraints shared by both message surfaces."""

from collections.abc import Callable

from squid_i18n import tree
from squid_ui import scene
from squid_ui.planning.limits import ComponentLimits
from squid_ui.planning.realizations import TextRealization


def select_violations(node: scene.Select | scene.RoutedSelect, component: ComponentLimits) -> tuple[str, ...]:
    errors: list[str] = []
    if not 1 <= len(node.options) <= component.select_options:
        errors.append("select option count")
    if not 0 <= node.min_values <= node.max_values <= len(node.options):
        errors.append("select cardinality")
    if len(node.placeholder or "") > component.select_placeholder:
        errors.append("select placeholder")
    identifier = node.action if isinstance(node, scene.Select) else node.route_id
    if len(identifier) > component.custom_id:
        errors.append("select identifier")
    for option in node.options:
        if not option.label or len(option.label) > component.option_label:
            errors.append("select option label")
        if not option.value or len(option.value) > component.option_value:
            errors.append("select option value")
        if len(option.description or "") > component.option_description:
            errors.append("select option description")
    return tuple(errors)


def discord_text(payload: TextRealization, escape: Callable[[str], str] | None = None) -> str:
    if payload.role == "code":
        content = "".join(tree.plain(node) for node in payload.nodes).replace("```", "``\u200b`")
        return f"```{payload.language}\n{content}\n```"
    content = tree.markdown(payload.nodes, discord=True, escape=escape)
    if payload.role == "heading":
        return "#" * payload.level + " " + content
    if payload.role == "note":
        return "-# " + content
    if payload.role == "quote":
        return "\n".join("> " + line for line in content.splitlines())
    return content
