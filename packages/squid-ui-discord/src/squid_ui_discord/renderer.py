"""Mechanical drawing of resolved Discord Components V2 scenes."""

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast, override
from urllib.parse import urlsplit

import discord

from squid_ui_discord.adapter import DISCORD_PY_27_ADAPTER, require_discord_py_capability
from squid_ui_discord.attachments import attachment_assets
from squid_ui_discord.conformance import LimitViolationError, conform
from squid_ui_discord.emoji import discord_emoji
from squid_ui_discord.presentation import DiscordPresentation
from squid_ui_discord.render_cache import RenderProgramCache
from squid_ui_discord.target import DISCORD_V2_DPY27
from squid_ui import scene
from squid_ui.assets import Asset, StoredAsset
from squid_ui.errors import DrawInvariantError
from squid_ui.interactions import ActionBinding
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.limits import LIMITS, V2Limits
from squid_ui.scene.model import PlanResult
from squid_ui.target_types import DiscordPyAdapter
from squid_ui.temporal import ZonedDateTime
from squid_ui.text import discord_text

type Control = scene.Button | scene.Select | scene.EntitySelect
type Wire = Callable[[Control, ActionBinding], discord.ui.Item[Any]]
type ViewFactory = Callable[[], discord.ui.LayoutView]


class _V2Op(StrEnum):
    TEXT = "text"
    FILE = "file"
    PANEL = "panel"
    SECTION = "section"
    SEPARATOR = "separator"
    ROW = "row"
    CONTROL_ROW = "control_row"
    ROUTED_SELECT = "routed_select"
    GALLERY = "gallery"
    THUMBNAIL = "thumbnail"
    LINK = "link"
    PREMIUM = "premium"
    CONTROL = "control"
    ROUTED_BUTTON = "routed_button"
    EXTENSION = "extension"


@dataclass(frozen=True, slots=True)
class _V2Instruction:
    op: _V2Op
    source: scene.Node | None = None
    text: str | None = None
    texts: tuple[str, ...] = ()
    children: tuple[_V2Instruction, ...] = ()


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


def _compile_accessory(node: scene.Node) -> _V2Instruction:
    match node:
        case scene.Thumbnail():
            return _V2Instruction(_V2Op.THUMBNAIL, source=node)
        case scene.Link():
            return _V2Instruction(_V2Op.LINK, source=node)
        case scene.PremiumButton():
            return _V2Instruction(_V2Op.PREMIUM, source=node)
        case scene.RoutedButton():
            return _V2Instruction(_V2Op.ROUTED_BUTTON, source=node)
        case scene.Button():
            return _V2Instruction(_V2Op.CONTROL, source=node)
        case scene.Extension():
            return _V2Instruction(_V2Op.EXTENSION, source=node)
    message = f"unsupported Components V2 accessory {type(node).__name__}"
    raise DrawInvariantError(message)


def _compile_item(node: scene.Node) -> _V2Instruction:
    match node:
        case scene.Text() as text:
            return _V2Instruction(_V2Op.TEXT, text=discord_text(text))
        case scene.Time(instant=instant, style=style, prefix=prefix):
            unix = int(datetime.fromisoformat(instant).timestamp())
            return _V2Instruction(_V2Op.TEXT, text=f"{prefix or ''}<t:{unix}:{style}>")
        case scene.ZonedTime(instant=instant, timezone=timezone, prefix=prefix):
            value = ZonedDateTime(datetime.fromisoformat(instant), timezone)
            return _V2Instruction(_V2Op.TEXT, text=f"{prefix or ''}{value.isoformat()}")
        case scene.File():
            return _V2Instruction(_V2Op.FILE, source=node)
        case scene.Panel(children=children):
            return _V2Instruction(_V2Op.PANEL, source=node, children=tuple(map(_compile_item, children)))
        case scene.Section(texts=texts, accessory=accessory):
            return _V2Instruction(
                _V2Op.SECTION,
                source=node,
                texts=tuple(discord_text(text) for text in texts),
                children=(_compile_accessory(accessory),),
            )
        case scene.Separator():
            return _V2Instruction(_V2Op.SEPARATOR, source=node)
        case scene.Row(items=items):
            return _V2Instruction(_V2Op.ROW, source=node, children=tuple(map(_compile_accessory, items)))
        case scene.Select() | scene.EntitySelect():
            return _V2Instruction(_V2Op.CONTROL_ROW, source=node)
        case scene.RoutedSelect():
            return _V2Instruction(_V2Op.ROUTED_SELECT, source=node)
        case scene.Gallery():
            return _V2Instruction(_V2Op.GALLERY, source=node)
        case scene.Thumbnail() | scene.Link() | scene.PremiumButton() | scene.Button() | scene.RoutedButton():
            return _compile_accessory(node)
        case scene.Extension():
            return _V2Instruction(_V2Op.EXTENSION, source=node)
    message = f"unsupported Components V2 scene node {type(node).__name__}"
    raise DrawInvariantError(message)


def _compile_program(document: scene.Document[scene.ComponentsV2]) -> _V2Program:
    children = tuple(_compile_item(child) for child in document.components_v2.children)

    def opaque(instruction: _V2Instruction) -> bool:
        return instruction.op is _V2Op.EXTENSION or any(map(opaque, instruction.children))

    return _V2Program(children, any(map(opaque, children)))


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
        document: scene.Document[scene.ComponentsV2],
        *,
        plan: PlanResult[scene.ComponentsV2] | None = None,
        wire: Wire | None = None,
    ) -> DiscordPresentation:
        """Draw the complete message this scene resolves to.

        A presentation rather than a view, because a message is both halves. For Components
        V2 the layout happens to be the whole message, so this looks like ceremony — it stops
        looking like it the moment the other renderer has content and embeds to return.
        """
        return DiscordPresentation.components_v2(
            self.view(document, plan=plan, wire=wire),
            assets=() if plan is None else attachment_assets(plan),
        )

    def view(
        self,
        document: scene.Document[scene.ComponentsV2],
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
        document: scene.Document[scene.ComponentsV2],
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
        source = instruction.source
        match instruction.op:
            case _V2Op.TEXT:
                return discord.ui.TextDisplay(cast(str, instruction.text))
            case _V2Op.FILE:
                node = cast(scene.File, source)
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
            case _V2Op.PANEL:
                node = cast(scene.Panel, source)
                return discord.ui.Container(
                    *(self._execute(child, plan=plan, wire=wire) for child in instruction.children),
                    accent_colour=node.accent,
                    spoiler=node.spoiler,
                )
            case _V2Op.SECTION:
                return discord.ui.Section(
                    *(discord.ui.TextDisplay(text) for text in instruction.texts),
                    accessory=self._execute(instruction.children[0], plan=plan, wire=wire),
                )
            case _V2Op.SEPARATOR:
                node = cast(scene.Separator, source)
                spacing = discord.SeparatorSpacing.large if node.large else discord.SeparatorSpacing.small
                return discord.ui.Separator(visible=node.visible, spacing=spacing)
            case _V2Op.ROW:
                return discord.ui.ActionRow(
                    *(self._execute(child, plan=plan, wire=wire) for child in instruction.children)
                )
            case _V2Op.CONTROL_ROW:
                return discord.ui.ActionRow(self._control(cast(Control, source), plan=plan, wire=wire))
            case _V2Op.ROUTED_SELECT:
                node = cast(scene.RoutedSelect, source)
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
            case _V2Op.GALLERY:
                node = cast(scene.Gallery, source)
                return discord.ui.MediaGallery(
                    *(
                        discord.MediaGalleryItem(entry.url, description=entry.description, spoiler=entry.spoiler)
                        for entry in node.items
                    )
                )
            case _V2Op.THUMBNAIL:
                node = cast(scene.Thumbnail, source)
                return discord.ui.Thumbnail(node.url, description=node.description, spoiler=node.spoiler)
            case _V2Op.LINK:
                node = cast(scene.Link, source)
                return discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label=node.label,
                    url=node.url,
                    emoji=discord_emoji(node.emoji),
                    disabled=node.disabled,
                )
            case _V2Op.PREMIUM:
                return discord.ui.Button(sku_id=cast(scene.PremiumButton, source).sku_id)
            case _V2Op.CONTROL:
                return self._control(cast(Control, source), plan=plan, wire=wire)
            case _V2Op.ROUTED_BUTTON:
                node = cast(scene.RoutedButton, source)
                return RoutedItem(
                    style=getattr(discord.ButtonStyle, node.style.value),
                    label=node.label,
                    custom_id=node.route_id,
                    emoji=discord_emoji(node.emoji),
                    disabled=node.disabled,
                )
            case _V2Op.EXTENSION:
                return self._extension(cast(scene.Extension, source), plan)
