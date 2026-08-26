"""Mechanical drawing of resolved Discord Components V2 scenes."""

from collections.abc import Callable
from datetime import datetime
from typing import Any, override
from urllib.parse import urlsplit

import discord

from squid_discord.adapter import DISCORD_PY_27_ADAPTER, require_discord_py_capability
from squid_discord.attachments import attachment_assets
from squid_discord.conformance import LimitViolationError, conform
from squid_discord.emoji import discord_emoji
from squid_discord.presentation import DiscordPresentation
from squid_discord.target import DISCORD_V2_DPY27
from squid_layouts import scene
from squid_layouts.assets import Asset, StoredAsset
from squid_layouts.errors import DrawInvariantError
from squid_layouts.interactions import ActionBinding
from squid_layouts.planning.adapter import AdapterCapability, AdapterProfile
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.scene.model import PlanResult
from squid_layouts.target_types import DiscordPyAdapter
from squid_layouts.temporal import ZonedDateTime
from squid_layouts.text import discord_text

type Control = scene.Button | scene.Select | scene.EntitySelect
type Wire = Callable[[Control, ActionBinding], discord.ui.Item[Any]]
type ViewFactory = Callable[[], discord.ui.LayoutView]


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


class V2Renderer:
    """Draw a resolved Components V2 scene without making layout decisions."""

    def __init__(
        self,
        *,
        limits: V2Limits = LIMITS,
        audit: bool = True,
        view_factory: ViewFactory = StaticView,
        adapter: AdapterProfile[DiscordPyAdapter] = DISCORD_PY_27_ADAPTER,
    ) -> None:
        require_discord_py_capability(adapter, AdapterCapability.RENDER_V2, "render Components V2")
        self.limits = limits
        self.audit = audit
        self.view_factory = view_factory

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

        def extension(node: scene.Extension) -> discord.ui.Item[Any]:
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
            node: scene.Thumbnail
            | scene.Link
            | scene.PremiumButton
            | scene.Button
            | scene.RoutedButton
            | scene.Extension,
        ) -> discord.ui.Item[Any]:
            match node:
                case scene.Thumbnail(url=url, description=description, spoiler=spoiler):
                    return discord.ui.Thumbnail(url, description=description, spoiler=spoiler)
                case scene.Link(label=label, url=url):
                    return discord.ui.Button(
                        style=discord.ButtonStyle.link,
                        label=label,
                        url=url,
                        emoji=discord_emoji(node.emoji),
                        disabled=node.disabled,
                    )
                case scene.PremiumButton(sku_id=sku_id):
                    return discord.ui.Button(sku_id=sku_id)
                case scene.RoutedButton(label=label, route_id=route_id):
                    # No binding to wire, so this draws in a sessionless document too. Not a
                    # DynamicItem: discord.py's dynamic dispatch finds the base item by custom
                    # id, so nothing outgoing has to be one.
                    return RoutedItem(
                        style=getattr(discord.ButtonStyle, node.style.value),
                        label=label,
                        custom_id=route_id,
                        emoji=discord_emoji(node.emoji),
                        disabled=node.disabled,
                    )
                case scene.Button():
                    return control(node)
                case scene.Extension():
                    return extension(node)

        def item(node: scene.Node) -> discord.ui.Item[Any]:
            match node:
                case scene.Text() as text:
                    return discord.ui.TextDisplay(discord_text(text))
                case scene.Time(instant=instant, style=style, prefix=prefix):
                    unix = int(datetime.fromisoformat(instant).timestamp())
                    return discord.ui.TextDisplay(f"{prefix or ''}<t:{unix}:{style}>")
                case scene.ZonedTime(instant=instant, timezone=timezone, prefix=prefix):
                    value = ZonedDateTime(datetime.fromisoformat(instant), timezone)
                    return discord.ui.TextDisplay(f"{prefix or ''}{value.isoformat()}")
                case scene.File(asset_key=asset_key, name=name, spoiler=spoiler):
                    resource = plan.resources.get(f"asset:{asset_key}") if plan is not None else None
                    if isinstance(resource, Asset) and isinstance(resource.source, StoredAsset):
                        parsed = urlsplit(resource.source.reference)
                        if parsed.scheme in {"http", "https"} and parsed.netloc:
                            if spoiler:
                                message = "stored-file link conversion cannot preserve spoiler metadata"
                                raise DrawInvariantError(message)
                            return discord.ui.Button(
                                style=discord.ButtonStyle.link,
                                label=name,
                                url=resource.source.reference,
                            )
                    return discord.ui.File(f"attachment://{name}", spoiler=spoiler)
                case scene.Panel(children=children, accent=accent, spoiler=spoiler):
                    return discord.ui.Container(
                        *(item(child) for child in children), accent_colour=accent, spoiler=spoiler
                    )
                case scene.Section(texts=texts, accessory=side):
                    return discord.ui.Section(
                        *(discord.ui.TextDisplay(discord_text(text)) for text in texts),
                        accessory=accessory(side),
                    )
                case scene.Separator(large=large, visible=visible):
                    spacing = discord.SeparatorSpacing.large if large else discord.SeparatorSpacing.small
                    return discord.ui.Separator(visible=visible, spacing=spacing)
                case scene.Row(items=items):
                    return discord.ui.ActionRow(*(accessory(entry) for entry in items))
                case scene.Select():
                    return discord.ui.ActionRow(control(node))
                case scene.EntitySelect():
                    return discord.ui.ActionRow(control(node))
                case scene.RoutedSelect(
                    options=options,
                    route_id=route_id,
                    placeholder=placeholder,
                    min_values=minimum,
                    max_values=maximum,
                    disabled=disabled,
                ):
                    select = RoutedSelectItem(
                        options=[
                            discord.SelectOption(
                                label=option.label,
                                value=option.value,
                                description=option.description,
                                default=option.default,
                                emoji=discord_emoji(option.emoji),
                            )
                            for option in options
                        ],
                        custom_id=route_id,
                        placeholder=placeholder,
                        min_values=minimum,
                        max_values=maximum,
                        disabled=disabled,
                    )
                    return discord.ui.ActionRow(select)
                case scene.Gallery(items=items):
                    return discord.ui.MediaGallery(
                        *(
                            discord.MediaGalleryItem(entry.url, description=entry.description, spoiler=entry.spoiler)
                            for entry in items
                        )
                    )
                case scene.Thumbnail() | scene.Link() | scene.PremiumButton() | scene.Button() | scene.RoutedButton():
                    return accessory(node)
                case scene.Extension():
                    return extension(node)

        for child in document.components_v2.children:
            view.add_item(item(child))

        if self.audit:
            try:
                conform(view, strict=True, limits=self.limits)
            except LimitViolationError as error:
                message = "Discord drawing violated its planned limits: " + "; ".join(error.interventions)
                raise DrawInvariantError(message) from error
        return view
