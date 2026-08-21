"""Mechanical drawing of resolved Discord Components V2 scenes."""

from collections.abc import Callable
from typing import Any

import discord

from squid_layouts.actions import ActionBinding
from squid_layouts.discord.conform import LimitViolationError, conform
from squid_layouts.errors import DrawInvariantError
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.scene.codec import SceneCodec
from squid_layouts.scene.model import (
    PlanResult,
    SceneButton,
    SceneDocument,
    SceneExtension,
    SceneGallery,
    SceneLink,
    SceneNode,
    ScenePanel,
    SceneRoutedButton,
    SceneRow,
    SceneSection,
    SceneSelect,
    SceneSeparator,
    SceneText,
    SceneThumbnail,
)
from squid_layouts.text import discord_text

type Control = SceneButton | SceneSelect
type Wire = Callable[[Control, ActionBinding], discord.ui.Item[Any]]
type ViewFactory = Callable[[], discord.ui.LayoutView]


class StaticView(discord.ui.LayoutView):
    """A rendered view with no interaction session or timeout."""

    def __init__(self) -> None:
        super().__init__(timeout=None)


class Renderer:
    """Draw a Discord-targeted scene without making layout decisions."""

    def __init__(
        self,
        *,
        limits: V2Limits = LIMITS,
        audit: bool = True,
        view_factory: ViewFactory = StaticView,
    ) -> None:
        self.limits = limits
        self.audit = audit
        self.view_factory = view_factory

    def draw(
        self,
        scene: SceneDocument,
        *,
        plan: PlanResult | None = None,
        wire: Wire | None = None,
    ) -> discord.ui.LayoutView:
        if scene.protocol != SceneCodec.protocol:
            message = f"Renderer cannot draw scene protocol {scene.protocol}"
            raise DrawInvariantError(message)
        if scene.target != "discord.components-v2":
            message = f"Renderer cannot draw target {scene.target!r}"
            raise DrawInvariantError(message)
        if scene.target_version != 1:
            message = f"Renderer cannot draw Discord target version {scene.target_version}"
            raise DrawInvariantError(message)

        view = self.view_factory()

        def control(node: Control) -> discord.ui.Item[Any]:
            if plan is None or wire is None:
                message = "interactive scene controls require a mounted Discord frontend"
                raise TypeError(message)
            binding = plan.bindings.get(node.action)
            if binding is None:
                message = f"scene action {node.action!r} has no binding"
                raise DrawInvariantError(message)
            return wire(node, binding)

        def extension(node: SceneExtension) -> discord.ui.Item[Any]:
            if plan is None:
                message = f"unsupported Discord scene extension {node.kind!r}"
                raise DrawInvariantError(message)
            key = node.payload.get("resource")
            item = plan.resources.get(key) if isinstance(key, str) else None
            if not isinstance(item, discord.ui.Item):
                message = f"Discord extension resource {key!r} is not an Item"
                raise DrawInvariantError(message)
            return item

        def accessory(
            node: SceneThumbnail | SceneLink | SceneButton | SceneRoutedButton | SceneExtension,
        ) -> discord.ui.Item[Any]:
            match node:
                case SceneThumbnail(url=url, description=description):
                    return discord.ui.Thumbnail(url, description=description)
                case SceneLink(label=label, url=url):
                    return discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=url)
                case SceneRoutedButton(label=label, custom_id=custom_id):
                    # No binding to wire, so this draws in a sessionless document too. A plain
                    # button is deliberate: discord.py's dynamic dispatch finds the base item by
                    # custom id, so nothing here has to be a DynamicItem.
                    return discord.ui.Button(
                        style=getattr(discord.ButtonStyle, node.style.value),
                        label=label,
                        custom_id=custom_id,
                        emoji=node.emoji,
                        disabled=node.disabled,
                    )
                case SceneButton():
                    return control(node)
                case SceneExtension():
                    return extension(node)

        def item(node: SceneNode) -> discord.ui.Item[Any]:
            match node:
                case SceneText() as text:
                    return discord.ui.TextDisplay(discord_text(text))
                case ScenePanel(children=children, accent=accent):
                    return discord.ui.Container(*(item(child) for child in children), accent_colour=accent)
                case SceneSection(texts=texts, accessory=side):
                    return discord.ui.Section(
                        *(discord.ui.TextDisplay(discord_text(text)) for text in texts),
                        accessory=accessory(side),
                    )
                case SceneSeparator(large=large, visible=visible):
                    spacing = discord.SeparatorSpacing.large if large else discord.SeparatorSpacing.small
                    return discord.ui.Separator(visible=visible, spacing=spacing)
                case SceneRow(items=items):
                    return discord.ui.ActionRow(*(accessory(entry) for entry in items))
                case SceneSelect():
                    return discord.ui.ActionRow(control(node))
                case SceneGallery(items=items):
                    return discord.ui.MediaGallery(
                        *(discord.MediaGalleryItem(entry.url, description=entry.description) for entry in items)
                    )
                case SceneThumbnail() | SceneLink() | SceneButton() | SceneRoutedButton():
                    return accessory(node)
                case SceneExtension():
                    return extension(node)

        for child in scene.children:
            view.add_item(item(child))

        if self.audit:
            try:
                conform(view, strict=True, limits=self.limits)
            except LimitViolationError as error:
                message = "Discord drawing violated its planned limits: " + "; ".join(error.interventions)
                raise DrawInvariantError(message) from error
        return view
