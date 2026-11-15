"""Shared safe Markdown drawing for native HTML and Discord previews."""

from html import escape

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token

from squid_ui_html.safety import attribute as _attribute
from squid_ui_html.safety import safe_url as _safe_url

_MARKDOWN = MarkdownIt("js-default", {"html": False, "linkify": False})


def _spoiler(state: StateInline, silent: bool) -> bool:
    start = state.pos
    if not state.src.startswith("||", start):
        return False
    state.pos += 2
    # Let the inline parser skip escapes and code spans while looking for the closer.
    while state.pos < state.posMax:
        if state.src.startswith("||", state.pos):
            end = state.pos
            if not silent:
                token = state.push("spoiler", "", 0)
                token.children = state.md.parseInline(state.src[start + 2 : end], state.env)[0].children
            state.pos = end + 2
            return True
        state.md.inline.skipToken(state)
    state.pos = start
    return False


def _subtext(state: StateBlock, start: int, end: int, silent: bool) -> bool:
    pos = state.bMarks[start] + state.tShift[start]
    if state.is_code_block(start) or not state.src.startswith("-# ", pos):
        return False
    if silent:
        return True
    state.line = start + 1
    state.push("subtext_open", "", 1)
    token = state.push("inline", "", 0)
    token.content = state.src[pos + 3 : state.eMarks[start]]
    token.map = [start, state.line]
    token.children = []
    state.push("subtext_close", "", -1)
    return True


_DISCORD_MARKDOWN = MarkdownIt("js-default", {"html": False, "linkify": False})
_DISCORD_MARKDOWN.inline.add_terminator_char("|")
_DISCORD_MARKDOWN.inline.ruler.before("text", "spoiler", _spoiler)
_DISCORD_MARKDOWN.block.ruler.before("paragraph", "subtext", _subtext, {"alt": ["paragraph"]})


def _token_attribute(token: Token, name: str) -> str | None:
    value = token.attrGet(name)
    return value if isinstance(value, str) else None


def markdown(content: str, *, block: bool = False, discord: bool = False) -> str:
    parser = _DISCORD_MARKDOWN if discord else _MARKDOWN
    parsed = parser.parse(content) if block else parser.parseInline(content)
    tokens = parsed if block else (() if not parsed else parsed[0].children or ())
    return "".join(_markdown_token(token) for token in tokens)


def _markdown_token(token: Token) -> str:
    if token.type == "inline":
        return "".join(_markdown_token(child) for child in token.children or ())
    if token.type in {"fence", "code_block"}:
        return f"<pre><code>{escape(token.content)}</code></pre>"
    if token.tag in {"p", "blockquote", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
        return f"<{token.tag}>" if token.nesting == 1 else f"</{token.tag}>"
    if token.type == "hr":
        return "<hr>"
    match token.type:
        case "subtext_open":
            return '<p class="squid-subtext">'
        case "subtext_close":
            return "</p>"
        case "spoiler":
            content = "".join(_markdown_token(child) for child in token.children or ())
            return (
                '<label class="squid-text-spoiler"><input type="checkbox" aria-label="Reveal spoiler">'
                f'<span class="squid-text-spoiler__content">{content}</span></label>'
            )
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
