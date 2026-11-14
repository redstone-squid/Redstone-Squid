"""Resolve source-referenced text, preserving provenance through composition."""

from bisect import bisect_right
from dataclasses import dataclass, replace
from typing import Literal

from squid_i18n import tree
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.semantics import CheckedAlternative, source_text
from squid_ui.presentations import SemanticRef, Shortening, TextPresentation
from squid_ui.text import Localization, resolve_text


@dataclass(frozen=True, slots=True)
class ResolvedFragment:
    source: SemanticRef | None
    nodes: tuple[tree.Node, ...]
    source_start: int
    source_end: int
    source_length: int

    @property
    def removed_chars(self) -> int:
        return self.source_length - (self.source_end - self.source_start)


@dataclass(frozen=True, slots=True)
class ResolvedText:
    presentation: SemanticRef
    fragments: tuple[ResolvedFragment, ...]

    @property
    def nodes(self) -> tuple[tree.Node, ...]:
        return tuple(node for fragment in self.fragments for node in fragment.nodes)


@dataclass(frozen=True, slots=True)
class UnsupportedText:
    """This candidate needs an inline transformation the core cannot yet preserve."""

    source: SemanticRef
    feature: str
    operation: Literal["shorten"] = "shorten"


_SHORTENABLE = frozenset({"text", "sequence", "inline", "strong", "em", "s", "code_inline", "softbreak", "hardbreak"})


def _unsupported(nodes: tuple[tree.Node, ...]) -> str | None:
    for node in nodes:
        if node.kind not in _SHORTENABLE:
            return node.kind
        if found := _unsupported(node.children):
            return found
    return None


def text_ends(nodes: tuple[tree.Node, ...]) -> tuple[int, ...]:
    """Legal visible-text cuts, keeping entity leaves and other opaque nodes intact."""
    ends: list[int] = []
    offset = 0
    for node in nodes:
        length = len(tree.plain(node))
        if node.kind in _SHORTENABLE | {"link"}:
            cuts = text_ends(node.children) if node.children else tuple(range(1, length + 1))
            ends.extend(offset + end for end in cuts)
        elif length:
            ends.append(offset + length)
        offset += length
    return tuple(ends)


def text_fragments(nodes: tuple[tree.Node, ...], size: int) -> tuple[tuple[tree.Node, ...], ...]:
    """Split near the requested size; an indivisible entity may exceed it."""
    ends = text_ends(nodes)
    if not ends:
        return (nodes,)
    fragments: list[tuple[tree.Node, ...]] = []
    start = 0
    while start < ends[-1]:
        index = bisect_right(ends, start + size) - 1
        end = ends[max(0, index)]
        if end <= start:
            end = ends[bisect_right(ends, start)]
        fragments.append(_slice(nodes, start, end))
        start = end
    return tuple(fragments)


def join_text(left: tuple[tree.Node, ...], right: tuple[tree.Node, ...]) -> tuple[tree.Node, ...]:
    """Rejoin adjacent pieces without introducing literal Markdown between styles."""
    if not left or not right:
        return (*left, *right)
    a, b = left[-1], right[0]
    if a.kind in _SHORTENABLE | {"link"} and replace(a, children=(), content="") == replace(b, children=(), content=""):
        if a.children and b.children:
            merged = replace(a, children=join_text(a.children, b.children))
        elif a.kind in ("text", "code_inline"):
            merged = replace(a, content=a.content + b.content)
        else:
            return (*left, *right)
        return (*left[:-1], merged, *right[1:])
    return (*left, *right)


def _slice(nodes: tuple[tree.Node, ...], start: int, end: int) -> tuple[tree.Node, ...]:
    """Slice the visible code points of supported inline text without dropping its styling."""
    result: list[tree.Node] = []
    offset = 0
    for node in nodes:
        length = len(tree.plain(node))
        left, right = max(0, start - offset), min(length, end - offset)
        if left < right:
            if left == 0 and right == length:
                result.append(node)
            elif node.children:
                result.append(replace(node, children=_slice(node.children, left, right)))
            else:
                result.append(replace(node, content=node.content[left:right]))
        offset += length
    return tuple(result)


def resolve_presentation_text[RenderTargetT, ContextT](
    checked: CheckedAlternative[RenderTargetT, ContextT],
    presentation: SemanticRef,
    *,
    localization: Localization,
) -> ResolvedText | UnsupportedText:
    """Resolve one checked presentation's fragments against its shared content.

    Shortening counts localized Unicode code points, excluding formatting syntax.
    Full references retain links and other structured text. Shortening that would
    require understanding rich inline behavior returns an unsupported candidate;
    it never flattens that behavior to plain text or declares the layout impossible.
    """
    presentation = checked.presentation.resolve(presentation)
    value = checked.presentation.units[presentation].value
    if not isinstance(value, TextPresentation):
        message = "text resolution requires a source-referenced TextPresentation"
        raise LayoutInvariantError(message)
    fragments: list[ResolvedFragment] = []
    for part in value.parts:
        if isinstance(part, SemanticRef | Shortening):
            reference = checked.source.resolve(part.source if isinstance(part, Shortening) else part)
            text = source_text(checked.source.units[reference].value)
        else:
            reference, text = None, part
        nodes = resolve_text(text, localization).nodes
        length = sum(len(tree.plain(node)) for node in nodes)
        start, end = 0, length
        if isinstance(part, Shortening) and length > part.max_chars:
            if feature := _unsupported(nodes):
                return UnsupportedText(checked.source.resolve(part.source), feature)
            start, end = (0, part.max_chars) if part.keep == "head" else (length - part.max_chars, length)
            nodes = _slice(nodes, start, end)
        fragments.append(ResolvedFragment(reference, nodes, start, end, length))
    return ResolvedText(presentation, tuple(fragments))
