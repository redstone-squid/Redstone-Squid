"""Safe semantic HTML drawing for resolved scenes."""

import base64
import json
from collections.abc import Callable
from datetime import datetime
from html import escape
from urllib.parse import urlsplit

from squid_ui import scene
from squid_ui.assets import Asset, InlineAsset, StoredAsset
from squid_ui.errors import DrawInvariantError
from squid_ui.scene.model import PlanResult
from squid_ui.temporal import ZonedDateTime

PREVIEW_CSS = """
.squid-view{box-sizing:border-box;max-width:720px;padding:16px;border-radius:8px;background:#313338;color:#dbdee1;
font:14px/1.375 system-ui,sans-serif}.squid-view *{box-sizing:border-box}.squid-panel{display:grid;gap:12px;padding:16px;
border-left:4px solid var(--squid-accent,#4e5058);border-radius:4px;background:#2b2d31}.squid-text{white-space:pre-wrap}
.squid-row{display:flex;flex-wrap:wrap;gap:8px}.squid-button{min-height:32px;padding:2px 16px;border:0;border-radius:3px;
background:#4e5058;color:#fff;font-weight:600}.squid-button--primary{background:#5865f2}.squid-button--success{background:#248046}
.squid-button--danger{background:#da373c}.squid-button:disabled{opacity:.5}.squid-link{text-decoration:none}
.squid-select{width:100%;min-height:40px;padding:8px;border:1px solid #1e1f22;border-radius:3px;background:#1e1f22;color:#dbdee1}
.squid-section{display:grid;grid-template-columns:1fr auto;gap:16px;align-items:start}.squid-thumbnail{max-width:96px;max-height:96px;
border-radius:4px}.squid-gallery{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:4px}
.squid-gallery img{width:100%;border-radius:4px}.squid-separator{height:1px;margin:4px 0;border:0;background:#3f4147}
.squid-separator--large{margin:12px 0}.squid-extension{padding:8px;border:1px dashed #6d6f78;border-radius:3px}
.squid-file[aria-disabled=true]{opacity:.5}
.squid-spoiler{filter:blur(12px);transition:filter .15s}.squid-spoiler:hover,.squid-spoiler:focus{filter:none}
""".strip()

type AssetResolver = Callable[[scene.Asset], str | None]
type FileResolver = Callable[[scene.File], str | None]


def _attribute(value: object) -> str:
    return escape(str(value), quote=True)


def _url(value: str) -> str | None:
    parsed = urlsplit(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


class Renderer:
    """Draw scenes as semantic HTML suitable for previews or browser adapters."""

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

    def draw(self, document: scene.Document, *, plan: PlanResult | None = None) -> str:
        if document.protocol != scene.Codec.protocol:
            message = f"Renderer cannot draw scene protocol {document.protocol}"
            raise DrawInvariantError(message)
        if document.target_version != 1:
            message = f"Renderer cannot preview target version {document.target_version}"
            raise DrawInvariantError(message)
        if not isinstance(document.body, scene.ComponentsV2):
            # The real gate, and both stricter and more honest than a target id: what this
            # preview can draw is a component tree, not a name. A classic message is content
            # and embeds with no tree to walk.
            message = f"HTML preview cannot draw a {type(document.body).__name__} body"
            raise DrawInvariantError(message)
        pager_data = json.dumps(
            [{"key": pager.key, "page": pager.page, "pages": pager.pages} for pager in document.pagers],
            separators=(",", ":"),
        )
        assets = {asset.key: asset for asset in document.assets}

        def resolve_file(node: scene.File) -> str | None:
            metadata = assets.get(node.asset_key)
            if metadata is None:
                return None
            if self.asset_resolver is not None and (resolved := self.asset_resolver(metadata)) is not None:
                return resolved
            resource = plan.resources.get(f"asset:{node.asset_key}") if plan is not None else None
            if not isinstance(resource, Asset):
                return None
            if isinstance(resource.source, InlineAsset):
                encoded = base64.b64encode(resource.source.data).decode("ascii")
                return f"data:{resource.media_type};base64,{encoded}"
            if isinstance(resource.source, StoredAsset):
                return _url(resource.source.reference)
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
            case scene.Text(content=content, markup=markup):
                return f'<div class="squid-text" data-squid-markup="{markup.value}">{escape(content)}</div>'
            case scene.Time(instant=instant, style=style, prefix=prefix):
                return (
                    f'<div class="squid-text">{escape(prefix or "")}'
                    f'<time datetime="{_attribute(instant)}" data-squid-style="{_attribute(style)}">'
                    f"{escape(instant)}</time></div>"
                )
            case scene.ZonedTime(instant=instant, timezone=timezone, prefix=prefix):
                value = ZonedDateTime(datetime.fromisoformat(instant), timezone)
                return (
                    f'<div class="squid-text">{escape(prefix or "")}'
                    f'<time datetime="{_attribute(instant)}" data-squid-timezone="{_attribute(timezone)}">'
                    f"{escape(value.isoformat())}</time></div>"
                )
            case scene.File(name=name, spoiler=spoiler):
                spoiler_class = " squid-spoiler" if spoiler else ""
                focus = ' tabindex="0"' if spoiler else ""
                resolved = resolve_file(node)
                if resolved is None:
                    return (
                        f'<span class="squid-button squid-file{spoiler_class}" aria-disabled="true"{focus}>'
                        f"{escape(name)}</span>"
                    )
                return (
                    f'<a class="squid-button squid-link squid-file{spoiler_class}" href="{_attribute(resolved)}" '
                    f'download="{_attribute(name)}"{focus}>{escape(name)}</a>'
                )
            case scene.Separator(large=large, visible=visible):
                if not visible:
                    return ""
                modifier = " squid-separator--large" if large else ""
                return f'<hr class="squid-separator{modifier}">'
            case scene.Panel(children=children, accent=accent, spoiler=spoiler):
                style = f' style="--squid-accent:#{accent:06x}"' if accent is not None else ""
                spoiler_attributes = ' squid-spoiler" tabindex="0' if spoiler else '"'
                return (
                    f'<section class="squid-panel{spoiler_attributes}{style}>'
                    f"{''.join(self._node(child, resolve_file) for child in children)}</section>"
                )
            case scene.Section(texts=texts, accessory=accessory):
                content = "".join(self._node(text, resolve_file) for text in texts)
                return (
                    f'<section class="squid-section"><div>{content}</div>'
                    f"{self._node(accessory, resolve_file)}</section>"
                )
            case scene.Row(items=items):
                return f'<div class="squid-row">{"".join(self._node(item, resolve_file) for item in items)}</div>'
            case scene.Button(
                label=label,
                action=action,
                style=style,
                emoji=emoji,
                disabled=disabled,
                mode=mode,
            ):
                disabled_attribute = " disabled" if disabled else ""
                icon = f'<span class="squid-button__emoji">{escape(emoji.name)}</span> ' if emoji else ""
                return (
                    f'<button type="button" class="squid-button squid-button--{style.value}" '
                    f'data-squid-action="{_attribute(action)}" data-squid-mode="{mode.value}"'
                    f"{disabled_attribute}>{icon}{escape(label or '')}</button>"
                )
            case scene.RoutedButton(label=label, route_id=route_id, style=style, emoji=emoji, disabled=disabled):
                disabled_attribute = " disabled" if disabled else ""
                icon = f'<span class="squid-button__emoji">{escape(emoji.name)}</span> ' if emoji else ""
                return (
                    f'<button type="button" class="squid-button squid-button--{style.value}" '
                    f'data-route-id="{_attribute(route_id)}"'
                    f"{disabled_attribute}>{icon}{escape(label or '')}</button>"
                )
            case scene.Link(label=label, url=url, emoji=emoji, disabled=disabled):
                icon = f'<span class="squid-button__emoji">{escape(emoji.name)}</span> ' if emoji else ""
                safe = _url(url)
                if safe is None or disabled:
                    return (
                        f'<span class="squid-button squid-link" aria-disabled="true">{icon}{escape(label or "")}</span>'
                    )
                return (
                    f'<a class="squid-button squid-link" href="{_attribute(safe)}" '
                    f'rel="noopener noreferrer">{icon}{escape(label or "")}</a>'
                )
            case scene.PremiumButton(sku_id=sku_id):
                return (
                    f'<button type="button" class="squid-button squid-premium" disabled '
                    f'data-sku-id="{sku_id}">Premium</button>'
                )
            case scene.Select(
                options=options,
                action=action,
                placeholder=placeholder,
                min_values=minimum,
                max_values=maximum,
                disabled=disabled,
                mode=mode,
            ):
                disabled_attribute = " disabled" if disabled else ""
                multiple = " multiple" if maximum > 1 else ""
                prompt = (
                    f'<option value="" disabled selected>{escape(placeholder)}</option>'
                    if placeholder is not None
                    else ""
                )
                rendered = "".join(
                    f'<option value="{_attribute(option.value)}"{" selected" if option.default else ""}>'
                    f"{escape((option.emoji.name + ' ') if option.emoji else '')}{escape(option.label)}</option>"
                    for option in options
                )
                return (
                    f'<select class="squid-select" data-squid-action="{_attribute(action)}" '
                    f'data-squid-mode="{mode.value}" data-squid-min="{minimum}" data-squid-max="{maximum}"'
                    f"{multiple}{disabled_attribute}>{prompt}{rendered}</select>"
                )
            case scene.RoutedSelect(
                options=options,
                route_id=route_id,
                placeholder=placeholder,
                min_values=minimum,
                max_values=maximum,
                disabled=disabled,
            ):
                disabled_attribute = " disabled" if disabled else ""
                multiple = " multiple" if maximum > 1 else ""
                prompt = (
                    f'<option value="" disabled selected>{escape(placeholder)}</option>'
                    if placeholder is not None
                    else ""
                )
                rendered = "".join(
                    f'<option value="{_attribute(option.value)}"{" selected" if option.default else ""}>'
                    f"{escape((option.emoji.name + ' ') if option.emoji else '')}{escape(option.label)}</option>"
                    for option in options
                )
                return (
                    f'<select class="squid-select" data-route-id="{_attribute(route_id)}" '
                    f'data-squid-min="{minimum}" data-squid-max="{maximum}"'
                    f"{multiple}{disabled_attribute}>{prompt}{rendered}</select>"
                )
            case scene.Thumbnail(url=url, description=description, spoiler=spoiler):
                safe = _url(url)
                if safe is None:
                    return ""
                spoiler_class = " squid-spoiler" if spoiler else ""
                focus = ' tabindex="0"' if spoiler else ""
                return f'<img class="squid-thumbnail{spoiler_class}" src="{_attribute(safe)}" alt="{_attribute(description or "")}"{focus}>'
            case scene.Gallery(items=items):
                images = "".join(
                    f'<img class="{"squid-spoiler" if item.spoiler else ""}" src="{_attribute(safe)}" '
                    f'alt="{_attribute(item.description or "")}"{' tabindex="0"' if item.spoiler else ""}>'
                    for item in items
                    if (safe := _url(item.url)) is not None
                )
                return f'<div class="squid-gallery">{images}</div>'
            case scene.Extension(kind=kind, version=version):
                return (
                    f'<div class="squid-extension" data-squid-extension="{_attribute(kind)}" '
                    f'data-squid-extension-version="{version}"></div>'
                )
        message = f"Renderer cannot draw scene node {type(node).__name__}"
        raise DrawInvariantError(message)
