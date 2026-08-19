"""Plan logical documents into immutable target-resolved scenes."""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from squid_layouts.actions import ActionBinding
from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.document import DocumentLike, as_document
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.ir import (
    Button,
    Gallery,
    LinkButton,
    Node,
    Panel,
    RawItem,
    Row,
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
from squid_layouts.solve import Realized, RPanel, RSection, RText, SolvedLayout, solve
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
            case RawItem(factory=factory):
                resource = f"native:{path}"
                self.resources[resource] = factory()
                return SceneExtension("discord.raw", 0, {"resource": resource})

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


def plan(
    rendered: DocumentLike,
    *,
    target: TargetProfile,
    chrome: Chrome = DEFAULT_CHROME,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
    page: int | None = None,
    nav: Callable[[int, int], Sequence[Node]] | None = None,
) -> PlanResult:
    """Resolve a complete logical document for one target.

    Planning owns every fit and fallback decision. The resulting scene contains visual action
    references, while callbacks remain in the plan result for the mounted frontend.
    """
    document = as_document(rendered)
    limits = target.limits if isinstance(target.limits, V2Limits) else LIMITS
    solved = solve(
        document.children,
        limits=limits,
        chrome=chrome,
        strict=strict,
        reserved_text=reservation.get("display_text"),
        page=page,
        nav=nav,
    )
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
    pager = solved.pager
    if pager is None:
        return ()
    digest = hashlib.blake2s("\\0".join(pager.fragments).encode(), digest_size=16).hexdigest()
    return (ScenePager("page", solved.page, solved.pages, digest),)


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
