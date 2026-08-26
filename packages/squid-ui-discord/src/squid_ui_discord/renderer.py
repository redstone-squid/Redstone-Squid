"""Mechanical drawing of resolved Discord Components V2 scenes."""

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, override
from urllib.parse import urlsplit

import discord

from squid_ui import scene
from squid_ui.assets import Asset, StoredAsset
from squid_ui.errors import DrawInvariantError
from squid_ui.interactions import ActionBinding
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.limits import LIMITS, V2Limits
from squid_ui.renderer import Renderer
from squid_ui.scene.model import PlanResult
from squid_ui.target_types import DiscordPyAdapter
from squid_ui.temporal import ZonedDateTime
from squid_ui.text import discord_text
from squid_ui_discord.adapter import DISCORD_PY_27_ADAPTER, require_discord_py_capability
from squid_ui_discord.attachments import attachment_assets
from squid_ui_discord.conformance import LimitViolationError, conform
from squid_ui_discord.emoji import discord_emoji
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.render_cache import RenderProgramCache
from squid_ui_discord.target import DISCORD_V2_DPY27

type Control = scene.Button | scene.Select | scene.EntitySelect
type Accessory = (
    scene.Thumbnail | scene.Link | scene.PremiumButton | scene.Button | scene.RoutedButton | scene.Extension
)
"""What may sit beside a section or inside a row -- `scene.Section.accessory`'s own type."""
type Wire = Callable[[Control, ActionBinding], discord.ui.Item[Any]]
type ViewFactory = Callable[[], discord.ui.LayoutView]


class MountedRenderer[BodyT: scene.Body](Renderer[BodyT, MessagePayload], Protocol):
    """A renderer a live `MessageRoot` can hand its dispatch to.

    `Renderer` is the mechanical contract: a scene in, a drawn thing out. A mount needs one
    more thing from it -- `wire`, which turns each interactive control into an item bound to
    that mount's generation -- and a mount picks its renderer by dialect id at runtime, so
    the two concrete renderers have to be reachable through one type. Without this, the call
    went through `cast(Any, renderer)` and nothing checked either half.
    """

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


class RoutedItem(discord.ui.Button[Any]):
    """A button whose only dispatch path is a `Router`.

    Discord's payload is an ordinary button; the override is purely about discord.py's
    in-process bookkeeping. `ViewStore.add_view` files an item into the stored dispatch
    table only when `is_dispatchable()` is true, and `dispatch_view` runs *both* the dynamic
    lookup and that table. A stored routed button would therefore take a second dispatch
    whose callback is `Item`'s no-op — harmless for responses, but `_scheduled_task` resets
    the view's timeout expiry before awaiting it, so clicking a routed control inside a
    mounted message silently extended that mount's life.

    Staying out of the table gives the control exactly one dispatch path. Dynamic dispatch
    is unaffected: `schedule_dynamic_item_call` rebuilds the view with
    `LayoutView.from_message` and finds the base item by component type and custom id there,
    on stock `Button` objects this class never touches.
    """

    @override
    def is_dispatchable(self) -> bool:
        return False


class RoutedSelectItem(discord.ui.Select[Any]):
    """A stateless select kept out of a surrounding mount's stored dispatch table."""

    @override
    def is_dispatchable(self) -> bool:
        return False


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
    """Whether drawing this step needs a plan resource, so its program cannot be certified."""
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
    """Draw a resolved Components V2 scene without making layout decisions."""

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
        """Draw the complete message this scene resolves to.

        A payload rather than a view, because a message is both halves. For Components
        V2 the layout happens to be the whole message, so this looks like ceremony — it stops
        looking like it the moment the other renderer has content and embeds to return.
        """
        return MessagePayload.components_v2(
            self.view(document, plan=plan, wire=wire),
            assets=() if plan is None else attachment_assets(plan),
        )

    def view(
        self,
        document: scene.Scene[scene.ComponentsV2],
        *,
        plan: PlanResult[scene.ComponentsV2] | None = None,
        wire: Wire | None = None,
    ) -> discord.ui.LayoutView:
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

    def _control(
        self,
        node: Control,
        *,
        plan: PlanResult[scene.ComponentsV2] | None,
        wire: Wire | None,
    ) -> discord.ui.Item[Any]:
        if plan is None or wire is None:
            message = "interactive scene controls require a mounted Discord frontend"
            raise TypeError(message)
        binding = plan.bindings.get(node.action)
        if binding is None:
            message = f"scene action {node.action!r} has no binding"
            raise DrawInvariantError(message)
        return wire(node, binding)

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
            case _V2File(node=node):
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
            case _V2Panel(node=node, children=children):
                return discord.ui.Container(
                    *(self._execute(child, plan=plan, wire=wire) for child in children),
                    accent_colour=node.accent,
                    spoiler=node.spoiler,
                )
            case _V2Section(texts=texts, accessory=accessory):
                return discord.ui.Section(
                    *(discord.ui.TextDisplay(text) for text in texts),
                    accessory=self._execute(accessory, plan=plan, wire=wire),
                )
            case _V2Separator(node=node):
                spacing = discord.SeparatorSpacing.large if node.large else discord.SeparatorSpacing.small
                return discord.ui.Separator(visible=node.visible, spacing=spacing)
            case _V2Row(children=children):
                return discord.ui.ActionRow(*(self._execute(child, plan=plan, wire=wire) for child in children))
            case _V2ControlRow(node=node):
                return discord.ui.ActionRow(self._control(node, plan=plan, wire=wire))
            case _V2RoutedSelect(node=node):
                select = RoutedSelectItem(
                    options=[
                        discord.SelectOption(
                            label=option.label,
                            value=option.value,
                            description=option.description,
                            default=option.default,
                            emoji=discord_emoji(option.emoji),
                        )
                        for option in node.options
                    ],
                    custom_id=node.route_id,
                    placeholder=node.placeholder,
                    min_values=node.min_values,
                    max_values=node.max_values,
                    disabled=node.disabled,
                )
                return discord.ui.ActionRow(select)
            case _V2Gallery(node=node):
                return discord.ui.MediaGallery(
                    *(
                        discord.MediaGalleryItem(entry.url, description=entry.description, spoiler=entry.spoiler)
                        for entry in node.items
                    )
                )
            case _V2Thumbnail(node=node):
                return discord.ui.Thumbnail(node.url, description=node.description, spoiler=node.spoiler)
            case _V2Link(node=node):
                return discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label=node.label,
                    url=node.url,
                    emoji=discord_emoji(node.emoji),
                    disabled=node.disabled,
                )
            case _V2Premium(node=node):
                return discord.ui.Button(sku_id=node.sku_id)
            case _V2Control(node=node):
                return self._control(node, plan=plan, wire=wire)
            case _V2RoutedButton(node=node):
                return RoutedItem(
                    style=getattr(discord.ButtonStyle, node.style.value),
                    label=node.label,
                    custom_id=node.route_id,
                    emoji=discord_emoji(node.emoji),
                    disabled=node.disabled,
                )
            case _V2Extension(node=node):
                return self._extension(node, plan)
