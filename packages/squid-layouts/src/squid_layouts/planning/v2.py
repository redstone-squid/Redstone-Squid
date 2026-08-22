"""The Components V2 dialect: what makes a V2 message a V2 message, and nothing else."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC

from squid_layouts.chrome import Chrome
from squid_layouts.errors import LayoutInvariantError, UnsolvableLayoutError
from squid_layouts.planning.cursors import CursorCoordinator, MaterializedCursorRequest
from squid_layouts.planning.dialect import SceneBindings
from squid_layouts.planning.identity import stable_fingerprint
from squid_layouts.planning.limits import V2Limits
from squid_layouts.planning.measure import (
    MeasuredLayout,
    Realized,
    RPanel,
    RSection,
    RText,
    RTime,
    RZonedTime,
    measure,
)
from squid_layouts.planning.navigation import PlannedNav, materialized_navigation_state
from squid_layouts.planning.target import TargetProfile
from squid_layouts.primitives.constraints import Never, Paginate
from squid_layouts.primitives.nodes import (
    ActionGroup,
    Boundary,
    Break,
    Budget,
    Button,
    Extension,
    File,
    Footer,
    Gallery,
    LinkButton,
    MediaCollection,
    Node,
    Panel,
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
from squid_layouts.scene.model import (
    SceneButton,
    SceneExtension,
    SceneFile,
    SceneGallery,
    SceneGalleryItem,
    SceneLink,
    SceneNode,
    SceneOption,
    ScenePanel,
    SceneRoutedButton,
    SceneRoutedSelect,
    SceneRow,
    SceneSection,
    SceneSelect,
    SceneSeparator,
    SceneText,
    SceneThumbnail,
    SceneTime,
    SceneZonedTime,
)
from squid_layouts.sources import Position


@dataclass(slots=True)
class _V2Converter:
    bindings: SceneBindings

    def accessory(self, node: Thumbnail | LinkButton | Button | RoutedButton | RawItem, path: str) -> SceneNode:
        return self.bindings.control(node, path)

    def node(self, node: Realized, path: str) -> SceneNode:
        match node:
            case RText(content=content):
                return SceneText(content)
            case RTime(instant=instant, style=style, prefix=prefix):
                return SceneTime(instant.astimezone(UTC).isoformat(), style, prefix)
            case RZonedTime(value=value, prefix=prefix):
                return SceneZonedTime(value.instant.isoformat(), value.timezone, prefix)
            case File(asset_key=asset_key, name=name, media_type=media_type):
                return SceneFile(asset_key, name, media_type)
            case RPanel(children=children, accent=accent):
                return ScenePanel(
                    tuple(self.node(child, f"{path}.{index}") for index, child in enumerate(children)), accent
                )
            case RSection(texts=texts, accessory=accessory):
                return SceneSection(
                    tuple(SceneText(text.content) for text in texts),
                    self.accessory(accessory, f"{path}.accessory"),
                )
            case Sep(large=large, visible=visible):
                return SceneSeparator(large, visible)
            case Row(items=items):
                converted = tuple(self.accessory(item, f"{path}.{index}") for index, item in enumerate(items))
                if not all(
                    isinstance(item, SceneLink | SceneButton | SceneRoutedButton | SceneExtension) for item in converted
                ):
                    message = f"row {path} contains an unsupported item"
                    raise LayoutInvariantError(message)
                return SceneRow(converted)
            case SelectMenu(options=options):
                return SceneSelect(
                    options=tuple(
                        SceneOption(option.label, option.value, option.description, option.default)
                        for option in options
                    ),
                    action=self.bindings.action(node),
                    placeholder=node.placeholder,
                    min_values=node.min_values,
                    max_values=node.max_values,
                    disabled=node.disabled,
                    policy=node.policy,
                )
            case RoutedSelect(options=options):
                return SceneRoutedSelect(
                    options=tuple(
                        SceneOption(option.label, option.value, option.description, option.default)
                        for option in options
                    ),
                    route_id=node.route_id,
                    placeholder=node.placeholder,
                    min_values=node.min_values,
                    max_values=node.max_values,
                    disabled=node.disabled,
                )
            case Thumbnail(url=url, description=description):
                return SceneThumbnail(url, description)
            case Gallery(urls=urls):
                return SceneGallery(tuple(SceneGalleryItem(url) for url in urls))
            case RawItem():
                return self.accessory(node, path)
            case LinkButton() | RoutedButton():
                return self.accessory(node, path)

    def children(self, children: Sequence[Realized]) -> tuple[SceneNode, ...]:
        return tuple(self.node(child, str(index)) for index, child in enumerate(children))


def _lower(
    nodes: Sequence[Node],
    target: TargetProfile,
    limits: V2Limits,
) -> tuple[Node, ...]:
    lowered: list[Node] = []
    for node in nodes:
        match node:
            case ActionGroup(items=items):
                lowered.extend(
                    Row(tuple(items[start : start + limits.row_buttons]))
                    for start in range(0, len(items), limits.row_buttons)
                )
            case MediaCollection(urls=urls):
                lowered.extend(
                    Gallery(tuple(urls[start : start + limits.gallery_items]))
                    for start in range(0, len(urls), limits.gallery_items)
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
                component_cost = prepared.cost.get("components")
                text_cost = prepared.cost.get("display_text")
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
    pager_keys: set[str] = set()

    def fail(path: str, detail: str) -> None:
        message = f"{path}: {detail}"
        raise LayoutInvariantError(message)

    def walk(node: Node, path: str) -> None:
        if isinstance(node, Boundary):
            fail(path, "Boundary must be expanded by a component mount before planning")
        overflow = getattr(node, "overflow", None)
        if isinstance(overflow, Paginate):
            if overflow.key is None:
                fail(path, "Paginate requires an explicit key")
            if overflow.key in pager_keys:
                fail(path, f"duplicate pager key {overflow.key!r}")
            pager_keys.add(overflow.key)
        match node:
            case Button(label=label):
                if len(label) > limits.button_label:
                    fail(path, f"button label exceeds {limits.button_label}")
            case Row(items=items):
                if len(items) > limits.row_buttons:
                    fail(path, f"row has {len(items)} controls; maximum is {limits.row_buttons}")
                for index, item in enumerate(items):
                    if isinstance(item, Button):
                        walk(item, f"{path}.{index}")
            case SelectMenu(options=options, placeholder=placeholder, min_values=minimum, max_values=maximum) | (
                RoutedSelect(options=options, placeholder=placeholder, min_values=minimum, max_values=maximum)
            ):
                if not options:
                    fail(path, "select needs at least one option")
                if len(options) > limits.select_options:
                    remedy = (
                        "split the routed picker into separate routes"
                        if isinstance(node, RoutedSelect)
                        else "use an option-paging semantic node"
                    )
                    fail(path, f"select has {len(options)} options; {remedy}")
                if placeholder is not None and len(placeholder) > limits.select_placeholder:
                    fail(path, f"select placeholder exceeds {limits.select_placeholder}")
                if minimum < 0 or maximum < minimum or maximum > max(1, len(options)):
                    fail(path, "select value bounds are invalid")
                for index, option in enumerate(options):
                    if len(option.label) > limits.option_label:
                        fail(f"{path}.option.{index}", f"label exceeds {limits.option_label}")
                    if len(option.value) > limits.option_value:
                        fail(f"{path}.option.{index}", f"value exceeds {limits.option_value}")
                    if option.description is not None and len(option.description) > limits.option_description:
                        fail(f"{path}.option.{index}", f"description exceeds {limits.option_description}")
            case Gallery(urls=urls):
                if len(urls) > limits.gallery_items:
                    fail(path, f"gallery has {len(urls)} items; use MediaCollection")
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
            case Panel(children=children) | Budget(children=children) | Break(children=children):
                for index, child in enumerate(children):
                    walk(child, f"{path}.{index}")
            case Variants(variants=variants):
                # Every rung is checked, not just the one the search will open on, so a
                # document is rejected for a bad rung it might never reach. That also means
                # two rungs cannot share a Paginate key — as under the previous Fold, whose
                # primary and fallback were both walked.
                for index, variant in enumerate(variants):
                    for child_index, child in enumerate(variant.nodes):
                        walk(child, f"{path}.variant.{index}.{child_index}")
            case _:
                return

    for index, node in enumerate(nodes):
        walk(node, f"$.{index}")


def _paginate_v2(
    nodes: Sequence[Node],
    *,
    key: str,
    capacities: Mapping[str, int],
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

    def normalize(self, nodes: Sequence[Node], target: TargetProfile, limits: V2Limits) -> tuple[Node, ...]:
        return _lower(nodes, target, limits)

    def validate(self, nodes: Sequence[Node], limits: V2Limits) -> None:
        _validate_v2(nodes, limits)

    def paginate(
        self,
        nodes: Sequence[Node],
        *,
        key: str,
        capacities: Mapping[str, int],
        limits: V2Limits,
        chrome: Chrome,
        nav: PlannedNav,
        broker: CursorCoordinator,
    ) -> tuple[MeasuredLayout, int]:
        return _paginate_v2(nodes, key=key, capacities=capacities, limits=limits, chrome=chrome, nav=nav, broker=broker)

    def body(self, children: Sequence[Realized], bindings: SceneBindings) -> tuple[SceneNode, ...]:
        return _V2Converter(bindings).children(children)


V2_DIALECT = V2Dialect()
