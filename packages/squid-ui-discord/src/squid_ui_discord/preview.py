"""HTML preview drawing for Discord Components V2 scenes."""

import base64
import json
from collections.abc import Callable
from datetime import datetime
from html import escape

from squid_ui_html.markup import markdown
from squid_ui_html.safety import attribute as _attribute
from squid_ui_html.safety import safe_url as _safe_url

from squid_ui import scene
from squid_ui.assets import Asset, InlineAsset, LocalAsset, StoredAsset
from squid_ui.entity import EntityKind, EntityRef
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.result import PlanResult
from squid_ui.renderer import AssetResolver
from squid_ui.temporal import ZonedDateTime
from squid_ui.text import Markup
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

        body = "".join(self._node(child, resolve_file) for child in document.body.children)
        root = (
            f'<div class="squid-view" data-squid-protocol="{document.protocol}" '
            f'data-squid-target="{_attribute(document.target)}" data-squid-pagers="{_attribute(pager_data)}">{body}</div>'
        )
        if not self.standalone:
            return root
        return (
            '<!doctype html><html><head><meta charset="utf-8">'
            f"<style>{self.css}</style></head><body>{root}</body></html>"
        )

    def _node(
        self,
        node: scene.Node | scene.Link | scene.PremiumButton | scene.Button | scene.RoutedButton,
        resolve_file: FileResolver,
    ) -> str:
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
            case scene.Button():
                return self._button(node)
            case scene.RoutedButton():
                return self._routed_button(node)
            case scene.Link():
                return self._link(node)
            case scene.PremiumButton():
                return self._premium_button(node)
            case scene.Select():
                return self._select(node)
            case scene.RoutedSelect():
                return self._routed_select(node)
            case scene.EntitySelect():
                return self._entity_select(node)
            case scene.Thumbnail():
                return self._thumbnail(node)
            case scene.Gallery():
                return self._gallery(node, resolve_file=resolve_file)
            case scene.Extension():
                return self._extension(node)
        message = f"DiscordPreviewRenderer cannot draw scene node {type(node).__name__}"
        raise DrawInvariantError(message)

    def _text(self, node: scene.Text) -> str:
        rendered = (
            escape(node.content)
            if node.markup is Markup.PLAIN
            else markdown(node.content, block=True, discord=node.markup is Markup.DISCORD_MARKDOWN)
        )
        return f'<div class="squid-text" data-squid-markup="{node.markup.value}">{rendered}</div>'

    def _time(self, node: scene.Time) -> str:
        return (
            f'<div class="squid-text">{escape(node.prefix or "")}'
            f'<time datetime="{_attribute(node.instant)}" data-squid-style="{_attribute(node.style)}">'
            f"{escape(node.instant)}</time></div>"
        )

    def _zoned_time(self, node: scene.ZonedTime) -> str:
        value = ZonedDateTime(datetime.fromisoformat(node.instant), node.timezone)
        return (
            f'<div class="squid-text">{escape(node.prefix or "")}'
            f'<time datetime="{_attribute(node.instant)}" data-squid-timezone="{_attribute(node.timezone)}">'
            f"{escape(value.isoformat())}</time></div>"
        )

    def _file(self, node: scene.File, resolve_file: FileResolver) -> str:
        spoiler_class = " squid-spoiler" if node.spoiler else ""
        focus = ' tabindex="0"' if node.spoiler else ""
        resolved = resolve_file(node)
        if resolved is None:
            return (
                f'<span class="squid-button squid-file{spoiler_class}" aria-disabled="true"{focus}>'
                f"{escape(node.name)}</span>"
            )
        return (
            f'<a class="squid-button squid-link squid-file{spoiler_class}" href="{_attribute(resolved)}" '
            f'download="{_attribute(node.name)}"{focus}>{escape(node.name)}</a>'
        )

    def _separator(self, node: scene.Separator) -> str:
        if not node.visible:
            return ""
        modifier = " squid-separator--large" if node.large else ""
        return f'<hr class="squid-separator{modifier}">'

    def _panel(self, node: scene.Panel, resolve_file: FileResolver) -> str:
        style = f' style="--squid-accent:#{node.accent:06x}"' if node.accent is not None else ""
        spoiler_attributes = ' squid-spoiler" tabindex="0' if node.spoiler else '"'
        return (
            f'<section class="squid-panel{spoiler_attributes}{style}>'
            f"{''.join(self._node(child, resolve_file) for child in node.children)}</section>"
        )

    def _section(self, node: scene.Section, resolve_file: FileResolver) -> str:
        content = "".join(self._node(text, resolve_file) for text in node.texts)
        return (
            f'<section class="squid-section"><div>{content}</div>{self._node(node.accessory, resolve_file)}</section>'
        )

    def _row(self, node: scene.Row, resolve_file: FileResolver) -> str:
        return f'<div class="squid-row">{"".join(self._node(item, resolve_file) for item in node.items)}</div>'

    def _button(self, node: scene.Button) -> str:
        disabled_attribute = " disabled" if node.disabled else ""
        icon = f'<span class="squid-button__emoji">{escape(node.emoji.name)}</span> ' if node.emoji else ""
        return (
            f'<button type="button" class="squid-button squid-button--{node.style.value}" '
            f'data-squid-action="{_attribute(node.action)}" data-squid-mode="{node.mode.value}"'
            f"{disabled_attribute}>{icon}{escape(node.label or '')}</button>"
        )

    def _routed_button(self, node: scene.RoutedButton) -> str:
        disabled_attribute = " disabled" if node.disabled else ""
        icon = f'<span class="squid-button__emoji">{escape(node.emoji.name)}</span> ' if node.emoji else ""
        return (
            f'<button type="button" class="squid-button squid-button--{node.style.value}" '
            f'data-route-id="{_attribute(node.route_id)}"'
            f"{disabled_attribute}>{icon}{escape(node.label or '')}</button>"
        )

    def _link(self, node: scene.Link) -> str:
        icon = f'<span class="squid-button__emoji">{escape(node.emoji.name)}</span> ' if node.emoji else ""
        safe = _safe_url(node.url)
        if safe is None or node.disabled:
            return f'<span class="squid-button squid-link" aria-disabled="true">{icon}{escape(node.label or "")}</span>'
        return (
            f'<a class="squid-button squid-link" href="{_attribute(safe)}" '
            f'rel="noopener noreferrer">{icon}{escape(node.label or "")}</a>'
        )

    def _premium_button(self, node: scene.PremiumButton) -> str:
        return (
            f'<button type="button" class="squid-button squid-premium" disabled '
            f'data-sku-id="{node.sku_id}">Premium</button>'
        )

    def _select(self, node: scene.Select) -> str:
        disabled_attribute = " disabled" if node.disabled else ""
        multiple = " multiple" if node.max_values > 1 else ""
        prompt = (
            f'<option value="" disabled selected>{escape(node.placeholder)}</option>'
            if node.placeholder is not None
            else ""
        )
        rendered = "".join(
            f'<option value="{_attribute(option.value)}"{" selected" if option.default else ""}>'
            f"{escape((option.emoji.name + ' ') if option.emoji else '')}{escape(option.label)}</option>"
            for option in node.options
        )
        return (
            f'<select class="squid-select" data-squid-action="{_attribute(node.action)}" '
            f'data-squid-mode="{node.mode.value}" data-squid-min="{node.min_values}" data-squid-max="{node.max_values}"'
            f"{multiple}{disabled_attribute}>{prompt}{rendered}</select>"
        )

    def _routed_select(self, node: scene.RoutedSelect) -> str:
        disabled_attribute = " disabled" if node.disabled else ""
        multiple = " multiple" if node.max_values > 1 else ""
        prompt = (
            f'<option value="" disabled selected>{escape(node.placeholder)}</option>'
            if node.placeholder is not None
            else ""
        )
        rendered = "".join(
            f'<option value="{_attribute(option.value)}"{" selected" if option.default else ""}>'
            f"{escape((option.emoji.name + ' ') if option.emoji else '')}{escape(option.label)}</option>"
            for option in node.options
        )
        return (
            f'<select class="squid-select" data-route-id="{_attribute(node.route_id)}" '
            f'data-squid-min="{node.min_values}" data-squid-max="{node.max_values}"'
            f"{multiple}{disabled_attribute}>{prompt}{rendered}</select>"
        )

    def _entity_select(self, node: scene.EntitySelect) -> str:
        # Discord fills entity pickers from the guild, so the preview can only show the
        # current values, written as the mentions Discord would display for them.
        disabled_attribute = " disabled" if node.disabled else ""
        multiple = " multiple" if node.max_values > 1 else ""
        prompt = (
            f'<option value="" disabled{"" if node.default_values else " selected"}>{escape(node.placeholder)}</option>'
            if node.placeholder is not None
            else ""
        )
        rendered = "".join(
            f'<option value="{_attribute(str(ref.id))}" selected>{escape(_mention(ref))}</option>'
            for ref in node.default_values
        )
        return (
            f'<select class="squid-select" data-squid-action="{_attribute(node.action)}" '
            f'data-squid-entity="{node.entity_type.value}" data-squid-mode="{node.mode.value}" '
            f'data-squid-min="{node.min_values}" data-squid-max="{node.max_values}"'
            f"{multiple}{disabled_attribute}>{prompt}{rendered}</select>"
        )

    def _thumbnail(self, node: scene.Thumbnail) -> str:
        safe = _safe_url(node.url)
        if safe is None:
            return ""
        spoiler_class = " squid-spoiler" if node.spoiler else ""
        focus = ' tabindex="0"' if node.spoiler else ""
        return f'<img class="squid-thumbnail{spoiler_class}" src="{_attribute(safe)}" alt="{_attribute(node.description or "")}"{focus}>'

    def _gallery(self, node: scene.Gallery, resolve_file: FileResolver) -> str:
        images = "".join(
            f'<img class="{"squid-spoiler" if item.spoiler else ""}" src="{_attribute(safe)}" '
            f'alt="{_attribute(item.description or "")}"{' tabindex="0"' if item.spoiler else ""}>'
            for item in node.items
            if (safe := resolve_file(item) if item.asset_name is not None else _safe_url(item.url)) is not None
        )
        return f'<div class="squid-gallery">{images}</div>'

    def _extension(self, node: scene.Extension) -> str:
        return (
            f'<div class="squid-extension" data-squid-extension="{_attribute(node.kind)}" '
            f'data-squid-extension-version="{node.version}"></div>'
        )


def _mention(ref: EntityRef) -> str:
    prefix = {EntityKind.USER: "@", EntityKind.ROLE: "@&", EntityKind.CONVERSATION: "#"}[ref.kind]
    return f"<{prefix}{ref.id}>"
