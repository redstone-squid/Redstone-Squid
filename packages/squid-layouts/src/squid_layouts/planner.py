"""Plan logical documents into immutable target-resolved scenes."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from squid_layouts.actions import ActionBinding
from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.constraints import Paginate
from squid_layouts.document import DocumentLike, as_document
from squid_layouts.errors import LayoutDegradedError, LayoutInvariantError, UnsolvableLayoutError
from squid_layouts.ir import (
    ActionGroup,
    Button,
    Choice,
    Embed,
    Extension,
    Fold,
    Gallery,
    LinkButton,
    MediaCollection,
    Node,
    Panel,
    RawItem,
    Row,
    Section,
    SelectMenu,
    Sep,
    Thumbnail,
)
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.scene import (
    PlanEvent,
    PlanReport,
    PlanResult,
    PlanSeverity,
    SceneAsset,
    SceneButton,
    SceneDocument,
    SceneExtension,
    SceneGallery,
    SceneGalleryItem,
    SceneLink,
    SceneNode,
    SceneOption,
    ScenePager,
    ScenePanel,
    SceneRow,
    SceneSection,
    SceneSelect,
    SceneSeparator,
    SceneText,
    SceneThumbnail,
)
from squid_layouts.scene_codec import SceneCodec
from squid_layouts.solve import (
    LayoutOverflowError,
    PageNav,
    PageState,
    Realized,
    RPanel,
    RSection,
    RText,
    SolvedLayout,
    solve,
)
from squid_layouts.target import ResourceCost, TargetProfile

EMPTY_RESERVATION = ResourceCost()


@dataclass(slots=True)
class _Converter:
    bindings: dict[str, ActionBinding] = field(default_factory=dict)
    resources: dict[str, object] = field(default_factory=dict)

    def action(self, node: Button | SelectMenu) -> str:
        key = node.key
        if key in self.bindings:
            message = f"duplicate action key {key!r}"
            raise LayoutInvariantError(message)
        handler = node.on_click if isinstance(node, Button) else node.on_select
        self.bindings[key] = ActionBinding(key=key, handler=handler, policy=node.policy)
        return key

    def accessory(self, node: Thumbnail | LinkButton | Button | RawItem, path: str) -> SceneNode:
        match node:
            case Thumbnail(url=url, description=description):
                return SceneThumbnail(url, description)
            case LinkButton(label=label, url=url):
                return SceneLink(label, url)
            case Button():
                return SceneButton(
                    label=node.label,
                    action=self.action(node),
                    style=node.style,
                    emoji=node.emoji,
                    disabled=node.disabled,
                    policy=node.policy,
                )
            case RawItem(factory=factory, kind=kind, version=version, payload=payload):
                resource = f"native:{path}"
                self.resources[resource] = factory()
                return SceneExtension(kind, version, {**payload, "resource": resource})

    def node(self, node: Realized, path: str) -> SceneNode:
        match node:
            case RText(content=content):
                return SceneText(content)
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
                if not all(isinstance(item, SceneLink | SceneButton | SceneExtension) for item in converted):
                    message = f"row {path} contains an unsupported item"
                    raise LayoutInvariantError(message)
                return SceneRow(converted)
            case SelectMenu(options=options):
                return SceneSelect(
                    options=tuple(
                        SceneOption(option.label, option.value, option.description, option.default)
                        for option in options
                    ),
                    action=self.action(node),
                    placeholder=node.placeholder,
                    min_values=node.min_values,
                    max_values=node.max_values,
                    disabled=node.disabled,
                    policy=node.policy,
                )
            case Thumbnail(url=url, description=description):
                return SceneThumbnail(url, description)
            case Gallery(urls=urls):
                return SceneGallery(tuple(SceneGalleryItem(url) for url in urls))
            case RawItem():
                return self.accessory(node, path)
            case LinkButton():
                return self.accessory(node, path)

    def children(self, children: Sequence[Realized]) -> tuple[SceneNode, ...]:
        return tuple(self.node(child, str(index)) for index, child in enumerate(children))


def _lower_children(
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
                lowered.append(Panel(_lower_children(children, target, limits), accent))
            case Fold(primary=primary, fallback=fallback, priority=priority):
                lowered.append(
                    Fold(
                        _lower_single(primary, target, limits),
                        _lower_single(fallback, target, limits),
                        priority,
                    )
                )
            case Extension(kind=kind, version=version, payload=payload, fallback=fallback):
                adapter = target.extensions.get(kind)
                if adapter is None:
                    lowered.extend(_lower_children((fallback,), target, limits))
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
            case Choice(variants=variants, priority=priority):
                supported = [variant for variant in variants if variant.requires <= target.capabilities]
                if not supported:
                    message = "Choice has no variant supported by the selected target"
                    raise LayoutInvariantError(message)
                branch = _lower_single(supported[-1].node, target, limits)
                for variant in reversed(supported[:-1]):
                    branch = Fold(_lower_single(variant.node, target, limits), branch, priority)
                lowered.append(branch)
            case _:
                lowered.append(node)
    return tuple(lowered)


def _lower_single(node: Node, target: TargetProfile, limits: V2Limits) -> Node:
    lowered = _lower_children((node,), target, limits)
    if len(lowered) != 1:
        message = "a structural choice variant must lower to one node"
        raise LayoutInvariantError(message)
    return lowered[0]


def _validate(nodes: Sequence[Node], limits: V2Limits) -> None:
    pager_keys: set[str] = set()

    def fail(path: str, detail: str) -> None:
        message = f"{path}: {detail}"
        raise LayoutInvariantError(message)

    def walk(node: Node, path: str) -> None:
        if isinstance(node, Embed):
            fail(path, "Embed must be expanded by a component mount before planning")
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
            case SelectMenu(options=options, placeholder=placeholder, min_values=minimum, max_values=maximum):
                if len(options) > limits.select_options:
                    fail(path, f"select has {len(options)} options; use an option-paging semantic node")
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
            case Panel(children=children):
                for index, child in enumerate(children):
                    walk(child, f"{path}.{index}")
            case Fold(primary=primary, fallback=fallback):
                walk(primary, f"{path}.primary")
                walk(fallback, f"{path}.fallback")
            case _:
                return

    for index, node in enumerate(nodes):
        walk(node, f"$.{index}")


def plan(
    rendered: DocumentLike,
    *,
    target: TargetProfile,
    chrome: Chrome = DEFAULT_CHROME,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
    page: PageState = None,
    nav: PageNav | None = None,
) -> PlanResult:
    """Resolve a complete logical document for one target.

    Planning owns every fit and fallback decision. The resulting scene contains visual action
    references, while callbacks remain in the plan result for the mounted frontend.
    """
    document = as_document(rendered)
    limits = target.limits if isinstance(target.limits, V2Limits) else LIMITS
    lowered = _lower_children(document.children, target, limits)
    _validate(lowered, limits)
    try:
        solved = solve(
            lowered,
            limits=limits,
            chrome=chrome,
            strict=strict,
            reserved_text=reservation.get("display_text"),
            page=page,
            nav=nav,
        )
    except LayoutOverflowError as error:
        raise LayoutDegradedError(str(error)) from error
    if solved.components > limits.total_components:
        message = f"{solved.components} components exceed target maximum {limits.total_components}"
        raise UnsolvableLayoutError(message)
    if any(note.startswith("Never nodes need") for note in solved.notes):
        message = "; ".join(note for note in solved.notes if note.startswith("Never nodes need"))
        raise UnsolvableLayoutError(message)
    converter = _Converter()
    scene = SceneDocument(
        protocol=SceneCodec.protocol,
        target=target.id,
        target_version=target.version,
        children=converter.children(solved.children),
        assets=tuple(SceneAsset(asset.key, asset.name, asset.media_type) for asset in document.assets),
        pagers=_pagers(solved),
    )
    fingerprint = SceneCodec.fingerprint(scene)
    report = PlanReport(
        events=tuple(
            PlanEvent(
                code="layout.degraded",
                path="$",
                message=note,
                severity=PlanSeverity.DEGRADATION,
            )
            for note in solved.notes
        ),
        logical_fingerprint=_logical_fingerprint(document.children),
        scene_fingerprint=fingerprint,
    )
    return PlanResult(scene=scene, bindings=converter.bindings, report=report, resources=converter.resources)


def _pagers(solved: SolvedLayout) -> tuple[ScenePager, ...]:
    return tuple(
        ScenePager(
            pager.key,
            pager.page,
            pager.pages,
            hashlib.blake2s("\\0".join(pager.fragments).encode(), digest_size=16).hexdigest(),
        )
        for pager in solved.pagers
    )


def _logical_fingerprint(nodes: Sequence[Node]) -> str:
    """Hash visible structure without callback reprs or process-specific object addresses."""
    parts: list[str] = []

    def walk(node: Node) -> None:
        parts.append(type(node).__name__)
        match node:
            case Button(label=label, key=key):
                parts.extend((label, key))
            case SelectMenu(key=key, options=options):
                parts.append(key)
                parts.extend(option.value for option in options)
            case Row(items=items):
                for item in items:
                    walk(item)
            case Panel(children=children):
                for child in children:
                    walk(child)
            case _:
                parts.append(str(node))

    for node in nodes:
        walk(node)
    return hashlib.blake2s("\\0".join(parts).encode(), digest_size=16).hexdigest()
