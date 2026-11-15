"""HTML preview drawing for Discord Components V2 scenes."""

import base64
import json
from collections.abc import Callable
from datetime import datetime

import htpy as h
from markupsafe import Markup as SafeHtml
from squid_ui_html.markup import markdown
from squid_ui_html.safety import safe_url as _safe_url

from squid_ui import scene
from squid_ui.assets import Asset, InlineAsset, LocalAsset, StoredAsset
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.result import PlanResult
from squid_ui.renderer import AssetResolver
from squid_ui.temporal import ZonedDateTime
from squid_ui.text import Markup
from squid_ui_discord import _preview_controls as controls
from squid_ui_discord._preview_styles import PREVIEW_CSS as PREVIEW_CSS

type FileResolver = Callable[[scene.File | scene.GalleryItem], str | None]


class DiscordPreviewRenderer:
    """Draw a Components V2 tree as HTML for inspection, without interaction dispatch.

    Discord Markdown includes subtext and keyboard-accessible text spoilers. CSS and
    non-None asset_resolver results are trusted host configuration: CSS is embedded
    verbatim and resolved file URLs are escaped but not scheme-filtered. Plan resources
    can supply inline downloads when no host URL is returned.
    """

    def __init__(
        self,
        *,
        standalone: bool = False,
        css: str = PREVIEW_CSS,
        asset_resolver: AssetResolver | None = None,
    ) -> None:
        self.standalone = standalone
        self.css = css
        self.asset_resolver = asset_resolver

    def draw(
        self,
        document: scene.Scene[scene.ComponentsV2],
        *,
        plan: PlanResult[scene.ComponentsV2] | None = None,
    ) -> str:
        if document.protocol != scene.Codec.protocol:
            message = f"DiscordPreviewRenderer cannot draw scene protocol {document.protocol}"
            raise DrawInvariantError(message)
        if document.target_version != 1:
            message = f"DiscordPreviewRenderer cannot preview target version {document.target_version}"
            raise DrawInvariantError(message)
        if not isinstance(document.body, scene.ComponentsV2):
            # Check the body shape: classic messages have content and embeds, not this tree.
            message = f"HTML preview cannot draw a {type(document.body).__name__} body"
            raise DrawInvariantError(message)
        pager_data = json.dumps(
            [{"key": pager.key, "page": pager.page, "pages": pager.pages} for pager in document.pagers],
            separators=(",", ":"),
        )
        assets = {asset.key: asset for asset in document.assets}

        def resolve_file(node: scene.File | scene.GalleryItem) -> str | None:
            metadata = (
                assets.get(node.asset_key)
                if isinstance(node, scene.File)
                else next((asset for asset in assets.values() if asset.name == node.asset_name), None)
            )
            if metadata is None:
                return None
            if self.asset_resolver is not None and (resolved := self.asset_resolver(metadata)) is not None:
                return resolved
            resource = plan.resources.get(f"asset:{metadata.key}") if plan is not None else None
            if not isinstance(resource, Asset):
                return None
            if isinstance(resource.source, InlineAsset | LocalAsset):
                encoded = base64.b64encode(resource.read()).decode("ascii")
                return f"data:{resource.media_type};base64,{encoded}"
            if isinstance(resource.source, StoredAsset):
                return _safe_url(resource.source.reference)
            return None

        root = h.div(
            class_="squid-view",
            data_squid_protocol=document.protocol,
            data_squid_target=document.target,
            data_squid_pagers=pager_data,
        )[tuple(self._node(child, resolve_file) for child in document.body.children)]
        if not self.standalone:
            return str(root.__html__())
        return str(h.html[h.head[h.meta(charset="utf-8"), h.style[SafeHtml(self.css)]], h.body[root]].__html__())

    def _node(
        self,
        node: scene.Node | scene.Link | scene.PremiumButton | scene.Button | scene.RoutedButton,
        resolve_file: FileResolver,
    ) -> h.Node:
        match node:
            case scene.Text():
                return self._text(node)
            case scene.Time():
                return self._time(node)
            case scene.ZonedTime():
                return self._zoned_time(node)
            case scene.File():
                return self._file(node, resolve_file=resolve_file)
            case scene.Separator():
                return self._separator(node)
            case scene.Panel():
                return self._panel(node, resolve_file=resolve_file)
            case scene.Section():
                return self._section(node, resolve_file=resolve_file)
            case scene.Row():
                return self._row(node, resolve_file=resolve_file)
            case scene.Button() | scene.RoutedButton() | scene.PremiumButton():
                return controls.button(node)
            case scene.Link():
                return controls.link(node)
            case scene.Select() | scene.RoutedSelect() | scene.EntitySelect():
                return controls.select(node)
            case scene.Thumbnail():
                return self._thumbnail(node)
            case scene.Gallery():
                return self._gallery(node, resolve_file=resolve_file)
            case scene.Extension():
                return self._extension(node)
        message = f"DiscordPreviewRenderer cannot draw scene node {type(node).__name__}"
        raise DrawInvariantError(message)

    def _text(self, node: scene.Text) -> h.Element:
        content = (
            node.content
            if node.markup is Markup.PLAIN
            else SafeHtml(markdown(node.content, block=True, discord=node.markup is Markup.DISCORD_MARKDOWN))
        )
        return h.div(class_="squid-text", data_squid_markup=node.markup.value)[content]

    def _time(self, node: scene.Time) -> h.Element:
        return h.div(class_="squid-text")[
            node.prefix or "",
            h.time(datetime=node.instant, data_squid_style=node.style)[node.instant],
        ]

    def _zoned_time(self, node: scene.ZonedTime) -> h.Element:
        value = ZonedDateTime(datetime.fromisoformat(node.instant), node.timezone)
        return h.div(class_="squid-text")[
            node.prefix or "",
            h.time(datetime=node.instant, data_squid_timezone=node.timezone)[value.isoformat()],
        ]

    def _file(self, node: scene.File, resolve_file: FileResolver) -> h.Element:
        resolved = resolve_file(node)
        spoiler_class = " squid-spoiler" if node.spoiler else ""
        if resolved is None:
            return h.span(
                class_=f"squid-button squid-file{spoiler_class}",
                aria_disabled="true",
                tabindex=0 if node.spoiler else None,
            )[node.name]
        return h.a(
            class_=f"squid-button squid-link squid-file{spoiler_class}",
            href=resolved,
            download=node.name,
            tabindex=0 if node.spoiler else None,
        )[node.name]

    def _separator(self, node: scene.Separator) -> h.VoidElement | None:
        if not node.visible:
            return None
        return h.hr(class_=["squid-separator", {"squid-separator--large": node.large}])

    def _panel(self, node: scene.Panel, resolve_file: FileResolver) -> h.Element:
        return h.section(
            class_=["squid-panel", {"squid-spoiler": node.spoiler}],
            tabindex=0 if node.spoiler else None,
            style=f"--squid-accent:#{node.accent:06x}" if node.accent is not None else None,
        )[tuple(self._node(child, resolve_file) for child in node.children)]

    def _section(self, node: scene.Section, resolve_file: FileResolver) -> h.Element:
        return h.section(class_="squid-section")[
            h.div[tuple(self._node(text, resolve_file) for text in node.texts)],
            self._node(node.accessory, resolve_file),
        ]

    def _row(self, node: scene.Row, resolve_file: FileResolver) -> h.Element:
        return h.div(class_="squid-row")[tuple(self._node(item, resolve_file) for item in node.items)]

    def _thumbnail(self, node: scene.Thumbnail) -> h.VoidElement | None:
        safe = _safe_url(node.url)
        if safe is None:
            return None
        return h.img(
            class_=["squid-thumbnail", {"squid-spoiler": node.spoiler}],
            src=safe,
            alt=node.description or "",
            tabindex=0 if node.spoiler else None,
        )

    def _gallery(self, node: scene.Gallery, resolve_file: FileResolver) -> h.Element:
        images: list[h.VoidElement] = []
        for item in node.items:
            url = resolve_file(item) if item.asset_name is not None else _safe_url(item.url)
            if url is not None:
                images.append(
                    h.img(
                        class_="squid-spoiler" if item.spoiler else "",
                        src=url,
                        alt=item.description or "",
                        tabindex=0 if item.spoiler else None,
                    )
                )
        return h.div(class_="squid-gallery")[images]

    def _extension(self, node: scene.Extension) -> h.Element:
        return h.div(
            class_="squid-extension", data_squid_extension=node.kind, data_squid_extension_version=node.version
        )
