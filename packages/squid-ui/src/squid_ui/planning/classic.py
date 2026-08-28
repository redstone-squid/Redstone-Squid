"""The classic-message dialect: content, embeds, and up to five action rows.

Everything a renderer will draw is decided here. By the time a `ClassicMessage` exists,
which prose became which card, which values became embed fields, which controls share a row,
and where the page boundaries fall are all settled — a renderer that had to infer any of that
would be making layout decisions with none of the planner's budget information.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from squid_ui import scene
from squid_ui.capabilities import Capability
from squid_ui.chrome import Chrome
from squid_ui.errors import LayoutInvariantError, UnsolvableLayoutError
from squid_ui.planning.control_validation import fail, register_pager, validate_component
from squid_ui.planning.cursors import CursorCoordinator, MaterializedCursorRequest
from squid_ui.planning.discord_dialect import SceneBindings
from squid_ui.planning.identity import stable_fingerprint
from squid_ui.planning.layout_measurement.model import (
    MeasuredCard,
    MeasuredContent,
    MeasuredGroup,
    MeasuredText,
    Realized,
)
from squid_ui.planning.layout_measurement.solver import (
    MeasuredLayout,
    measure,
)
from squid_ui.planning.limits import CLASSIC_LIMITS, Axis, ClassicLimits
from squid_ui.planning.navigation import PlannedNav, materialized_navigation_state
from squid_ui.planning.resolved import emoji as resolved_emoji
from squid_ui.planning.resolved import optional_text as resolved_optional_text
from squid_ui.planning.resolved import text as resolved_text
from squid_ui.planning.target import Target
from squid_ui.primitives.constraints import Never
from squid_ui.primitives.nodes import (
    Break,
    Budget,
    Button,
    Card,
    CardMedia,
    CardText,
    Content,
    ControlGroup,
    EntitySelect,
    Extension,
    File,
    Footer,
    Gallery,
    LinkButton,
    MediaCollection,
    Node,
    Option,
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
from squid_ui.sources import Position
from squid_ui.target_types import ClassicTarget
from squid_ui.temporal import ZonedDateTime

BLOCK_JOIN = "\n\n"
"""How a card's description blocks are joined. One rule, so one card is one string."""


def _lower(nodes: Sequence[Node], target: Target, limits: ClassicLimits) -> tuple[Node, ...]:
    lowered: list[Node] = []
    for node in nodes:
        match node:
            case ControlGroup(items=items):
                lowered.extend(
                    Row(tuple(items[start : start + limits.components.row_buttons]))
                    for start in range(0, len(items), limits.components.row_buttons)
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

    def slot(path: str, value: CardText | None, cap: int, what: str) -> None:
        """A direct value over its local cap is a planning error, not a silent trim."""
        if value is None:
            return
        node = card_text(value)
        length = len(resolved_text(node.content).strip())
        if length > cap and isinstance(node.overflow, Never):
            fail(path, f"{what} is {length} characters; the cap is {cap}. Give it an explicit overflow policy")

    def walk(node: Node, path: str) -> None:
        nonlocal contents
        register_pager(node, path, pager_keys)
        match node:
            case File() | Panel() | Section() | Gallery() | MediaCollection():
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
                slot(path, title, limits.embeds.title, "embed title")
                if len(fields) > limits.embeds.fields:
                    fail(path, f"card has {len(fields)} fields; the cap is {limits.embeds.fields}")
                for index, entry in enumerate(fields):
                    slot(f"{path}.field.{index}", entry.name, limits.embeds.field_name, "field name")
                    slot(f"{path}.field.{index}", entry.value, limits.embeds.field_value, "field value")
                if footer is not None:
                    slot(f"{path}.footer", footer.text, limits.embeds.footer, "embed footer")
                if author is not None:
                    slot(f"{path}.author", author.name, limits.embeds.author, "embed author")
                for index, child in enumerate(children):
                    walk(child, f"{path}.{index}")
            case _:
                validate_component(node, path, limits=limits, walk=walk)

    for index, node in enumerate(nodes):
        walk(node, f"$.{index}")


def _paginate(
    nodes: Sequence[Node],
    *,
    key: str,
    capacities: Mapping[Axis, int],
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
    embeds: list[scene.Embed] = field(default_factory=list)
    rows: list[scene.ClassicRow] = field(default_factory=list)

    def convert(self, children: Sequence[Realized], path: str = "") -> None:
        for index, child in enumerate(children):
            here = f"{path}{index}"
            match child:
                case MeasuredContent(slot=slot):
                    self.content = slot.content or None
                case MeasuredCard():
                    self.embeds.append(self._embed(child))
                case MeasuredGroup(children=inner):
                    self.convert(inner, f"{here}.")
                case Row(items=items):
                    self.rows.append(
                        scene.ClassicRow(
                            tuple(
                                _as_control(self.bindings.control(item, f"{here}.{position}"), here)
                                for position, item in enumerate(items)
                            )
                        )
                    )
                case SelectMenu(options=options):
                    self.rows.append(
                        scene.ClassicRow(
                            (
                                scene.Select(
                                    options=tuple(_options(options)),
                                    action=self.bindings.action(child),
                                    placeholder=resolved_optional_text(child.placeholder),
                                    min_values=child.min_values,
                                    max_values=child.max_values,
                                    disabled=child.disabled,
                                    mode=child.mode,
                                ),
                            )
                        )
                    )
                case EntitySelect():
                    self.rows.append(
                        scene.ClassicRow(
                            (
                                scene.EntitySelect(
                                    entity_type=child.entity_type,
                                    action=self.bindings.action(child),
                                    placeholder=resolved_optional_text(child.placeholder),
                                    default_values=child.default_values,
                                    channel_types=child.channel_types,
                                    min_values=child.min_values,
                                    max_values=child.max_values,
                                    disabled=child.disabled,
                                    mode=child.mode,
                                ),
                            )
                        )
                    )
                case RoutedSelect(options=options):
                    self.rows.append(
                        scene.ClassicRow(
                            (
                                scene.RoutedSelect(
                                    options=tuple(_options(options)),
                                    route_id=child.route_id,
                                    placeholder=resolved_optional_text(child.placeholder),
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
                    self.rows.append(scene.ClassicRow((_as_control(self.bindings.control(child, here), here),)))
                case _:
                    message = f"{here}: {type(child).__name__} cannot appear in a classic message"
                    raise LayoutInvariantError(message)

    def _embed(self, card: MeasuredCard) -> scene.Embed:
        description = BLOCK_JOIN.join(text for block in card.blocks if (text := _block_text(block)))
        return scene.Embed(
            title=_text(card.title),
            url=card.url,
            description=description.strip() or None,
            fields=tuple(
                scene.EmbedField(field.name.content, field.value.content, field.inline) for field in card.fields
            ),
            footer=(None if (footer := _text(card.footer)) is None else scene.EmbedFooter(footer, card.footer_icon)),
            author=(
                None
                if (author := _text(card.author)) is None
                else scene.EmbedAuthor(author, card.author_url, card.author_icon)
            ),
            colour=card.accent,
            image=_media(card.image),
            thumbnail=_media(card.thumbnail),
            timestamp=_timestamp(card.timestamp),
        )


def _block_text(block: Realized) -> str:
    match block:
        case MeasuredText(content=content, dropped=False):
            return content
        case MeasuredGroup(children=children):
            return BLOCK_JOIN.join(text for child in children if (text := _block_text(child)))
        case _:
            message = f"{type(block).__name__} cannot appear in a card description"
            raise LayoutInvariantError(message)


def _text(slot: MeasuredText | None) -> str | None:
    """An embed value, or None where it is empty. Discord trims these server-side anyway."""
    if slot is None or slot.dropped:
        return None
    return slot.content.strip() or None


def _media(media: CardMedia | None) -> scene.EmbedMedia | None:
    return None if media is None else scene.EmbedMedia(media.url, resolved_optional_text(media.description))


def _timestamp(value: ZonedDateTime | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, ZonedDateTime):
        return value.instant.isoformat()
    return value.isoformat()


def _options(options: Sequence[Option]) -> list[scene.Option]:
    return [
        scene.Option(
            resolved_text(option.label),
            option.value,
            resolved_optional_text(option.description),
            option.default,
            resolved_emoji(option.emoji),
        )
        for option in options
    ]


def _as_control(node: scene.Node, path: str) -> scene.Control:
    if not isinstance(node, scene.Link | scene.PremiumButton | scene.Button | scene.RoutedButton | scene.Extension):
        message = f"{path}: an action row cannot hold {type(node).__name__}"
        raise LayoutInvariantError(message)
    return node


class ClassicDialect:
    """Pre-Components-V2 message shape. Everything else about planning is shared."""

    id = "discord.components-v1"
    version = 1
    capabilities = frozenset(
        {
            Capability.ACTIONS_BUTTONS,
            Capability.ACTIONS_DISCORD_PREMIUM,
            Capability.ACTIONS_SELECT,
            Capability.ACTIONS_DISCORD_ENTITY,
            Capability.FORMS_MODAL,
            Capability.FORMS_DISCORD_CHECKBOX_GROUP,
            Capability.LAYOUT_EMBED,
            Capability.LAYOUT_EMBED_FIELDS,
            Capability.MESSAGE_CONTENT,
        }
    )
    render_target = ClassicTarget
    body_type = scene.ClassicMessage
    default_limits = CLASSIC_LIMITS
    # A classic message has no component that can hold a native discord.py item.
    realizes_extensions = False

    @property
    def planner(self) -> Any:
        from squid_ui.planning.discord_planner import DISCORD_PLANNER

        return DISCORD_PLANNER

    def normalize(
        self, nodes: Sequence[Node], target: Target[ClassicLimits, scene.ClassicMessage, ClassicTarget, Any]
    ) -> tuple[Node, ...]:
        return _lower(nodes, target, target.limits)

    def validate(self, nodes: Sequence[Node], limits: ClassicLimits) -> None:
        _validate(nodes, limits)

    def paginate(
        self,
        nodes: Sequence[Node],
        *,
        key: str,
        capacities: Mapping[Axis, int],
        limits: ClassicLimits,
        chrome: Chrome,
        nav: PlannedNav,
        broker: CursorCoordinator,
    ) -> tuple[MeasuredLayout, int]:
        return _paginate(nodes, key=key, capacities=capacities, limits=limits, chrome=chrome, nav=nav, broker=broker)

    def body(self, children: Sequence[Realized], bindings: SceneBindings) -> scene.ClassicMessage:
        converter = _ClassicConverter(bindings)
        converter.convert(children)
        return scene.ClassicMessage(
            content=converter.content,
            embeds=tuple(converter.embeds),
            rows=tuple(converter.rows),
        )


CLASSIC_DIALECT = ClassicDialect()
