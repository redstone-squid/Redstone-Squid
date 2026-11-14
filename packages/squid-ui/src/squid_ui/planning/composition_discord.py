"""Discord formatting and control constraints shared by both message surfaces."""

from collections.abc import Callable
from datetime import datetime

from squid_i18n import tree
from squid_ui import scene
from squid_ui.entity import ConversationType
from squid_ui.planning.limits import ComponentLimits
from squid_ui.planning.realizations import TextRealization, TimeRealization


def select_violations(
    node: scene.Select | scene.RoutedSelect | scene.EntitySelect, component: ComponentLimits
) -> tuple[str, ...]:
    errors: list[str] = []
    if isinstance(node, scene.EntitySelect):
        if not 0 <= node.min_values <= node.max_values <= component.select_options or node.max_values < 1:
            errors.append("entity select cardinality")
        if len(node.default_values) > node.max_values:
            errors.append("entity select default count")
        if any(not node.entity_type.supports(value.kind) for value in node.default_values):
            errors.append("entity select default kind")
        if any(
            value
            in (
                ConversationType.WORKSPACE_PUBLIC,
                ConversationType.WORKSPACE_PRIVATE,
                ConversationType.DIRECT,
                ConversationType.GROUP_DIRECT,
            )
            for value in node.conversation_types
        ):
            errors.append("Discord conversation family")
        if len(node.placeholder or "") > component.select_placeholder:
            errors.append("select placeholder")
        if len(node.action) > component.custom_id:
            errors.append("select identifier")
        return tuple(errors)
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
    if isinstance(payload, TimeRealization):
        prefix = discord_time_prefix(payload, escape)
        if payload.timezone is not None:
            return prefix + payload.display
        return prefix + f"<t:{int(datetime.fromisoformat(payload.instant).timestamp())}:{payload.style}>"
    if payload.shortening is not None and not payload.visible_nodes:
        return ""
    if payload.role == "code":
        content = "".join(tree.plain(node) for node in payload.visible_nodes).replace("```", "``\u200b`")
        return f"```{payload.language}\n{content}\n```"
    content = tree.markdown(payload.visible_nodes, discord=True, escape=escape)
    if payload.role == "heading":
        return "#" * payload.level + " " + content
    if payload.role == "note":
        return "-# " + content
    if payload.role == "quote":
        return "\n".join("> " + line for line in content.splitlines())
    return content


def discord_time_prefix(payload: TimeRealization, escape: Callable[[str], str] | None = None) -> str:
    if payload.native_prefix is not None:
        return payload.native_prefix
    # Serialize the label separately so a trailing space cannot become an HTML entity.
    return tree.markdown(payload.prefix[:-1], discord=True, escape=escape) + " " if payload.prefix else ""
