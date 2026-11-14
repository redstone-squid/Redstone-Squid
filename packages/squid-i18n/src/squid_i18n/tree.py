"""Immutable text structure and library-backed Markdown parsing and serialization."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from types import SimpleNamespace

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdformat.plugins import PARSER_EXTENSIONS
from mdformat.renderer import MDRenderer, RenderContext, RenderTreeNode


@dataclass(frozen=True, slots=True)
class Node:
    """A literal, inline style, or block with independently composed children."""

    kind: str
    content: str = ""
    children: tuple[Node, ...] = ()
    tag: str = ""
    attrs: tuple[tuple[str, str], ...] = ()
    markup: str = ""
    info: str = ""
    block: bool = False


def literal(content: str) -> Node:
    return Node("text", content)


def parse(parts: Sequence[str | Node]) -> tuple[Node, ...]:
    return _parse(tuple(parts))


@lru_cache(maxsize=2048)
def _parse(parts: tuple[str | Node, ...]) -> tuple[Node, ...]:
    """Parse only authored strings, inserting captured nodes after parsing."""
    prefix = "squidTextSlot"
    authored = "".join(part for part in parts if isinstance(part, str))
    while prefix in authored:
        prefix += "X"
    slots: dict[str, Node] = {}
    source: list[str] = []
    for part in parts:
        if isinstance(part, str):
            source.append(part)
        else:
            marker = f"{prefix}{len(slots)}End"
            slots[marker] = part
            source.append(marker)
    pattern = re.compile("(" + "|".join(map(re.escape, slots)) + ")") if slots else None

    def substitute(content: str) -> tuple[Node, ...]:
        return tuple(
            slots[part] if part in slots else literal(part)
            for part in (pattern.split(content) if pattern else [content])
            if part
        )

    def freeze(tokens: Sequence[Token]) -> tuple[Node, ...]:
        roots: list[Node] = []
        stack: list[tuple[Token, list[Node]]] = []
        for token in tokens:
            for key, value in token.attrs.items():
                if any(marker in str(value) for marker in slots):
                    destination = "".join(plain(node) for node in substitute(str(value)))
                    if key in {"href", "src"} and not valid_link(destination):
                        msg = "Unsafe Markdown destination"
                        raise ValueError(msg)
                    token.attrs[key] = destination
            if token.nesting == 1:
                stack.append((token, roots))
                roots = []
                continue
            if token.nesting == -1:
                opening, parent = stack.pop()
                parent.append(
                    Node(
                        opening.type.removesuffix("_open"),
                        children=tuple(roots),
                        tag=opening.tag,
                        attrs=tuple((key, str(value)) for key, value in opening.attrs.items()),
                        markup=opening.markup,
                        block=opening.block,
                    )
                )
                roots = parent
                continue
            if token.type == "text":
                roots.extend(substitute(token.content))
            else:
                children = freeze(token.children) if token.children is not None else ()
                content = token.content
                if token.type in {"code_inline", "fence", "code_block"}:
                    content = "".join(plain(node) for node in substitute(content))
                attrs = tuple((key, str(value)) for key, value in token.attrs.items())
                if any(marker in value for _, value in attrs for marker in slots):
                    msg = "Use a link constructor for dynamic destinations"
                    raise ValueError(msg)
                roots.append(
                    Node(token.type, content, children, token.tag, attrs, token.markup, token.info, token.block)
                )
        return tuple(roots)

    parsed = MarkdownIt("commonmark", {"html": False}).enable(["strikethrough", "table"]).parse("".join(source))
    nodes = freeze(parsed)
    if len(nodes) == 1 and nodes[0].kind == "paragraph":
        return nodes[0].children[0].children
    return nodes


def plain(node: Node) -> str:
    if node.kind in {"softbreak", "hardbreak"}:
        return "\n"
    if node.kind == "mention":
        return ("#" if dict(node.attrs)["kind"] == "channel" else "@") + node.content
    if node.kind == "emoji":
        return f":{node.content}:"
    content = "".join(plain(child) for child in node.children) if node.children else node.content
    return content + "\n" if node.kind in {"paragraph", "heading", "list_item"} else content


def valid_link(destination: str) -> bool:
    return MarkdownIt().validateLink(destination)


def _destination_nodes(nodes: Sequence[Node], discord: bool) -> tuple[Node, ...]:
    result: list[Node] = []
    for node in nodes:
        if node.kind == "mention":
            prefix = {"user": "@", "role": "@&", "channel": "#"}[dict(node.attrs)["kind"]]
            result.append(Node("html_inline", f"<{prefix}{node.content}>") if discord else literal(plain(node)))
        elif node.kind == "discord_markup":
            result.append(Node("html_inline", node.content) if discord else literal(node.content))
        elif node.kind == "emoji":
            attrs = dict(node.attrs)
            result.append(
                Node("html_inline", f"<{attrs['animated']}:{node.content}:{attrs['id']}>")
                if discord
                else literal(plain(node))
            )
        elif node.kind == "timestamp":
            attrs = dict(node.attrs)
            result.append(
                Node("html_inline", f"<t:{attrs['unix']}:{attrs['style']}>") if discord else literal(node.content)
            )
        else:
            content = node.content.replace("\0", "\ufffd")
            if discord and node.kind == "text":
                content = content.replace("@", "@\u200b").replace("<#", "<\u200b#")
            result.append(replace(node, content=content, children=_destination_nodes(node.children, discord)))
    return tuple(result)


def tokens(nodes: Sequence[Node]) -> list[Token]:
    result: list[Token] = []
    for node in nodes:
        if node.kind == "sequence":
            result.extend(tokens(node.children))
            continue
        container = node.kind in {
            "strong",
            "em",
            "s",
            "link",
            "paragraph",
            "heading",
            "blockquote",
            "bullet_list",
            "ordered_list",
            "list_item",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
        }
        token = Token(node.kind + ("_open" if container else ""), node.tag, 1 if container else 0)
        token.content = node.content
        token.attrs = dict(node.attrs)
        token.markup = node.markup
        token.info = node.info
        token.block = node.block
        if node.kind in {"inline", "image"}:
            token.children = tokens(node.children)
        result.append(token)
        if container:
            result.extend(tokens(node.children))
            result.append(Token(node.kind + "_close", node.tag, -1))
    return result


def markdown(nodes: Sequence[Node], *, discord: bool = False, escape: Callable[[str], str] | None = None) -> str:
    return _markdown(_destination_nodes(nodes, discord), escape)


@lru_cache(maxsize=2048)
def _markdown(nodes: tuple[Node, ...], escape: Callable[[str], str] | None = None) -> str:
    """Serialize a composed tree without interpreting literal nodes as markup."""

    def flatten(items: Sequence[Node]) -> list[Node]:
        return [child for item in items for child in (flatten(item.children) if item.kind == "sequence" else [item])]

    stream: list[Token] = []
    pending: list[Node] = []

    def flush() -> None:
        if pending:
            inline = Token("inline", "", 0)
            inline.children = tokens(pending)
            stream.extend([Token("paragraph_open", "p", 1), inline, Token("paragraph_close", "p", -1)])
            pending.clear()

    for node in flatten(nodes):
        if node.block:
            flush()
            stream.extend(tokens((node,)))
        else:
            pending.append(node)
    flush()
    # mdformat rewrites leading spaces and trailing newlines even in html_inline
    # tokens. Restore Discord markup after serialization so copied messages survive
    # unchanged, including when nested inside authored formatting.
    prefix = "squidRawSlot"
    while prefix in repr(nodes):
        prefix += "X"
    raw: dict[str, str] = {}

    def protect_raw(items: Sequence[Token]) -> None:
        for token in items:
            if token.type == "html_inline":
                marker = f"{prefix}{len(raw)}End"
                raw[marker] = token.content
                token.content = marker
            if token.children:
                protect_raw(token.children)

    protect_raw(stream)
    plugins = [PARSER_EXTENSIONS["gfm"], PARSER_EXTENSIONS["tables"]]
    if escape is not None:

        def render_literal(node: RenderTreeNode, context: RenderContext) -> str:
            return escape(node.content)

        def render_paragraph(node: RenderTreeNode, context: RenderContext) -> str:
            return "".join(child.render(context) for child in node.children)

        plugins.insert(0, SimpleNamespace(RENDERERS={"text": render_literal, "paragraph": render_paragraph}))
    rendered = MDRenderer().render(stream, {"parser_extension": plugins}, {}, finalize=False)
    if raw:
        return re.sub("|".join(map(re.escape, raw)), lambda match: raw[match[0]], rendered)
    return rendered


def formatted(node: Node) -> bool:
    return any(formatted(child) for child in node.children) if node.kind == "sequence" else node.kind != "text"
