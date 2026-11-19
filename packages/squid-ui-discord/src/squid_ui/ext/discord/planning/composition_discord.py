"""Discord formatting and control constraints shared by both message surfaces."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from squid_i18n import tree
from squid_ui.content.entity import ConversationType
from squid_ui.ext.discord import scene
from squid_ui.ext.discord.limits import ComponentLimits, V2Limits
from squid_ui.ext.discord.planning.realizations import GalleryRealization, ThumbnailRealization
from squid_ui.native import NativeValue
from squid_ui.planning import controls
from squid_ui.planning.composition import (
    CandidateViolation,
    ConstraintViolation,
    LocalCapacityExceeded,
    OccurrenceId,
    OfferDiscovery,
    PlanningContext,
)
from squid_ui.planning.offers.policy import Offers
from squid_ui.planning.realizations import (
    ControlRealization,
    EntityRealization,
    ImageRealization,
    SelectRealization,
    TextRealization,
    TimeRealization,
)


def select_violations(
    node: scene.Select
    | scene.RoutedSelect
    | scene.EntitySelect
    | controls.Select
    | controls.RoutedSelect
    | controls.EntitySelect,
    component: ComponentLimits,
    occurrence: OccurrenceId,
    *,
    paged: bool = False,
) -> tuple[CandidateViolation, ...]:
    errors: list[CandidateViolation] = []

    def check(identity: OccurrenceId, name: str, used: int, capacity: int) -> None:
        if used > capacity:
            errors.append(LocalCapacityExceeded(identity, name, used, capacity))

    check(occurrence, "select.placeholder", len(node.placeholder or ""), component.select_placeholder)
    identifier = node.route_id if isinstance(node, scene.RoutedSelect | controls.RoutedSelect) else node.action
    check(occurrence, "select.id", len(identifier), component.custom_id)
    if isinstance(node, scene.EntitySelect | controls.EntitySelect):
        check(occurrence, "select.max_values", node.max_values, component.select_options)
        if not 0 <= node.min_values <= node.max_values or node.max_values < 1:
            errors.append(ConstraintViolation(occurrence, "entity select cardinality"))
        if len(node.default_values) > node.max_values:
            errors.append(ConstraintViolation(occurrence, "entity select default count"))
        if any(not node.entity_type.supports(value.kind) for value in node.default_values):
            errors.append(ConstraintViolation(occurrence, "entity select default kind"))
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
            errors.append(ConstraintViolation(occurrence, "Discord conversation family"))
        return tuple(errors)
    if not paged or component.select_options == 0:
        check(occurrence, "select.options", len(node.options), component.select_options)
    if not node.options:
        errors.append(ConstraintViolation(occurrence, "select needs an option"))
    if not 0 <= node.min_values <= node.max_values <= len(node.options):
        errors.append(ConstraintViolation(occurrence, "select cardinality"))
    for index, option in enumerate(node.options):
        identity = OccurrenceId((*occurrence.path, "@option", str(index)))
        if not option.label or not option.value:
            errors.append(ConstraintViolation(identity, "select option needs a label and a value"))
        check(identity, "select.option.label", len(option.label), component.option_label)
        check(identity, "select.option.value", len(option.value), component.option_value)
        check(identity, "select.option.description", len(option.description or ""), component.option_description)
    return tuple(errors)


def button_violations(
    control: ControlRealization, component: ComponentLimits, occurrence: OccurrenceId
) -> tuple[CandidateViolation, ...]:
    errors: list[CandidateViolation] = [
        LocalCapacityExceeded(occurrence, name, used, capacity)
        for name, used, capacity in (
            ("button.label", len(control.label), component.button_label),
            ("button.url", len(control.url or ""), component.link_url),
            ("button.id", len(control.action or control.route or ""), component.custom_id),
            ("row.buttons", 1, component.row_buttons),
        )
        if used > capacity
    ]
    if not control.label and control.emoji is None:
        errors.append(ConstraintViolation(occurrence, "button needs a label"))
    return tuple(errors)


def constrain_offers[R, C](
    offers: Offers[R, C], context: PlanningContext, component: ComponentLimits, *, v2: V2Limits | None = None
) -> Offers[R, C]:
    """Attach failures that cannot be repaired by placing the offer on a different page."""
    domain = offers.offers if isinstance(offers, OfferDiscovery) else offers
    checked = []
    for offer in domain:
        payload = offer.payload
        identity = offer.occurrence.identity
        errors: tuple[CandidateViolation, ...] = ()
        match payload:
            case ControlRealization() if offer.fragments is None:
                errors = button_violations(payload, component, identity)
            case EntityRealization(node=node) if offer.fragments is None:
                errors = select_violations(node, component, identity)
            case SelectRealization(node=node):
                # Shared select offers preserve each option across all cuts.
                # A native extension can supply its own fragment constructor.
                occurrence = offer.occurrence
                custom = isinstance(occurrence.graph.units[occurrence.reference].value, NativeValue)
                paged = offer.region is not None and offer.region.max_units is not None
                if offer.fragments is None or (paged and not custom) or component.select_options == 0:
                    context.work.consume(len(node.options))
                    errors = select_violations(node, component, identity, paged=paged)
            case ImageRealization() | ThumbnailRealization() | GalleryRealization() if v2 is not None and (
                offer.fragments is None
                or (isinstance(payload, GalleryRealization) and offer.fragments.payload == payload.fragment)
            ):
                if isinstance(payload, GalleryRealization):
                    descriptions = tuple(item.description for item in payload.items)
                elif isinstance(payload, ThumbnailRealization):
                    descriptions = (payload.node.description,)
                else:
                    descriptions = (payload.description,)
                context.work.consume(len(descriptions))
                errors = tuple(
                    LocalCapacityExceeded(identity, "media.description", len(description), v2.gallery_item_description)
                    for description in descriptions
                    if description is not None and len(description) > v2.gallery_item_description
                )
        checked.append(replace(offer, rejections=(*offer.rejections, *errors)) if errors else offer)
    return replace(offers, offers=tuple(checked)) if isinstance(offers, OfferDiscovery) else tuple(checked)


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


def resolve_select(
    node: controls.Select | controls.RoutedSelect | controls.EntitySelect,
) -> scene.Select | scene.RoutedSelect | scene.EntitySelect:
    """Draw a portable resolved choice using Discord's scene vocabulary."""
    if isinstance(node, controls.EntitySelect):
        return scene.EntitySelect(
            node.entity_type,
            node.action,
            node.placeholder,
            node.default_values,
            node.conversation_types,
            node.min_values,
            node.max_values,
            node.disabled,
            node.mode,
        )
    options = tuple(
        scene.Option(item.label, item.value, item.description, item.default, item.emoji) for item in node.options
    )
    if isinstance(node, controls.RoutedSelect):
        return scene.RoutedSelect(
            options, node.route_id, node.placeholder, node.min_values, node.max_values, node.disabled
        )
    return scene.Select(
        options, node.action, node.placeholder, node.min_values, node.max_values, node.disabled, node.mode
    )
