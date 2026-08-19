"""Plan logical documents into immutable target-resolved scenes."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum

from squid_layouts.actions import ActionBinding
from squid_layouts.cache import CachedPlan, PlanCache
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
from squid_layouts.presentation import PresentationSession
from squid_layouts.scene import (
    PlanEvent,
    PlanMetrics,
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
from squid_layouts.semantic_adapter import lower_semantics
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
        routes = node.routes if isinstance(node, SelectMenu) else {}
        for route_key, binding in routes.items():
            if route_key in self.bindings:
                message = f"duplicate action key {route_key!r}"
                raise LayoutInvariantError(message)
            self.bindings[route_key] = binding
        self.bindings[key] = ActionBinding(key=key, handler=handler, policy=node.policy, routes=routes)
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
    session: PresentationSession | None = None,
    cache: PlanCache | None = None,
) -> PlanResult:
    """Resolve a complete logical document for one target.

    Planning owns every fit and fallback decision. The resulting scene contains visual action
    references, while callbacks remain in the plan result for the mounted frontend.
    """
    document = as_document(rendered)
    limits = target.limits if isinstance(target.limits, V2Limits) else LIMITS
    presentation = session if session is not None else PresentationSession()
    semantic = lower_semantics(
        (*document.children, *document.assets),
        limits=limits,
        chrome=chrome,
        session=presentation,
        page=page,
        nav=nav,
    )
    lowered = _lower_children(semantic.nodes, target, limits)
    _validate(lowered, limits)
    cache_key = _plan_cache_key(
        document.children,
        target=target,
        limits=limits,
        chrome=chrome,
        presentation=presentation,
        reservation=reservation,
        strict=strict,
        nav=nav,
    )
    if cache is not None and _cacheable(lowered) and (cached := cache.get(cache_key)) is not None:
        converter = _collect_bindings(lowered)
        resources = {f"asset:{asset.key}": asset for asset in document.assets}
        return PlanResult(
            scene=cached.scene,
            bindings=converter.bindings,
            report=cached.report,
            resources=resources,
            metrics=PlanMetrics(states_explored=semantic.states_explored, cache_hit=True),
        )
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
        pagers=semantic.pagers + _pagers(solved),
    )
    fingerprint = SceneCodec.fingerprint(scene)
    report = PlanReport(
        events=semantic.events
        + tuple(
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
    resources = dict(converter.resources)
    resources.update({f"asset:{asset.key}": asset for asset in document.assets})
    result = PlanResult(
        scene=scene,
        bindings=converter.bindings,
        report=report,
        resources=resources,
        metrics=PlanMetrics(states_explored=semantic.states_explored),
    )
    if cache is not None and _cacheable(lowered):
        cache.put(cache_key, CachedPlan(scene, report))
    return result


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


def _logical_fingerprint(nodes: Sequence[object]) -> str:
    """Hash semantic structure without callback identity or process addresses."""
    payload = json.dumps(_stable_value(nodes), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2s(payload.encode(), digest_size=16).hexdigest()


def _stable_value(value: object) -> object:
    if callable(value):
        return "<callback>"
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__qualname__,
            "fields": {item.name: _stable_value(getattr(value, item.name)) for item in fields(value)},
        }
    if isinstance(value, Mapping):
        return {str(key): _stable_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_stable_value(item) for item in value]
    if isinstance(value, bytes):
        return hashlib.blake2s(value, digest_size=16).hexdigest()
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return type(value).__qualname__


def _plan_cache_key(
    nodes: Sequence[object],
    *,
    target: TargetProfile,
    limits: V2Limits,
    chrome: Chrome,
    presentation: PresentationSession,
    reservation: ResourceCost,
    strict: bool,
    nav: PageNav | None,
) -> str:
    relevant = {
        "document": _stable_value(nodes),
        "target": (target.id, target.version),
        "limits": _stable_value(limits),
        "presentation": _stable_value(presentation),
        "chrome": (
            chrome.previous,
            chrome.next,
            chrome.back,
            chrome.home,
            chrome.close,
            chrome.page_footer(1, 2),
            chrome.and_n_more(2),
        ),
        "reservation": _stable_value(reservation),
        "strict": strict,
        "nav": (
            None
            if nav is None
            else (
                getattr(nav, "__module__", ""),
                getattr(nav, "__qualname__", type(nav).__qualname__),
                getattr(nav, "version", 0),
            )
        ),
    }
    payload = json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2s(payload.encode(), digest_size=16).hexdigest()


def _cacheable(nodes: Sequence[Node]) -> bool:
    def check(node: Node) -> bool:
        if isinstance(node, Extension | RawItem | Fold | Choice):
            return False
        if isinstance(node, Panel):
            return all(check(child) for child in node.children)
        return True

    return all(check(node) for node in nodes)


def _collect_bindings(nodes: Sequence[Node]) -> _Converter:
    converter = _Converter()

    def collect(node: Node) -> None:
        match node:
            case Button() | SelectMenu():
                converter.action(node)
            case Row(items=items):
                for item in items:
                    if isinstance(item, Button):
                        converter.action(item)
            case Section(accessory=accessory):
                if isinstance(accessory, Button):
                    converter.action(accessory)
            case Panel(children=children):
                for child in children:
                    collect(child)
            case _:
                return

    for node in nodes:
        collect(node)
    return converter
