"""Text units, exact chrome costing, and lossless splitting."""

from dataclasses import dataclass, replace

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.breaking import BreakItem, balanced_breaks
from squid_layouts.planning.layout_measurement.model import RText
from squid_layouts.planning.limits import ELLIPSIS, TEXT_AXES, Axis
from squid_layouts.planning.target import ResourceCost
from squid_layouts.primitives.constraints import Never, Overflow
from squid_layouts.primitives.nodes import Code, Footer, Heading, Lines, Node, Text

type TextBearing = Text | Heading | Footer | Code | Lines


@dataclass(slots=True)
class TextUnit:
    """One text-bearing node's mutable allocation state."""

    node: TextBearing
    slot: RText
    index: int
    axis: str
    """Which message-wide text pool this unit draws from."""
    prefix: str
    suffix: str
    content: str
    ladders: tuple[tuple[str, ...], ...] | None
    ranks: tuple[int, ...]
    """One drop priority per ladder; empty for nodes that are not entry lists."""
    join: str
    priority: int
    overflow: Overflow
    grant: int = 0
    fragments: list[str] | None = None
    count_pages: list[str] | None = None
    """Count-based pages, when the node paginates every N entries rather than on overflow."""

    @property
    def chrome_len(self) -> int:
        return len(self.prefix) + len(self.suffix)

    @property
    def need(self) -> int:
        if self.count_pages is not None:
            return self.chrome_len + max(len(page) for page in self.count_pages)
        return self.chrome_len + len(self.content)


@dataclass(frozen=True, slots=True)
class BudgetRegion:
    units: tuple[TextUnit, ...]
    minimum: int
    preferred: int
    stretch: int
    best_effort: bool

    @property
    def axis(self) -> str | None:
        """The single text pool this region reserves from, or None if it holds no text."""
        axes = {unit.axis for unit in self.units}
        if len(axes) > 1:
            named = ", ".join(sorted(axes))
            message = f"a Budget region spans the text axes {named}; give each axis its own Budget"
            raise LayoutInvariantError(message)
        return next(iter(axes), None)


def _escape_fences(content: str) -> str:
    return content.replace("```", "``\N{ZERO WIDTH SPACE}`")


def make_unit(node: TextBearing, slot: RText, index: int, axis: str = Axis.DISPLAY_TEXT) -> TextUnit | None:
    """Create the allocation unit for one text-bearing primitive."""
    prefix, suffix, ladders, join = "", "", None, "\n"
    ranks: tuple[int, ...] = ()
    match node:
        case Text(content=content):
            pass
        case Heading(content=content, level=level):
            prefix = "#" * level + " "
        case Footer(content=content):
            prefix = "-# "
        case Code(content=content, lang=lang):
            prefix = f"```{lang}\n"
            suffix = "\n```"
            content = _escape_fences(content)
        case Lines(lines=raw_lines, join=join):
            entries = [
                ((entry,), 0) if isinstance(entry, str) else (entry.steps, entry.priority) for entry in raw_lines
            ]
            kept = [(ladder, rank) for ladder, rank in entries if ladder[0]]
            ladders = tuple(ladder for ladder, _ in kept)
            ranks = tuple(rank for _, rank in kept)
            content = join.join(ladder[0] for ladder in ladders)
    if not content:
        slot.dropped = True
        return None
    return TextUnit(
        node=node,
        slot=slot,
        index=index,
        axis=axis,
        prefix=prefix,
        suffix=suffix,
        content=content,
        ladders=ladders,
        ranks=ranks,
        join=join,
        priority=node.priority,
        overflow=node.overflow,
    )


def _hard_split(segment: str, limit: int) -> list[str]:
    return [segment[start : start + limit] for start in range(0, len(segment), limit)]


def split_pages(
    text: str,
    limit: int,
    boundary: str = "\n",
    *,
    min_fill: int = 0,
    widows: int = 1,
) -> list[str]:
    """Balance ``text`` across the fewest bounded pages, preferring semantic cuts."""
    if limit < 1:
        message = "page limit must be positive"
        raise ValueError(message)
    chunks: list[tuple[str, str]] = []
    for segment_index, segment in enumerate(text.split(boundary)):
        separator = boundary if segment_index else ""
        pieces = _hard_split(segment, limit) if len(segment) > limit else [segment]
        for piece_index, piece in enumerate(pieces):
            chunks.append((separator if piece_index == 0 else "", piece))
    if not chunks:
        return [""]

    def page_text(start: int, end: int) -> str:
        return chunks[start][1] + "".join(separator + content for separator, content in chunks[start + 1 : end])

    cuts = balanced_breaks(
        [BreakItem(len(content), leading_chars=len(separator)) for separator, content in chunks],
        max_chars=limit,
        min_fill=min_fill,
        widows=widows,
        ideal_total=len(text),
    )
    result: list[str] = []
    start = 0
    for end in cuts:
        result.append(page_text(start, end))
        start = end
    return result or [""]


def trim_keep(text: str, limit: int, keep: str) -> str:
    """Trim text to a limit while preserving the requested end."""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return ELLIPSIS if limit == 1 else ""
    if keep == "tail":
        return ELLIPSIS + text[-(limit - 1) :].lstrip()
    return text[: limit - 1].rstrip() + ELLIPSIS


def text_total(cost: ResourceCost) -> int:
    """Every text axis a cost spends on, added up."""
    return sum(value for axis, value in cost.values.items() if axis in TEXT_AXES)


def split_text_node(
    node: Node,
    limit: int,
    *,
    min_fill: int = 0,
    widows: int = 1,
) -> tuple[Node, ...] | None:
    """Losslessly split one text primitive into independently renderable fragments."""
    if not isinstance(node, Text | Heading | Footer | Code | Lines):
        return None
    slot = RText()
    unit = make_unit(node, slot, 0)
    if unit is None:
        return (node,)
    usable = limit - unit.chrome_len
    if usable < 1:
        return None
    boundary = node.join if isinstance(node, Lines) else "\n"
    fragments = split_pages(unit.content, usable, boundary, min_fill=min_fill, widows=widows)
    if len(fragments) <= 1:
        return (node,)
    if isinstance(node, Lines):
        return tuple(Text(fragment, overflow=Never(), priority=node.priority) for fragment in fragments)
    return tuple(replace(node, content=fragment, overflow=Never()) for fragment in fragments)
