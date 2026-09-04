"""Safe mechanical drawing for semantic HTML scenes."""

import base64
import json
import re
from collections.abc import Iterable
from html import escape

from markdown_it import MarkdownIt
from markdown_it.token import Token

from squid_ui import scene
from squid_ui.assets import Asset, InlineAsset, StoredAsset
from squid_ui.errors import DrawInvariantError
from squid_ui.html._safety import attribute as _attribute
from squid_ui.html._safety import safe_url as _safe_url
from squid_ui.renderer import AssetResolver
from squid_ui.scene.model import PlanResult
from squid_ui.text import Markup

DEFAULT_CSS = """
:root{color-scheme:light dark;font:16px/1.5 system-ui,sans-serif}
body{margin:0;background:Canvas;color:CanvasText}.squid-document{box-sizing:border-box;max-width:72rem;margin:auto;padding:1rem}
.squid-document *{box-sizing:border-box}.squid-stack,.squid-group,.squid-form,.squid-fields{display:grid;gap:.75rem}
.squid-cluster,.squid-actions{display:flex;flex-wrap:wrap;gap:.5rem}.squid-section,.squid-article,.squid-block,.squid-aside{
padding:1rem;border-inline-start:.25rem solid var(--squid-accent,GrayText);border-radius:.25rem;background:color-mix(in srgb,Canvas 92%,CanvasText)}
.squid-gallery,.squid-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(10rem,1fr));gap:.75rem}
.squid-gallery img,figure img{display:block;max-width:100%;height:auto}table{width:100%;border-collapse:collapse}th,td{padding:.5rem;
border:1px solid color-mix(in srgb,CanvasText 25%,transparent);text-align:start}input,textarea,select,button{font:inherit}
textarea,select,input:not([type=checkbox]){width:100%;max-width:36rem;padding:.5rem}.squid-field{display:grid;gap:.25rem}
.squid-spoiler{filter:blur(.5rem)}.squid-spoiler:focus,.squid-spoiler:hover{filter:none}
""".strip()

_MARKDOWN = MarkdownIt("js-default", {"html": False, "linkify": False})
_MEDIA_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*\Z")
_BOOLEAN_ATTRIBUTES = frozenset(
    {
        scene.HtmlAttributeName.CHECKED,
        scene.HtmlAttributeName.DISABLED,
        scene.HtmlAttributeName.MULTIPLE,
        scene.HtmlAttributeName.OPEN,
        scene.HtmlAttributeName.REQUIRED,
        scene.HtmlAttributeName.SELECTED,
    }
)
_DATA_ATTRIBUTES = {
    scene.HtmlAttributeName.CONVERSATION_TYPES: "data-squid-conversation-types",
    scene.HtmlAttributeName.DISPLAY: "data-squid-display",
    scene.HtmlAttributeName.EMPHASIS: "data-squid-emphasis",
    scene.HtmlAttributeName.ENTITY_TYPE: "data-squid-entity-type",
    scene.HtmlAttributeName.SELECTION_MAX: "data-squid-max",
    scene.HtmlAttributeName.SELECTION_MIN: "data-squid-min",
    scene.HtmlAttributeName.TIME_STYLE: "data-squid-time-style",
    scene.HtmlAttributeName.TIMEZONE: "data-squid-timezone",
    scene.HtmlAttributeName.TONE: "data-squid-tone",
}
_VOID_TAGS = frozenset({scene.HtmlTag.BR, scene.HtmlTag.HR, scene.HtmlTag.IMG, scene.HtmlTag.INPUT})


def _token_attribute(token: Token, name: str) -> str | None:
    value = token.attrGet(name)
    return value if isinstance(value, str) else None


class Renderer:
    """Draw a semantic HTML scene, without adding browser behavior or transport.

    Passing ``css`` opts into trusted host configuration. It is embedded verbatim in a
    standalone document and must never contain untrusted authored content.
    """

    def __init__(
        self,
        *,
        standalone: bool = False,
        title: str = "Squid UI",
        css: str = DEFAULT_CSS,
        asset_resolver: AssetResolver | None = None,
    ) -> None:
        self.standalone = standalone
        self.title = title
        self.css = css
        self.asset_resolver = asset_resolver

    def draw(
        self,
        document: scene.Scene[scene.HtmlBody],
        *,
        plan: PlanResult[scene.HtmlBody] | None = None,
    ) -> str:
        """Draw one planned HTML scene as a fragment or standalone document."""
        if document.protocol != scene.Codec.protocol:
            message = f"Renderer cannot draw scene protocol {document.protocol}"
            raise DrawInvariantError(message)
        if document.target_version != 1:
            message = f"Renderer cannot draw HTML target version {document.target_version}"
            raise DrawInvariantError(message)
        if not isinstance(document.body, scene.HtmlBody):
            message = f"HTML Renderer cannot draw a {type(document.body).__name__} body"
            raise DrawInvariantError(message)

        assets = {asset.key: asset for asset in document.assets}
        pager_data = json.dumps(
            [{"key": pager.key, "page": pager.page, "pages": pager.pages} for pager in document.pagers],
            separators=(",", ":"),
        )
        body = "".join(self._node(child, assets, plan) for child in document.body.children)
        locale = document.body.locale
        locale_attribute = "" if locale is None else f' lang="{_attribute(locale)}"'
        root = (
            f'<main class="squid-document"{locale_attribute} data-squid-protocol="{document.protocol}" '
            f'data-squid-target="{_attribute(document.target)}" data-squid-pagers="{_attribute(pager_data)}">'
            f"{body}</main>"
        )
        if not self.standalone:
            return root
        document_locale = "und" if locale is None else locale
        return (
            f'<!doctype html><html lang="{_attribute(document_locale)}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{escape(self.title)}</title><style>{self.css}</style></head><body>{root}</body></html>"
        )

    def _node(
        self,
        node: scene.HtmlNode,
        assets: dict[str, scene.Asset],
        plan: PlanResult[scene.HtmlBody] | None,
    ) -> str:
        if isinstance(node, scene.HtmlText):
            if node.markup is Markup.PLAIN:
                return escape(node.content)
            return self._markdown(node.content)
        return self._element(node, assets, plan)

    def _element(
        self,
        element: scene.HtmlElement,
        assets: dict[str, scene.Asset],
        plan: PlanResult[scene.HtmlBody] | None,
    ) -> str:
        attributes = list(self._attributes(element.attributes))
        if element.action is not None:
            attributes.extend(
                (
                    f'data-squid-action="{_attribute(element.action.action)}"',
                    f'data-squid-mode="{element.action.mode.value}"',
                )
            )
        if element.route is not None:
            attributes.append(f'data-route-id="{_attribute(element.route.route_id)}"')
        if element.form is not None:
            attributes.append(f'data-squid-form="{_attribute(element.form.key)}"')
            if element.form.field_name is not None:
                attributes.append(f'data-squid-field="{_attribute(element.form.field_name)}"')
        if element.time is not None:
            if element.tag is not scene.HtmlTag.TIME:
                message = "HTML time references require a time element"
                raise DrawInvariantError(message)
            attributes.append(f'datetime="{_attribute(element.time.instant)}"')
            if element.time.timezone is not None:
                attributes.append(f'data-squid-timezone="{_attribute(element.time.timezone)}"')
            if element.time.style is not None:
                attributes.append(f'data-squid-time-style="{_attribute(element.time.style)}"')
        if element.colour is not None:
            attributes.append(f'style="--squid-accent:#{element.colour.value:06x}"')

        tag = element.tag.value
        if element.url is not None:
            attribute_name = (
                "href" if element.tag is scene.HtmlTag.A else "src" if element.tag is scene.HtmlTag.IMG else None
            )
            if attribute_name is None:
                message = "HTML URL references require an anchor or image element"
                raise DrawInvariantError(message)
            if (safe := _safe_url(element.url.url)) is not None:
                attributes.append(f'{attribute_name}="{_attribute(safe)}"')
            elif element.tag is scene.HtmlTag.A:
                attributes.append('aria-disabled="true"')
        if element.asset is not None:
            if element.tag is not scene.HtmlTag.A:
                message = "HTML asset references require an anchor element"
                raise DrawInvariantError(message)
            if (resolved := self._resolve_asset(element.asset, assets, plan)) is not None:
                attributes.append(f'href="{_attribute(resolved)}"')
            else:
                attributes.append('aria-disabled="true"')

        suffix = "" if not attributes else " " + " ".join(attributes)
        if element.tag in _VOID_TAGS:
            if element.children:
                message = f"void HTML element {tag!r} cannot have children"
                raise DrawInvariantError(message)
            return f"<{tag}{suffix}>"
        children = "".join(self._node(child, assets, plan) for child in element.children)
        return f"<{tag}{suffix}>{children}</{tag}>"

    def _attributes(self, attributes: Iterable[scene.HtmlAttribute]) -> Iterable[str]:
        for attribute in attributes:
            name = _DATA_ATTRIBUTES.get(attribute.name, attribute.name.value)
            if attribute.name in _BOOLEAN_ATTRIBUTES:
                if bool(attribute.value):
                    yield name
                continue
            yield f'{name}="{_attribute(attribute.value)}"'

    def _resolve_asset(
        self,
        reference: scene.HtmlAssetRef,
        assets: dict[str, scene.Asset],
        plan: PlanResult[scene.HtmlBody] | None,
    ) -> str | None:
        metadata = assets.get(reference.key)
        if metadata is None or (metadata.name, metadata.media_type) != (reference.name, reference.media_type):
            return None
        if self.asset_resolver is not None and (resolved := self.asset_resolver(metadata)) is not None:
            return _safe_url(resolved)
        resource = plan.resources.get(f"asset:{reference.key}") if plan is not None else None
        if not isinstance(resource, Asset):
            return None
        if isinstance(resource.source, InlineAsset) and _MEDIA_TYPE.fullmatch(resource.media_type):
            encoded = base64.b64encode(resource.source.data).decode("ascii")
            return f"data:{resource.media_type};base64,{encoded}"
        if isinstance(resource.source, StoredAsset):
            return _safe_url(resource.source.reference)
        return None

    def _markdown(self, content: str) -> str:
        parsed = _MARKDOWN.parseInline(content)
        tokens = () if not parsed else parsed[0].children or ()
        return "".join(self._markdown_token(token) for token in tokens)

    def _markdown_token(self, token: Token) -> str:
        match token.type:
            case "text":
                return escape(token.content)
            case "softbreak":
                return "\n"
            case "hardbreak":
                return "<br>"
            case "code_inline":
                return f"<code>{escape(token.content)}</code>"
            case "strong_open":
                return "<strong>"
            case "strong_close":
                return "</strong>"
            case "em_open":
                return "<em>"
            case "em_close":
                return "</em>"
            case "s_open":
                return "<s>"
            case "s_close":
                return "</s>"
            case "link_open":
                href = _token_attribute(token, "href")
                if href is None or (safe := _safe_url(href)) is None:
                    return ""
                title = _token_attribute(token, "title")
                title_attribute = "" if title is None else f' title="{_attribute(title)}"'
                return f'<a href="{_attribute(safe)}" rel="noopener noreferrer"{title_attribute}>'
            case "link_close":
                return "</a>"
            case "image":
                source = _token_attribute(token, "src")
                if source is None or (safe := _safe_url(source)) is None:
                    return escape(token.content)
                title = _token_attribute(token, "title")
                title_attribute = "" if title is None else f' title="{_attribute(title)}"'
                return f'<img src="{_attribute(safe)}" alt="{_attribute(token.content)}"{title_attribute}>'
            case _:
                if token.content:
                    return escape(token.content)
                return escape(token.markup)


__all__ = ["DEFAULT_CSS", "AssetResolver", "Renderer"]
