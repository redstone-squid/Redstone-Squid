"""The Components V2 dialect: what makes a V2 message a V2 message, and nothing else."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC
from typing import Any

from squid_ui import scene
from squid_ui.capabilities import Capability
from squid_ui.chrome import Chrome
from squid_ui.errors import LayoutInvariantError, UnsolvableLayoutError
from squid_ui.planning.control_validation import fail, register_pager, validate_component
from squid_ui.planning.cursors import CursorCoordinator, MaterializedCursorRequest
from squid_ui.planning.discord_dialect import SceneBindings
from squid_ui.planning.identity import stable_fingerprint
from squid_ui.planning.layout_measurement.model import (
    MeasuredPanel,
    MeasuredSection,
    MeasuredText,
    MeasuredTime,
    MeasuredZonedTime,
    Realized,
)
from squid_ui.planning.layout_measurement.solver import (
    MeasuredLayout,
    measure,
)
from squid_ui.planning.limits import LIMITS, Axis, V2Limits
from squid_ui.planning.navigation import PlannedNav, materialized_navigation_state
from squid_ui.planning.resolved import emoji as resolved_emoji
from squid_ui.planning.resolved import optional_text as resolved_optional_text
from squid_ui.planning.resolved import text as resolved_text
from squid_ui.planning.target import Target
from squid_ui.primitives.constraints import Never, Paginate
from squid_ui.primitives.nodes import (
    Break,
    Budget,
    Button,
    ControlGroup,
    EntitySelect,
    Extension,
    File,
    Footer,
    Gallery,
    GalleryItem,
    LinkButton,
    MediaCollection,
    Node,
    Panel,
    PremiumButton,
    RawItem,
    RoutedButton,
    RoutedSelect,
    Row,
    Section,
    SelectMenu,
    Sep,
    Thumbnail,
    Variant,
    Variants,
)
from squid_ui.sources import Position
from squid_ui.target_types import ComponentsV2Target


def _gallery_item(value: str | GalleryItem) -> GalleryItem:
    """Return the item normalized by Gallery construction."""
    if isinstance(value, str):
        message = "Gallery left a shorthand URL unnormalized"
        raise LayoutInvariantError(message)
    return value


@dataclass(slots=True)
class _V2Converter:
    bindings: SceneBindings

    def accessory(
        self, node: Thumbnail | LinkButton | PremiumButton | Button | RoutedButton | RawItem, path: str
    ) -> scene.Thumbnail | scene.Link | scene.PremiumButton | scene.Button | scene.RoutedButton | scene.Extension:
        converted = self.bindings.control(node, path)
        if not isinstance(
            converted,
            scene.Thumbnail | scene.Link | scene.PremiumButton | scene.Button | scene.RoutedButton | scene.Extension,
        ):
            message = f"accessory {path} converted to unsupported {type(converted).__name__}"
            raise LayoutInvariantError(message)
        return converted

    def row_item(
        self, node: Thumbnail | LinkButton | PremiumButton | Button | RoutedButton | RawItem, path: str
    ) -> scene.Link | scene.PremiumButton | scene.Button | scene.RoutedButton | scene.Extension:
        """Convert a control while excluding thumbnails from action rows."""
        converted = self.accessory(node, path)
        if isinstance(converted, scene.Thumbnail):
            message = f"row {path} contains a thumbnail"
            raise LayoutInvariantError(message)
        return converted

    def node(self, node: Realized, path: str) -> scene.Node:
        match node:
            case MeasuredText(content=content):
                return scene.Text(content)
            case MeasuredTime(instant=instant, style=style, prefix=prefix):
                return scene.Time(instant.astimezone(UTC).isoformat(), style, prefix)
            case MeasuredZonedTime(value=value, prefix=prefix):
                return scene.ZonedTime(value.instant.isoformat(), value.timezone, prefix)
            case File(asset_key=asset_key, name=name, media_type=media_type, spoiler=spoiler):
                return scene.File(asset_key, name, media_type, spoiler)
            case MeasuredPanel(children=children, accent=accent, spoiler=spoiler):
                return scene.Panel(
                    tuple(self.node(child, f"{path}.{index}") for index, child in enumerate(children)), accent, spoiler
                )
            case MeasuredSection(texts=texts, accessory=accessory):
                return scene.Section(
                    tuple(scene.Text(text.content) for text in texts),
                    self.accessory(accessory, f"{path}.accessory"),
                )
            case Sep(large=large, visible=visible):
                return scene.Separator(large, visible)
            case Row(items=items):
                converted = tuple(self.row_item(item, f"{path}.{index}") for index, item in enumerate(items))
                return scene.Row(converted)
            case SelectMenu(options=options):
                return scene.Select(
                    options=tuple(
                        scene.Option(
                            resolved_text(option.label),
                            option.value,
                            resolved_optional_text(option.description),
                            option.default,
                            resolved_emoji(option.emoji),
                        )
                        for option in options
                    ),
                    action=self.bindings.action(node),
                    placeholder=resolved_optional_text(node.placeholder),
                    min_values=node.min_values,
                    max_values=node.max_values,
                    disabled=node.disabled,
                    mode=node.mode,
                )
            case EntitySelect():
                return scene.EntitySelect(
                    entity_type=node.entity_type,
                    action=self.bindings.action(node),
                    placeholder=resolved_optional_text(node.placeholder),
                    default_values=node.default_values,
                    channel_types=node.channel_types,
                    min_values=node.min_values,
                    max_values=node.max_values,
                    disabled=node.disabled,
                    mode=node.mode,
                )
            case RoutedSelect(options=options):
                return scene.RoutedSelect(
                    options=tuple(
                        scene.Option(
                            resolved_text(option.label),
                            option.value,
                            resolved_optional_text(option.description),
                            option.default,
                            resolved_emoji(option.emoji),
                        )
                        for option in options
                    ),
                    route_id=node.route_id,
                    placeholder=resolved_optional_text(node.placeholder),
                    min_values=node.min_values,
                    max_values=node.max_values,
                    disabled=node.disabled,
                )
            case Thumbnail(url=url, description=description, spoiler=spoiler):
                return scene.Thumbnail(url, resolved_optional_text(description), spoiler)
            case Gallery(items=items):
                return scene.Gallery(
                    tuple(
                        scene.GalleryItem(
                            _gallery_item(item).url,
                            resolved_optional_text(_gallery_item(item).description),
                            _gallery_item(item).spoiler,
                        )
                        for item in items
                    )
                )
            case RawItem():
                return self.accessory(node, path)
            case LinkButton() | PremiumButton() | RoutedButton() | Button():
                return self.accessory(node, path)
        message = f"{path}: {type(node).__name__} cannot appear in a Components V2 scene"
        raise LayoutInvariantError(message)

    def children(self, children: Sequence[Realized]) -> tuple[scene.Node, ...]:
        return tuple(self.node(child, str(index)) for index, child in enumerate(children))


def _lower(
    nodes: Sequence[Node],
    target: Target,
    limits: V2Limits,
) -> tuple[Node, ...]:
    lowered: list[Node] = []
    for node in nodes:
        match node:
            case PremiumButton() if Capability.ACTIONS_DISCORD_PREMIUM not in target.capabilities:
                message = "premium buttons require an explicit Variants fallback on this target"
                raise LayoutInvariantError(message)
            case Row(items=items) | ControlGroup(items=items) if (
                Capability.ACTIONS_DISCORD_PREMIUM not in target.capabilities
                and any(isinstance(item, PremiumButton) for item in items)
            ):
                message = "premium buttons require an explicit Variants fallback on this target"
                raise LayoutInvariantError(message)
            case Section(accessory=PremiumButton()) if Capability.ACTIONS_DISCORD_PREMIUM not in target.capabilities:
                message = "premium buttons require an explicit Variants fallback on this target"
                raise LayoutInvariantError(message)
            case ControlGroup(items=items):
                lowered.extend(
                    Row(tuple(items[start : start + limits.components.row_buttons]))
                    for start in range(0, len(items), limits.components.row_buttons)
                )
            case MediaCollection(items=items):
                lowered.extend(
                    Gallery(tuple(items[start : start + limits.gallery_items]))
                    for start in range(0, len(items), limits.gallery_items)
                )
            case Panel(children=children, accent=accent):
                lowered.append(Panel(_lower(children, target, limits), accent))
            case Budget(children=children) | Break(children=children):
                lowered.append(replace(node, children=_lower(children, target, limits)))
            case Extension(kind=kind, version=version, payload=payload, fallback=fallback):
                adapter = target.extensions.get(kind)
                if adapter is None:
                    lowered.extend(_lower((fallback,), target, limits))
                    continue
                prepared = adapter.prepare(payload)
                component_cost = prepared.cost.get(Axis.COMPONENTS)
                text_cost = prepared.cost.get(Axis.DISPLAY_TEXT)
                if component_cost < 1 or text_cost < 0:
                    message = f"extension adapter {kind!r} returned an invalid resource cost"
                    raise LayoutInvariantError(message)
                resource = prepared.resource
                lowered.append(
                    RawItem(
                        factory=lambda resource=resource: resource,
                        text_cost=text_cost,
                        component_cost=component_cost,
                        kind=kind,
                        version=version,
                        payload=prepared.scene_payload,
                    )
                )
            case Variants(variants=variants, priority=priority):
                supported = [variant for variant in variants if variant.requires <= target.capabilities]
                if not supported:
                    message = "Variants has no variant supported by the selected target"
                    raise LayoutInvariantError(message)
                # `requires` is cleared rather than carried: capability selection happens here
                # and exactly once, leaving the search a pure budget ladder whose rung
                # numbering is stable for the rest of the plan.
                lowered.append(
                    Variants(
                        tuple(
                            Variant(_lower(variant.nodes, target, limits), fidelity=variant.fidelity)
                            for variant in supported
                        ),
                        priority,
                    )
                )
            case _:
                lowered.append(node)
    return tuple(lowered)


def _validate_v2(nodes: Sequence[Node], limits: V2Limits) -> None:
    """Reject what a Components V2 message cannot express, naming the node that cannot be drawn."""
    pager_keys: set[str] = set()

    def walk(node: Node, path: str) -> None:
        register_pager(node, path, pager_keys)
        match node:
            case PremiumButton(sku_id=sku_id):
                if sku_id <= 0:
                    fail(path, "premium button SKU must be positive")
            case Gallery(items=items):
                if len(items) > limits.gallery_items:
                    fail(path, f"gallery has {len(items)} items; use MediaCollection")
                for index, item in enumerate(items):
                    description = resolved_optional_text(_gallery_item(item).description)
                    if description is not None and len(description) > limits.gallery_item_description:
                        fail(
                            f"{path}.{index}",
                            f"media description exceeds {limits.gallery_item_description}",
                        )
            case Section(texts=texts):
                if len(texts) > limits.section_texts:
                    fail(path, f"section has {len(texts)} text slots; maximum is {limits.section_texts}")
                for index, text in enumerate(texts):
                    if isinstance(text.overflow, Paginate):
                        fail(
                            f"{path}.text.{index}",
                            "Paginate cannot be nested in a Section; place it beside the Section",
                        )
                    walk(text, f"{path}.text.{index}")
            case Panel(children=children):
                for index, child in enumerate(children):
                    walk(child, f"{path}.{index}")
            case _:
                validate_component(node, path, limits=limits, walk=walk)

    for index, node in enumerate(nodes):
        walk(node, f"$.{index}")


def _paginate_v2(
    nodes: Sequence[Node],
    *,
    key: str,
    capacities: Mapping[Axis, int],
    limits: V2Limits,
    chrome: Chrome,
    nav: PlannedNav,
    broker: CursorCoordinator,
) -> tuple[MeasuredLayout, int]:
    maximum_pages = max(1, len(nodes))

    def measure_page(children: Sequence[Node], index: int, pages: int) -> MeasuredLayout:
        chrome_nodes: tuple[Node, ...] = (
            Footer(chrome.page_footer(index + 1, pages), overflow=Never()),
            *nav(materialized_navigation_state(key, Position(offset=index), pages, chrome)),
        )
        return measure(
            (*children, *chrome_nodes),
            limits=limits,
            chrome=chrome,
            strict=False,
            nav=nav,
        )

    # Greedy: grow a page until it stops fitting, then cut. Every probe measures a
    # different prefix, so there is nothing to memoize — the cost is one measurement per
    # node, paid only by a document that already blew the component budget. These probes
    # are packing, not optimization, so they stay out of the search's evaluation count.
    pages: list[tuple[Node, ...]] = []
    current: tuple[Node, ...] = ()
    for node in nodes:
        candidate = (*current, node)
        probe = measure_page(candidate, 0, maximum_pages)
        if current and (not probe.fits(capacities) or probe.overflowed):
            pages.append(current)
            current = (node,)
            probe = measure_page(current, 0, maximum_pages)
        else:
            current = candidate
        if not probe.fits(capacities):
            blown = ", ".join(f"{axis} {spent}/{capacity}" for axis, spent, capacity in probe.cost.over(capacities))
            message = (
                f"root page {len(pages) + 1} cannot fit node {type(node).__name__} ({blown}); "
                "give that node a structural fallback or move it to another screen"
            )
            raise UnsolvableLayoutError(message)
    if current:
        pages.append(current)

    request = MaterializedCursorRequest(key=key, extent=len(pages), fingerprint=stable_fingerprint(nodes))
    grant = broker.grant(request)
    broker.record(request, grant.position)
    index = grant.position.offset
    return measure_page(pages[index], index, grant.extent), grant.extent


class V2Dialect:
    """Discord Components V2 shape. Everything else about planning is shared."""

    id = "discord.components-v2"
    version = 1
    capabilities = frozenset(
        {
            Capability.ACTIONS_BUTTONS,
            Capability.ACTIONS_DISCORD_PREMIUM,
            Capability.ACTIONS_SELECT,
            Capability.ACTIONS_DISCORD_ENTITY,
            Capability.FORMS_DISCORD_ENTITY,
            Capability.FORMS_DISCORD_FILE,
            Capability.FORMS_DISCORD_CHECKBOX_GROUP,
            Capability.FORMS_MODAL,
            Capability.LAYOUT_CONTAINER,
            Capability.LAYOUT_GALLERY,
            Capability.LAYOUT_SECTION,
        }
    )
    render_target = ComponentsV2Target
    body_type = scene.ComponentsV2
    default_limits = LIMITS
    realizes_extensions = True

    @property
    def planner(self) -> Any:
        from squid_ui.planning.discord_planner import DISCORD_PLANNER

        return DISCORD_PLANNER

    def normalize(
        self, nodes: Sequence[Node], target: Target[V2Limits, scene.ComponentsV2, ComponentsV2Target, Any]
    ) -> tuple[Node, ...]:
        return _lower(nodes, target, target.limits)

    def validate(self, nodes: Sequence[Node], limits: V2Limits) -> None:
        _validate_v2(nodes, limits)

    def paginate(
        self,
        nodes: Sequence[Node],
        *,
        key: str,
        capacities: Mapping[Axis, int],
        limits: V2Limits,
        chrome: Chrome,
        nav: PlannedNav,
        broker: CursorCoordinator,
    ) -> tuple[MeasuredLayout, int]:
        return _paginate_v2(nodes, key=key, capacities=capacities, limits=limits, chrome=chrome, nav=nav, broker=broker)

    def body(self, children: Sequence[Realized], bindings: SceneBindings) -> scene.ComponentsV2:
        return scene.ComponentsV2(_V2Converter(bindings).children(children))


V2_DIALECT = V2Dialect()
