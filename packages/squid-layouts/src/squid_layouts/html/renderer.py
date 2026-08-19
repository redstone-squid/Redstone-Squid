"""Safe semantic HTML drawing for resolved scenes."""

import json
from html import escape
from urllib.parse import urlsplit

from squid_layouts.errors import DrawInvariantError
from squid_layouts.scene import (
    PlanResult,
    SceneButton,
    SceneDocument,
    SceneExtension,
    SceneGallery,
    SceneLink,
    SceneNode,
    ScenePanel,
    SceneRow,
    SceneSection,
    SceneSelect,
    SceneSeparator,
    SceneText,
    SceneThumbnail,
)
from squid_layouts.scene_codec import SceneCodec

DISCORD_PREVIEW_CSS = """
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
""".strip()


def _attribute(value: object) -> str:
    return escape(str(value), quote=True)


def _url(value: str) -> str | None:
    parsed = urlsplit(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


class HtmlRenderer:
    """Draw scenes as semantic HTML suitable for previews or browser adapters."""

    def __init__(self, *, standalone: bool = False, css: str = DISCORD_PREVIEW_CSS) -> None:
        self.standalone = standalone
        self.css = css

    def draw(self, scene: SceneDocument, *, plan: PlanResult | None = None) -> str:
        if scene.protocol != SceneCodec.protocol:
            message = f"HtmlRenderer cannot draw scene protocol {scene.protocol}"
            raise DrawInvariantError(message)
        if scene.target != "discord.components-v2" or scene.target_version != 1:
            message = f"HtmlRenderer cannot preview target {scene.target!r} version {scene.target_version}"
            raise DrawInvariantError(message)
        pager_data = json.dumps(
            [{"key": pager.key, "page": pager.page, "pages": pager.pages} for pager in scene.pagers],
            separators=(",", ":"),
        )
        body = "".join(self._node(child) for child in scene.children)
        root = (
            f'<div class="squid-view" data-squid-protocol="{scene.protocol}" '
            f'data-squid-target="{_attribute(scene.target)}" data-squid-pagers="{_attribute(pager_data)}">{body}</div>'
        )
        if not self.standalone:
            return root
        return (
            '<!doctype html><html><head><meta charset="utf-8">'
            f"<style>{self.css}</style></head><body>{root}</body></html>"
        )

    def _node(self, node: SceneNode | SceneLink | SceneButton) -> str:
        match node:
            case SceneText(content=content, dialect=dialect):
                return f'<div class="squid-text" data-squid-dialect="{dialect.value}">{escape(content)}</div>'
            case SceneSeparator(large=large, visible=visible):
                if not visible:
                    return ""
                modifier = " squid-separator--large" if large else ""
                return f'<hr class="squid-separator{modifier}">'
            case ScenePanel(children=children, accent=accent):
                style = f' style="--squid-accent:#{accent:06x}"' if accent is not None else ""
                return (
                    f'<section class="squid-panel"{style}>{"".join(self._node(child) for child in children)}</section>'
                )
            case SceneSection(texts=texts, accessory=accessory):
                content = "".join(self._node(text) for text in texts)
                return f'<section class="squid-section"><div>{content}</div>{self._node(accessory)}</section>'
            case SceneRow(items=items):
                return f'<div class="squid-row">{"".join(self._node(item) for item in items)}</div>'
            case SceneButton(
                label=label,
                action=action,
                style=style,
                emoji=emoji,
                disabled=disabled,
                policy=policy,
            ):
                disabled_attribute = " disabled" if disabled else ""
                icon = f'<span class="squid-button__emoji">{escape(emoji)}</span> ' if emoji else ""
                return (
                    f'<button type="button" class="squid-button squid-button--{style.value}" '
                    f'data-squid-action="{_attribute(action)}" data-squid-policy="{policy.value}"'
                    f"{disabled_attribute}>{icon}{escape(label)}</button>"
                )
            case SceneLink(label=label, url=url):
                safe = _url(url)
                if safe is None:
                    return f'<span class="squid-button squid-link">{escape(label)}</span>'
                return (
                    f'<a class="squid-button squid-link" href="{_attribute(safe)}" '
                    f'rel="noopener noreferrer">{escape(label)}</a>'
                )
            case SceneSelect(
                options=options,
                action=action,
                placeholder=placeholder,
                min_values=minimum,
                max_values=maximum,
                disabled=disabled,
                policy=policy,
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
                    f"{escape(option.label)}</option>"
                    for option in options
                )
                return (
                    f'<select class="squid-select" data-squid-action="{_attribute(action)}" '
                    f'data-squid-policy="{policy.value}" data-squid-min="{minimum}" data-squid-max="{maximum}"'
                    f"{multiple}{disabled_attribute}>{prompt}{rendered}</select>"
                )
            case SceneThumbnail(url=url, description=description):
                safe = _url(url)
                if safe is None:
                    return ""
                return f'<img class="squid-thumbnail" src="{_attribute(safe)}" alt="{_attribute(description or "")}">'
            case SceneGallery(items=items):
                images = "".join(
                    f'<img src="{_attribute(safe)}" alt="{_attribute(item.description or "")}">'
                    for item in items
                    if (safe := _url(item.url)) is not None
                )
                return f'<div class="squid-gallery">{images}</div>'
            case SceneExtension(kind=kind, version=version):
                return (
                    f'<div class="squid-extension" data-squid-extension="{_attribute(kind)}" '
                    f'data-squid-extension-version="{version}"></div>'
                )
        message = f"HtmlRenderer cannot draw scene node {type(node).__name__}"
        raise DrawInvariantError(message)
