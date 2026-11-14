"""Resolve source-referenced text, preserving provenance through composition."""

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
