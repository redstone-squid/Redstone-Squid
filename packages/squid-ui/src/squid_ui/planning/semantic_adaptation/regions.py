"""Lower semantic regions and balanced region pagination."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from squid_ui.capabilities import Capability
from squid_ui.errors import UnsolvableLayoutError
from squid_ui.planning.breaking import BreakItem, balanced_breaks
from squid_ui.planning.cursors import MaterializedCursorRequest
from squid_ui.planning.identity import stable_fingerprint
from squid_ui.planning.layout_measurement.costing import measure_nodes
from squid_ui.planning.layout_measurement.text import split_text_node, text_total
from squid_ui.planning.limits import Axis, MessageLimits
from squid_ui.planning.semantic_adaptation.common import (
    _resolve,
)
from squid_ui.planning.semantic_adaptation.model import (
    LoweringContext as _Context,
)
from squid_ui.primitives.constraints import (
    Alt,
    Condense,
    Never,
)
from squid_ui.primitives.nodes import (
    Break,
    Budget,
    Card,
    CardFooter,
    CardText,
    Fidelity,
    Footer,
    Lines,
    Node,
    Panel,
    Text,
    Time,
    Variant,
    Variants,
    ZonedTime,
    card_text,
)
from squid_ui.primitives.nodes import (
    Code as PrimitiveCode,
)
from squid_ui.primitives.nodes import (
    Heading as PrimitiveHeading,
)
from squid_ui.scene.model import PlanEvent, PlanSeverity
from squid_ui.semantic import (
    BestEffort,
    LayoutNode,
    Paged,
)


@dataclass(frozen=True, slots=True)
class _Fragment(Card):
    """A card holding loose prose or fields rather than an authored region.

    Fragments fold into their neighbours; an `Article`, `Aside`, or `Figure` produces a plain
    `Card` and never does. Only this layer can tell the two apart — after lowering, both are
    just cards, and merging two authored regions into one embed would change the author's
    grouping rather than express it.
    """


def _fold(nodes: Sequence[Node], context: _Context) -> list[Node]:
    """Fold consecutive fragments into as few legal cards as possible.

    Loose prose has to live somewhere, and a classic message's only home for prose beside an
    explicit `Content` is an embed description. Consecutive prose therefore becomes one
    implicit card and the first suitable heading becomes its title — later headings stay in
    the description as formatted text, because an embed has one title.

    A fragment that would overflow a card's own shape opens a continuation card. Continuations
    are exact: everything the author wrote is still shown, in the same order.
    """
    limits = context.limits
    folded: list[Node] = []
    open_card: _Fragment | None = None

    def flush() -> None:
        nonlocal open_card
        if open_card is not None:
            folded.append(open_card)
            open_card = None

    for node in nodes:
        fragment = _as_fragment(node, open_card)
        if fragment is None:
            flush()
            folded.append(node)
            continue
        merged = None if open_card is None else _merge(open_card, fragment, limits)
        if merged is None:
            flush()
            open_card = fragment
        else:
            open_card = merged
    flush()
    return folded


def _as_fragment(node: Node, open_card: _Fragment | None) -> _Fragment | None:
    """This node as a foldable card fragment, or None if it stands on its own."""
    if isinstance(node, _Fragment):
        return node
    if isinstance(node, Footer):
        # A trailing note is what the footer slot is for. One anywhere else stays subtle
        # description text, because an embed has exactly one footer.
        if open_card is not None and open_card.footer is None:
            return _Fragment(footer=CardFooter(Text(node.content, overflow=node.overflow, priority=node.priority)))
        return _Fragment(children=(node,))
    if isinstance(node, PrimitiveHeading):
        # "Suitable" means it can actually be a title: it has to come before any body text,
        # or the description would read as though it began mid-sentence.
        leading = open_card is None or (open_card.title is None and not open_card.children)
        if leading:
            return _Fragment(title=Text(node.content, overflow=node.overflow, priority=node.priority))
        return _Fragment(children=(node,))
    if isinstance(node, Text | PrimitiveCode | Lines | Time | ZonedTime):
        return _Fragment(children=(node,))
    return None


def _merge(first: _Fragment, second: _Fragment, limits: MessageLimits) -> _Fragment | None:
    """Fold `second` into `first`, or None when the result would not be one legal embed."""
    embeds = limits.embeds
    if embeds is None or len(first.fields) + len(second.fields) > embeds.fields:
        return None
    for slot in ("title", "url", "footer", "author", "image", "thumbnail", "timestamp"):
        if getattr(first, slot) is not None and getattr(second, slot) is not None:
            return None
    if first.accent is not None and second.accent is not None and first.accent != second.accent:
        return None
    return _Fragment(
        children=(*first.children, *second.children),
        title=first.title if first.title is not None else second.title,
        url=first.url if first.url is not None else second.url,
        fields=(*first.fields, *second.fields),
        footer=first.footer if first.footer is not None else second.footer,
        author=first.author if first.author is not None else second.author,
        accent=first.accent if first.accent is not None else second.accent,
        image=first.image if first.image is not None else second.image,
        thumbnail=first.thumbnail if first.thumbnail is not None else second.thumbnail,
        timestamp=first.timestamp if first.timestamp is not None else second.timestamp,
    )


def _settle(nodes: Sequence[Node], context: _Context) -> list[Node]:
    """Close every still-open fragment. Folding leaves them open so a parent can absorb them."""
    return [_close(node, context) if isinstance(node, _Fragment) else node for node in nodes]


def _close(card: _Fragment, context: _Context) -> Node:
    """Settle a folded card, offering the reformatted alternative where one exists."""
    plain = Card(
        children=card.children,
        title=card.title,
        url=card.url,
        fields=card.fields,
        footer=card.footer,
        author=card.author,
        accent=card.accent,
        image=card.image,
        thumbnail=card.thumbnail,
        timestamp=card.timestamp,
    )
    if not card.fields:
        return plain
    # Real embed fields are exact. Spelling the same values as description lines keeps every
    # one of them but reformats the block, so the solver is told which is which rather than
    # inferring loss from a rung number.
    lines = Lines(
        tuple(
            Alt(
                f"**{_slot_text(field.name)}:** {_slot_text(field.value)}",
                priority=card_text(field.value).priority,
            )
            for field in card.fields
        ),
        overflow=Condense(),
    )
    reformatted = replace(plain, fields=(), children=(*plain.children, lines))
    return Variants((Variant((plain,)), Variant((reformatted,), fidelity=Fidelity.REFORMATTED)))


def _slot_text(value: CardText) -> str:
    return card_text(value).content


def _region(card: Card, body: Sequence[Node], context: _Context) -> Node:
    """One authored region as one card, absorbing whatever its body folded into.

    A region's body is already folded, so the common case is a single fragment that merges
    straight in. Anything the region cannot hold — a second image, a 26th field — stays a
    sibling card, which is a continuation rather than a loss.
    """
    fragment = _Fragment(
        title=card.title,
        url=card.url,
        footer=card.footer,
        author=card.author,
        accent=card.accent,
        image=card.image,
        thumbnail=card.thumbnail,
        timestamp=card.timestamp,
    )
    folded = _settle(_fold([fragment, *body], context), context)
    if len(folded) == 1:
        return folded[0]
    # A region that needs continuation cards is still one region: keeping them together stops
    # root pagination cutting between a heading and the rest of what it introduced.
    return Break(tuple(folded), keep_with_next=True)


def _cards(context: _Context) -> bool:
    """Whether this target draws regions as embeds rather than as container components."""
    return Capability.LAYOUT_EMBED in context.capabilities


@dataclass(frozen=True, slots=True)
class _RegionItem:
    nodes: tuple[Node, ...]
    keep_with_next: bool = False
    unbreakable: bool = False


def _region_items(nodes: Sequence[Node], *, keep_heading: bool) -> list[_RegionItem]:
    items: list[_RegionItem] = []
    for node in nodes:
        if isinstance(node, Break):
            items.append(_RegionItem(node.children, node.keep_with_next, node.unbreakable))
        else:
            items.append(_RegionItem((node,)))
    if keep_heading and len(items) > 1 and isinstance(items[0].nodes[0], PrimitiveHeading):
        items[0] = replace(items[0], keep_with_next=True)
    return items


def _split_oversized_region_items(
    items: Sequence[_RegionItem],
    *,
    chars: int,
    min_fill: int,
    widows: int,
    limits: MessageLimits,
    path: str,
) -> list[_RegionItem]:
    result: list[_RegionItem] = []
    for item in items:
        cost = measure_nodes(item.nodes, limits=limits)
        if text_total(cost) <= chars and cost.get(Axis.COMPONENTS) <= limits.component_budget:
            result.append(item)
            continue
        fragments = (
            None
            if item.unbreakable or len(item.nodes) != 1
            else split_text_node(item.nodes[0], chars, min_fill=min_fill, widows=widows)
        )
        if fragments is None or len(fragments) <= 1:
            message = (
                f"{path}: unbreakable region child {type(item.nodes[0]).__name__} needs "
                f"{text_total(cost)} characters and {cost.get(Axis.COMPONENTS)} components; "
                f"page limit is {chars} characters"
            )
            raise UnsolvableLayoutError(message)
        result.extend(
            _RegionItem((fragment,), keep_with_next=item.keep_with_next and index == len(fragments) - 1)
            for index, fragment in enumerate(fragments)
        )
    return result


def _break_region(
    items: Sequence[_RegionItem],
    *,
    chars: int,
    min_fill: int,
    widows: int,
    limits: MessageLimits,
    path: str,
) -> list[tuple[_RegionItem, ...]]:
    if not items:
        return [()]
    costs = [measure_nodes(item.nodes, limits=limits) for item in items]
    try:
        cuts = balanced_breaks(
            [
                BreakItem(
                    text_total(cost),
                    cost.get(Axis.COMPONENTS),
                    break_after=not item.keep_with_next,
                )
                for item, cost in zip(items, costs, strict=True)
            ],
            max_chars=chars,
            max_components=limits.component_budget,
            min_fill=min_fill,
            widows=widows,
            ideal_total=sum(text_total(cost) for cost in costs),
        )
    except ValueError as error:
        message = f"{path}: region has no feasible break set within its {chars}-character page budget"
        raise UnsolvableLayoutError(message) from error
    pages: list[tuple[_RegionItem, ...]] = []
    start = 0
    for end in cuts:
        pages.append(tuple(items[start:end]))
        start = end
    return pages


def _paged_region(
    node: Paged,
    path: str,
    context: _Context,
    lower_node: Callable[[LayoutNode, str, _Context], list[Node]],
    *,
    minimum: int,
    preferred: int,
    stretch: int,
) -> list[Node]:
    lowered = lower_node(node.node, path, context)
    shell: Panel | None = lowered[0] if len(lowered) == 1 and isinstance(lowered[0], Panel) else None
    children = shell.children if shell is not None else tuple(lowered)
    items = _region_items(children, keep_heading=shell is not None)
    items = _split_oversized_region_items(
        items,
        chars=node.chars,
        min_fill=node.min_fill,
        widows=node.widows,
        limits=context.limits,
        path=path,
    )
    pages = _break_region(
        items,
        chars=node.chars,
        min_fill=node.min_fill,
        widows=node.widows,
        limits=context.limits,
        path=path,
    )
    request = MaterializedCursorRequest(
        key=node.key,
        extent=len(pages),
        fingerprint=stable_fingerprint(children),
        initial=node.initial,
    )
    grant = context.pages.grant(request)
    context.pages.record(request, grant.position)
    selected = [primitive for item in pages[grant.position.offset] for primitive in item.nodes]
    budgeted = Budget(
        tuple(selected),
        minimum,
        preferred,
        stretch,
        best_effort=isinstance(node.node, BestEffort),
    )
    controls = context.pages.controls(node.key, grant.position, grant.extent)
    if controls and node.footer is not None:
        controls[0] = Footer(_resolve(node.footer(grant.position.offset + 1, grant.extent), context), overflow=Never())
    if grant.extent > 1:
        context.events.append(
            PlanEvent(
                code="pagination.region",
                path=path,
                message=f"Region {node.key!r} uses {grant.extent} balanced pages",
                severity=PlanSeverity.ADAPTATION,
                after={"pages": grant.extent},
            )
        )
    if shell is not None:
        return [replace(shell, children=(budgeted, *controls))]
    return [budgeted, *controls]
