"""The classic-message dialect: content, embeds, and up to five action rows.

Everything a renderer will draw is decided here. By the time a `SceneClassicMessage` exists,
which prose became which card, which values became embed fields, which controls share a row,
and where the page boundaries fall are all settled — a renderer that had to infer any of that
would be making layout decisions with none of the planner's budget information.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from squid_layouts.chrome import Chrome
from squid_layouts.errors import LayoutInvariantError, UnsolvableLayoutError
from squid_layouts.planning.cursors import CursorCoordinator, MaterializedCursorRequest
from squid_layouts.planning.dialect import SceneBindings
from squid_layouts.planning.identity import stable_fingerprint
from squid_layouts.planning.limits import ClassicLimits
from squid_layouts.planning.measure import (
    MeasuredLayout,
    RCard,
    RContent,
    Realized,
    RGroup,
    RText,
    measure,
)
from squid_layouts.planning.navigation import PlannedNav, materialized_navigation_state
from squid_layouts.planning.target import TargetProfile
from squid_layouts.primitives.constraints import Never, Paginate
from squid_layouts.primitives.nodes import (
    ActionGroup,
    Boundary,
    Break,
    Budget,
    Button,
    Card,
    CardMedia,
    Content,
    Extension,
    Footer,
    Gallery,
    LinkButton,
    MediaCollection,
    Node,
    Panel,
    RawItem,
    RoutedButton,
    RoutedSelect,
    Row,
    Section,
    SelectMenu,
    Variant,
    Variants,
    card_text,
)
from squid_layouts.scene.model import (
    SceneButton,
    SceneClassicMessage,
    SceneClassicRow,
    SceneControl,
    SceneEmbed,
    SceneEmbedAuthor,
    SceneEmbedField,
    SceneEmbedFooter,
    SceneEmbedMedia,
    SceneExtension,
    SceneLink,
    SceneOption,
    SceneRoutedButton,
    SceneRoutedSelect,
    SceneSelect,
)
from squid_layouts.sources import Position
from squid_layouts.temporal import ZonedDateTime

BLOCK_JOIN = "\n\n"
"""How a card's description blocks are joined. One rule, so one card is one string."""


def _lower(nodes: Sequence[Node], target: TargetProfile, limits: ClassicLimits) -> tuple[Node, ...]:
    """Rewrite lowered nodes into classic shape: legal rows, one card per embed."""
    lowered: list[Node] = []
    for node in nodes:
        match node:
            case ActionGroup(items=items):
                lowered.extend(
                    Row(tuple(items[start : start + limits.row_buttons]))
                    for start in range(0, len(items), limits.row_buttons)
                )
            case Card(children=children):
                lowered.append(replace(node, children=_lower(children, target, limits)))
            case Budget(children=children) | Break(children=children):
                lowered.append(replace(node, children=_lower(children, target, limits)))
            case Extension(fallback=fallback):
                # No classic target registers an extension adapter, so a native item is
                # always its portable fallback here rather than silently reinterpreted.
                lowered.extend(_lower((fallback,), target, limits))
            case Variants(variants=variants, priority=priority):
                supported = [variant for variant in variants if variant.requires <= target.capabilities]
                if not supported:
                    message = "Variants has no variant supported by the selected target"
                    raise LayoutInvariantError(message)
                lowered.append(
                    Variants(
                        tuple(
                            Variant(_lower(variant.nodes, target, limits), fidelity=variant.fidelity)
                            for variant in supported
                        ),
                        priority,
                    )
                )
            case _:
                lowered.append(node)
    return tuple(lowered)


def _validate(nodes: Sequence[Node], limits: ClassicLimits) -> None:
    """Reject what a classic message cannot express, naming the node that cannot be drawn."""
    pager_keys: set[str] = set()
    contents = 0

    def fail(path: str, detail: str) -> None:
        message = f"{path}: {detail}"
        raise LayoutInvariantError(message)

    def slot(path: str, value: object, cap: int, what: str) -> None:
        """A direct value over its local cap is a planning error, not a silent trim."""
        if value is None:
            return
        node = card_text(value)  # type: ignore[arg-type]
        length = len(node.content.strip())
        if length > cap and isinstance(node.overflow, Never):
            fail(path, f"{what} is {length} characters; the cap is {cap}. Give it an explicit overflow policy")

    def walk(node: Node, path: str) -> None:
        nonlocal contents
        overflow = getattr(node, "overflow", None)
        if isinstance(overflow, Paginate):
            if overflow.key is None:
                fail(path, "Paginate requires an explicit key")
            if overflow.key in pager_keys:
                fail(path, f"duplicate pager key {overflow.key!r}")
            pager_keys.add(overflow.key)
        match node:
            case Boundary():
                fail(path, "Boundary must be expanded by a component mount before planning")
            case Panel() | Section() | Gallery() | MediaCollection():
                fail(
                    path,
                    f"{type(node).__name__} is a Components V2 structure with no classic form; "
                    "offer a classic rung in a Variants ladder or plan for the V2 target",
                )
            case Content():
                contents += 1
                if contents > 1:
                    fail(path, "a message has one content field; only one Content node is legal")
            case Card(title=title, fields=fields, footer=footer, author=author, children=children):
                slot(path, title, limits.embed_title, "embed title")
                if len(fields) > limits.embed_fields:
                    fail(path, f"card has {len(fields)} fields; the cap is {limits.embed_fields}")
                for index, entry in enumerate(fields):
                    slot(f"{path}.field.{index}", entry.name, limits.field_name, "field name")
                    slot(f"{path}.field.{index}", entry.value, limits.field_value, "field value")
                if footer is not None:
                    slot(f"{path}.footer", footer.text, limits.embed_footer, "embed footer")
                if author is not None:
                    slot(f"{path}.author", author.name, limits.embed_author, "embed author")
                for index, child in enumerate(children):
                    walk(child, f"{path}.{index}")
            case Button(label=label):
                if len(label) > limits.button_label:
                    fail(path, f"button label exceeds {limits.button_label}")
            case Row(items=items):
                if len(items) > limits.row_buttons:
                    fail(path, f"row has {len(items)} controls; maximum is {limits.row_buttons}")
                for index, item in enumerate(items):
                    if isinstance(item, Button):
                        walk(item, f"{path}.{index}")
            case SelectMenu(options=options, placeholder=placeholder, min_values=minimum, max_values=maximum) | (
                RoutedSelect(options=options, placeholder=placeholder, min_values=minimum, max_values=maximum)
            ):
                if not options:
                    fail(path, "select needs at least one option")
                if len(options) > limits.select_options:
                    fail(path, f"select has {len(options)} options; use an option-paging semantic node")
                if placeholder is not None and len(placeholder) > limits.select_placeholder:
                    fail(path, f"select placeholder exceeds {limits.select_placeholder}")
                if minimum < 0 or maximum < minimum or maximum > max(1, len(options)):
                    fail(path, "select value bounds are invalid")
                for index, option in enumerate(options):
                    if len(option.label) > limits.option_label:
                        fail(f"{path}.option.{index}", f"label exceeds {limits.option_label}")
                    if len(option.value) > limits.option_value:
                        fail(f"{path}.option.{index}", f"value exceeds {limits.option_value}")
                    if option.description is not None and len(option.description) > limits.option_description:
                        fail(f"{path}.option.{index}", f"description exceeds {limits.option_description}")
            case Budget(children=children) | Break(children=children):
                for index, child in enumerate(children):
                    walk(child, f"{path}.{index}")
            case Variants(variants=variants):
                for index, variant in enumerate(variants):
                    for child_index, child in enumerate(variant.nodes):
                        walk(child, f"{path}.variant.{index}.{child_index}")
            case _:
                return

    for index, node in enumerate(nodes):
        walk(node, f"$.{index}")


def _paginate(
    nodes: Sequence[Node],
    *,
    key: str,
    capacities: Mapping[str, int],
    limits: ClassicLimits,
    chrome: Chrome,
    nav: PlannedNav,
    broker: CursorCoordinator,
) -> tuple[MeasuredLayout, int]:
    """Pack cards and rows into the fewest legal pages, losslessly.

    Explicit `Content` is pinned: it is the message's one content field and every page has
    one, so repeating it unchanged is the only representation that does not invent or lose
    text. Everything else flows in source order, and each page is the longest legal prefix
    left once the navigation row has reserved its slot.
    """
    pinned = tuple(node for node in nodes if isinstance(node, Content))
    flowing = [node for node in nodes if not isinstance(node, Content)]
    maximum_pages = max(1, len(flowing))

    def measure_page(children: Sequence[Node], index: int, pages: int) -> MeasuredLayout:
        chrome_nodes: tuple[Node, ...] = (
            Card(children=(Footer(chrome.page_footer(index + 1, pages), overflow=Never()),)),
            *nav(materialized_navigation_state(key, Position(offset=index), pages, chrome)),
        )
        return measure((*pinned, *children, *chrome_nodes), limits=limits, chrome=chrome, strict=False, nav=nav)

    pages: list[tuple[Node, ...]] = []
    current: tuple[Node, ...] = ()
    for node in flowing:
        candidate = (*current, node)
        probe = measure_page(candidate, 0, maximum_pages)
        if current and (not probe.fits(capacities) or probe.overflowed):
            pages.append(current)
            current = (node,)
            probe = measure_page(current, 0, maximum_pages)
        else:
            current = candidate
        if not probe.fits(capacities):
            blown = ", ".join(f"{axis} {spent}/{capacity}" for axis, spent, capacity in probe.cost.over(capacities))
            message = (
                f"classic page {len(pages) + 1} cannot fit node {type(node).__name__} ({blown}); "
                "split it into continuation cards or give it a structural fallback"
            )
            raise UnsolvableLayoutError(message)
    if current or not pages:
        pages.append(current)

    request = MaterializedCursorRequest(key=key, extent=len(pages), fingerprint=stable_fingerprint(nodes))
    grant = broker.grant(request)
    broker.record(request, grant.position)
    index = grant.position.offset
    return measure_page(pages[index], index, grant.extent), grant.extent


@dataclass(slots=True)
class _ClassicConverter:
    """Assemble one exact classic message from an already-fitted realized tree."""

    bindings: SceneBindings
    content: str | None = None
    embeds: list[SceneEmbed] = None  # type: ignore[assignment]
    rows: list[SceneClassicRow] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.embeds = []
        self.rows = []

    def convert(self, children: Sequence[Realized], path: str = "") -> None:
        for index, child in enumerate(children):
            here = f"{path}{index}"
            match child:
                case RContent(slot=slot):
                    self.content = slot.content or None
                case RCard():
                    self.embeds.append(self._embed(child))
                case RGroup(children=inner):
                    self.convert(inner, f"{here}.")
                case Row(items=items):
                    self.rows.append(
                        SceneClassicRow(
                            tuple(
                                _as_control(self.bindings.control(item, f"{here}.{position}"), here)
                                for position, item in enumerate(items)
                            )
                        )
                    )
                case SelectMenu(options=options):
                    self.rows.append(
                        SceneClassicRow(
                            (
                                SceneSelect(
                                    options=tuple(_options(options)),
                                    action=self.bindings.action(child),
                                    placeholder=child.placeholder,
                                    min_values=child.min_values,
                                    max_values=child.max_values,
                                    disabled=child.disabled,
                                    policy=child.policy,
                                ),
                            )
                        )
                    )
                case RoutedSelect(options=options):
                    self.rows.append(
                        SceneClassicRow(
                            (
                                SceneRoutedSelect(
                                    options=tuple(_options(options)),
                                    route_id=child.route_id,
                                    placeholder=child.placeholder,
                                    min_values=child.min_values,
                                    max_values=child.max_values,
                                    disabled=child.disabled,
                                ),
                            )
                        )
                    )
                case LinkButton() | RoutedButton() | Button() | RawItem():
                    # A bare control at the root gets its own row rather than being merged
                    # with a neighbour: merging is a layout decision, and lowering made it.
                    self.rows.append(SceneClassicRow((_as_control(self.bindings.control(child, here), here),)))
                case _:
                    message = f"{here}: {type(child).__name__} cannot appear in a classic message"
                    raise LayoutInvariantError(message)

    def _embed(self, card: RCard) -> SceneEmbed:
        description = BLOCK_JOIN.join(text for block in card.blocks if (text := _block_text(block)))
        return SceneEmbed(
            title=_text(card.title),
            url=card.url,
            description=description.strip() or None,
            fields=tuple(
                SceneEmbedField(field.name.content, field.value.content, field.inline) for field in card.fields
            ),
            footer=(None if (footer := _text(card.footer)) is None else SceneEmbedFooter(footer, card.footer_icon)),
            author=(
                None
                if (author := _text(card.author)) is None
                else SceneEmbedAuthor(author, card.author_url, card.author_icon)
            ),
            colour=card.accent,
            image=_media(card.image),
            thumbnail=_media(card.thumbnail),
            timestamp=_timestamp(card.timestamp),
        )


def _block_text(block: Realized) -> str:
    match block:
        case RText(content=content, dropped=False):
            return content
        case RGroup(children=children):
            return BLOCK_JOIN.join(text for child in children if (text := _block_text(child)))
        case _:
            message = f"{type(block).__name__} cannot appear in a card description"
            raise LayoutInvariantError(message)


def _text(slot: RText | None) -> str | None:
    """An embed value, or None where it is empty. Discord trims these server-side anyway."""
    if slot is None or slot.dropped:
        return None
    return slot.content.strip() or None


def _media(media: CardMedia | None) -> SceneEmbedMedia | None:
    return None if media is None else SceneEmbedMedia(media.url, media.description)


def _timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, ZonedDateTime):
        return value.instant.isoformat()
    return value.isoformat()  # type: ignore[union-attr]


def _options(options: Sequence[object]) -> list[SceneOption]:
    return [
        SceneOption(option.label, option.value, option.description, option.default)  # type: ignore[attr-defined]
        for option in options
    ]


def _as_control(node: object, path: str) -> SceneControl:
    if not isinstance(node, SceneLink | SceneButton | SceneRoutedButton | SceneExtension):
        message = f"{path}: an action row cannot hold {type(node).__name__}"
        raise LayoutInvariantError(message)
    return node


class ClassicDialect:
    """Pre-Components-V2 message shape. Everything else about planning is shared."""

    def normalize(self, nodes: Sequence[Node], target: TargetProfile, limits: ClassicLimits) -> tuple[Node, ...]:
        return _lower(nodes, target, limits)

    def validate(self, nodes: Sequence[Node], limits: ClassicLimits) -> None:
        _validate(nodes, limits)

    def paginate(
        self,
        nodes: Sequence[Node],
        *,
        key: str,
        capacities: Mapping[str, int],
        limits: ClassicLimits,
        chrome: Chrome,
        nav: PlannedNav,
        broker: CursorCoordinator,
    ) -> tuple[MeasuredLayout, int]:
        return _paginate(nodes, key=key, capacities=capacities, limits=limits, chrome=chrome, nav=nav, broker=broker)

    def body(self, children: Sequence[Realized], bindings: SceneBindings) -> SceneClassicMessage:
        converter = _ClassicConverter(bindings)
        converter.convert(children)
        return SceneClassicMessage(
            content=converter.content,
            embeds=tuple(converter.embeds),
            rows=tuple(converter.rows),
        )


CLASSIC_DIALECT = ClassicDialect()
