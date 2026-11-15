"""Turn planned Components V2 scenes into discord.py message payloads.

Planning has already chosen the layout and fitted its content. This renderer compiles
the scene into drawing steps, executes them to build a fresh view, and audits the native
result. Local action controls ask the MessageRoot's wire callback to attach dispatch;
routed controls carry their route ID and are dispatched separately by a Router.
"""

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, override
from urllib.parse import urlsplit

import discord
from squid_ui import scene
from squid_ui.content.assets import Asset, StoredAsset
from squid_ui.content.temporal import ZonedDateTime
from squid_ui.content.text import discord_text
from squid_ui.errors import DrawInvariantError
from squid_ui.ext.discord.adapter import DISCORD_PY_27_ADAPTER, require_discord_py_capability
from squid_ui.ext.discord.conformance import LimitViolationError, conform
from squid_ui.ext.discord.limits import LIMITS, V2Limits
from squid_ui.ext.discord.messages.attachments import attachment_assets
from squid_ui.ext.discord.messages.payload import MessagePayload
from squid_ui.ext.discord.native_controls import Control as Control
from squid_ui.ext.discord.native_controls import RoutedItem as RoutedItem
from squid_ui.ext.discord.native_controls import RoutedSelectItem as RoutedSelectItem
from squid_ui.ext.discord.native_controls import Wire as Wire
from squid_ui.ext.discord.native_controls import (
    draw_control,
    draw_link,
    draw_routed_button,
    draw_routed_select,
)
from squid_ui.ext.discord.render_cache import RenderProgramCache
from squid_ui.ext.discord.target import DISCORD_V2_DPY27
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.result import PlanResult
from squid_ui.renderer import Renderer
from squid_ui.target_types import DiscordPyAdapter

type Accessory = (
    scene.Thumbnail | scene.Link | scene.PremiumButton | scene.Button | scene.RoutedButton | scene.Extension
)
"""What may sit beside a section or inside a row -- `scene.Section.accessory`'s own type."""
type ViewFactory = Callable[[], discord.ui.LayoutView]


class MountedRenderer[BodyT: scene.Body](Renderer[BodyT, MessagePayload], Protocol):
    """A message renderer that accepts a mount's control-wiring callback.

    wire builds SDK items bound to the current mount generation using the matching
    plan's bindings. Both classic and Components V2 renderers implement this contract.
    """

    @override
    def draw(
        self,
        document: scene.Scene[BodyT],
        *,
        plan: PlanResult[BodyT] | None = None,
        wire: Wire | None = None,
    ) -> MessagePayload: ...


@dataclass(frozen=True, slots=True)
class _V2Text:
    text: str


@dataclass(frozen=True, slots=True)
class _V2File:
    node: scene.File


@dataclass(frozen=True, slots=True)
class _V2Panel:
    node: scene.Panel
    children: tuple[_V2Instruction, ...]


@dataclass(frozen=True, slots=True)
class _V2Section:
    texts: tuple[str, ...]
    accessory: _V2Instruction


@dataclass(frozen=True, slots=True)
class _V2Separator:
    node: scene.Separator


@dataclass(frozen=True, slots=True)
class _V2Row:
    children: tuple[_V2Instruction, ...]


@dataclass(frozen=True, slots=True)
class _V2ControlRow:
    node: Control


@dataclass(frozen=True, slots=True)
class _V2RoutedSelect:
    node: scene.RoutedSelect


@dataclass(frozen=True, slots=True)
class _V2Gallery:
    node: scene.Gallery


@dataclass(frozen=True, slots=True)
class _V2Thumbnail:
    node: scene.Thumbnail


@dataclass(frozen=True, slots=True)
class _V2Link:
    node: scene.Link


@dataclass(frozen=True, slots=True)
class _V2Premium:
    node: scene.PremiumButton


@dataclass(frozen=True, slots=True)
class _V2Control:
    node: Control


@dataclass(frozen=True, slots=True)
class _V2RoutedButton:
    node: scene.RoutedButton


@dataclass(frozen=True, slots=True)
class _V2Extension:
    node: scene.Extension


type _V2Instruction = (
    _V2Text
    | _V2File
    | _V2Panel
    | _V2Section
    | _V2Separator
    | _V2Row
    | _V2ControlRow
    | _V2RoutedSelect
    | _V2Gallery
    | _V2Thumbnail
    | _V2Link
    | _V2Premium
    | _V2Control
    | _V2RoutedButton
    | _V2Extension
)
"""One drawing step, holding the exact scene node it draws.

A tag plus a widened `source: scene.Node` would make `_execute` re-assert a node type the
compiler already proved, once per arm. Each step carrying its own node means the match
narrows and the arms read what they were handed.
"""


@dataclass(frozen=True, slots=True)
class _V2Program:
    children: tuple[_V2Instruction, ...]
    opaque: bool = False


class StaticView(discord.ui.LayoutView):
    """A rendered view with no interaction session or timeout."""

    def __init__(self) -> None:
        super().__init__(timeout=None)


def _compile_accessory(node: Accessory) -> _V2Instruction:
    match node:
        case scene.Thumbnail():
            return _V2Thumbnail(node)
        case scene.Link():
            return _V2Link(node)
        case scene.PremiumButton():
            return _V2Premium(node)
        case scene.RoutedButton():
            return _V2RoutedButton(node)
        case scene.Button():
            return _V2Control(node)
        case scene.Extension():
            return _V2Extension(node)
    message = f"unsupported Components V2 accessory {type(node).__name__}"
    raise DrawInvariantError(message)


def _compile_item(node: scene.Node) -> _V2Instruction:
    match node:
        case scene.Text() as text:
            return _V2Text(discord_text(text))
        case scene.Time(instant=instant, style=style, prefix=prefix):
            unix = int(datetime.fromisoformat(instant).timestamp())
            return _V2Text(f"{prefix or ''}<t:{unix}:{style}>")
        case scene.ZonedTime(instant=instant, timezone=timezone, prefix=prefix):
            value = ZonedDateTime(datetime.fromisoformat(instant), timezone)
            return _V2Text(f"{prefix or ''}{value.isoformat()}")
        case scene.File():
            return _V2File(node)
        case scene.Panel(children=children):
            return _V2Panel(node, tuple(map(_compile_item, children)))
        case scene.Section(texts=texts, accessory=accessory):
            return _V2Section(
                tuple(discord_text(text) for text in texts),
                _compile_accessory(accessory),
            )
        case scene.Separator():
            return _V2Separator(node)
        case scene.Row(items=items):
            return _V2Row(tuple(map(_compile_accessory, items)))
        case scene.Select() | scene.EntitySelect():
            return _V2ControlRow(node)
        case scene.RoutedSelect():
            return _V2RoutedSelect(node)
        case scene.Gallery():
            return _V2Gallery(node)
        case scene.Thumbnail() | scene.Link() | scene.PremiumButton() | scene.Button() | scene.RoutedButton():
            return _compile_accessory(node)
        case scene.Extension():
            return _V2Extension(node)
    message = f"unsupported Components V2 scene node {type(node).__name__}"
    raise DrawInvariantError(message)


def _opaque(instruction: _V2Instruction) -> bool:
    """Return whether a native extension prevents reuse of the program's audit result."""
    match instruction:
        case _V2Extension():
            return True
        case _V2Panel(children=children) | _V2Row(children=children):
            return any(map(_opaque, children))
        case _V2Section(accessory=accessory):
            return _opaque(accessory)
        case _:
            return False


def _compile_program(document: scene.Scene[scene.ComponentsV2]) -> _V2Program:
    children = tuple(_compile_item(child) for child in document.components_v2.children)
    return _V2Program(children, any(map(_opaque, children)))


class V2Renderer:
    """Draw a resolved Components V2 scene without making layout decisions.

    The optional plan must belong to the supplied scene: its fingerprint selects the
    cached drawing program, and its resources supply extensions and attachments.
    Mounted controls require both plan and wire; routed controls need neither.
    Cached programs construct new views and controls on each draw.
    """

    def __init__(
        self,
        *,
        limits: V2Limits = LIMITS,
        audit: bool = True,
        view_factory: ViewFactory = StaticView,
        adapter: AdapterProfile[DiscordPyAdapter] = DISCORD_PY_27_ADAPTER,
        cache: RenderProgramCache | None = None,
    ) -> None:
        require_discord_py_capability(adapter, AdapterCapability.RENDER_V2, "render Components V2")
        self.limits = limits
        self.audit = audit
        self.view_factory = view_factory
        self.cache = cache if cache is not None else RenderProgramCache()

    def draw(
        self,
        document: scene.Scene[scene.ComponentsV2],
        *,
        plan: PlanResult[scene.ComponentsV2] | None = None,
        wire: Wire | None = None,
    ) -> MessagePayload:
        """Draw a view and include upload assets from the matching plan, when supplied."""
        return MessagePayload.components_v2(
            self.view(document, plan=plan, wire=wire),
            # `attachment_assets` is body-agnostic; BasedPyright infers `PlanResult`'s frozen
            # field as writable, hence invariant, and rejects the narrower plan.
            assets=() if plan is None else attachment_assets(plan),  # pyright: ignore[reportArgumentType]
        )

    def view(
        self,
        document: scene.Scene[scene.ComponentsV2],
        *,
        plan: PlanResult[scene.ComponentsV2] | None = None,
        wire: Wire | None = None,
    ) -> discord.ui.LayoutView:
        """Build a fresh view, checking the scene protocol and target first.

        With audit enabled, inspect the result unless an unchanged static program already
        passed. Custom factories, mounted wiring, and native extensions are audited on
        every draw because their SDK objects can differ between calls.
        """
        if document.protocol != scene.Codec.protocol:
            message = f"V2Renderer cannot draw scene protocol {document.protocol}"
            raise DrawInvariantError(message)
        if document.target != DISCORD_V2_DPY27.id:
            message = f"V2Renderer cannot draw target {document.target!r}"
            raise DrawInvariantError(message)
        if document.target_version != 1:
            message = f"V2Renderer cannot draw Discord target version {document.target_version}"
            raise DrawInvariantError(message)
        key = self._cache_key(document, plan)
        cached = self.cache.get(key)
        if cached is None:
            program = _compile_program(document)
            certified = False
        else:
            stored, certified = cached
            if not isinstance(stored, _V2Program):
                message = "render program cache returned the wrong program type"
                raise DrawInvariantError(message)
            program = stored
        view = self.view_factory()
        for child in program.children:
            view.add_item(self._execute(child, plan=plan, wire=wire))

        certifiable = self.view_factory is StaticView and wire is None and not program.opaque
        if self.audit and (not certified or not certifiable):
            try:
                conform(view, strict=True, limits=self.limits)
            except LimitViolationError as error:
                message = "Discord drawing violated its planned limits: " + "; ".join(error.interventions)
                raise DrawInvariantError(message) from error
        self.cache.put(key, program, certified=certified or (self.audit and certifiable))
        return view

    def _cache_key(
        self,
        document: scene.Scene[scene.ComponentsV2],
        plan: PlanResult[scene.ComponentsV2] | None,
    ) -> Hashable:
        fingerprint = plan.report.scene_fingerprint if plan is not None else scene.Codec.fingerprint(document)
        factory = "static" if self.view_factory is StaticView else "custom"
        return "discord.components-v2", fingerprint, self.limits, factory

    @staticmethod
    def _extension(
        node: scene.Extension,
        plan: PlanResult[scene.ComponentsV2] | None,
    ) -> discord.ui.Item[Any]:
        if plan is None:
            message = f"unsupported Discord scene extension {node.kind!r}"
            raise DrawInvariantError(message)
        key = node.payload.get("resource")
        item = plan.resources.get(key) if isinstance(key, str) else None
        if not isinstance(item, discord.ui.Item):
            message = f"Discord extension resource {key!r} is not an Item"
            raise DrawInvariantError(message)
        return item

    def _execute(
        self,
        instruction: _V2Instruction,
        *,
        plan: PlanResult[scene.ComponentsV2] | None,
        wire: Wire | None,
    ) -> discord.ui.Item[Any]:
        match instruction:
            case _V2Text(text=text):
                return discord.ui.TextDisplay(text)
            case _V2File():
                return self._draw_file(instruction.node, plan=plan)
            case _V2Panel():
                return self._draw_panel(instruction, plan=plan, wire=wire)
            case _V2Section():
                return self._draw_section(instruction, plan=plan, wire=wire)
            case _V2Separator():
                return self._draw_separator(instruction.node)
            case _V2Row(children=children):
                return discord.ui.ActionRow(*(self._execute(child, plan=plan, wire=wire) for child in children))
            case _V2ControlRow(node=node):
                return discord.ui.ActionRow(draw_control(node, plan=plan, wire=wire))
            case _V2RoutedSelect():
                return discord.ui.ActionRow(draw_routed_select(instruction.node))
            case _V2Gallery():
                return self._draw_gallery(instruction.node)
            case _V2Thumbnail(node=node):
                return discord.ui.Thumbnail(node.url, description=node.description, spoiler=node.spoiler)
            case _V2Link():
                return draw_link(instruction.node)
            case _V2Premium(node=node):
                return discord.ui.Button(sku_id=node.sku_id)
            case _V2Control(node=node):
                return draw_control(node, plan=plan, wire=wire)
            case _V2RoutedButton():
                return draw_routed_button(instruction.node)
            case _V2Extension(node=node):
                return self._extension(node, plan)

    def _draw_file(self, node: scene.File, plan: PlanResult[scene.ComponentsV2] | None) -> discord.ui.Item[Any]:
        resource = plan.resources.get(f"asset:{node.asset_key}") if plan is not None else None
        if isinstance(resource, Asset) and isinstance(resource.source, StoredAsset):
            parsed = urlsplit(resource.source.reference)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                if node.spoiler:
                    message = "stored-file link conversion cannot preserve spoiler metadata"
                    raise DrawInvariantError(message)
                return discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label=node.name,
                    url=resource.source.reference,
                )
        return discord.ui.File(f"attachment://{node.name}", spoiler=node.spoiler)

    def _draw_panel(
        self, instruction: _V2Panel, plan: PlanResult[scene.ComponentsV2] | None, wire: Wire | None
    ) -> discord.ui.Item[Any]:
        return discord.ui.Container(
            *(self._execute(child, plan=plan, wire=wire) for child in instruction.children),
            accent_colour=instruction.node.accent,
            spoiler=instruction.node.spoiler,
        )

    def _draw_section(
        self, instruction: _V2Section, plan: PlanResult[scene.ComponentsV2] | None, wire: Wire | None
    ) -> discord.ui.Item[Any]:
        return discord.ui.Section(
            *(discord.ui.TextDisplay(text) for text in instruction.texts),
            accessory=self._execute(instruction.accessory, plan=plan, wire=wire),
        )

    def _draw_separator(self, node: scene.Separator) -> discord.ui.Item[Any]:
        spacing = discord.SeparatorSpacing.large if node.large else discord.SeparatorSpacing.small
        return discord.ui.Separator(visible=node.visible, spacing=spacing)

    def _draw_gallery(self, node: scene.Gallery) -> discord.ui.Item[Any]:
        return discord.ui.MediaGallery(
            *(
                discord.MediaGalleryItem(
                    f"attachment://{entry.asset_name}" if entry.asset_name is not None else entry.url,
                    description=entry.description,
                    spoiler=entry.spoiler,
                )
                for entry in node.items
            )
        )
