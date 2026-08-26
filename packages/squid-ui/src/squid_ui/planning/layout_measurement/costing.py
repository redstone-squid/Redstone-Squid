"""Preferred resource measurement, pruning, and structural costs."""

from collections.abc import Sequence
from dataclasses import replace

from squid_ui.planning.adapter import ResourceCost
from squid_ui.planning.layout_measurement.model import (
    MeasuredCard,
    MeasuredContent,
    MeasuredGroup,
    MeasuredPanel,
    MeasuredSection,
    MeasuredText,
    Realized,
)
from squid_ui.planning.layout_measurement.realization import Builder
from squid_ui.planning.limits import LIMITS, Axis, DiscordLimits
from squid_ui.planning.navigation import NavNode
from squid_ui.primitives.nodes import (
    ActionGroup,
    Break,
    Budget,
    Card,
    EntitySelect,
    Gallery,
    MediaCollection,
    Node,
    Panel,
    RawItem,
    RoutedSelect,
    Row,
    SelectMenu,
    Sep,
    Thumbnail,
    Variants,
)


def measure_nodes(nodes: Sequence[Node], *, limits: DiscordLimits = LIMITS) -> ResourceCost:
    """Measure preferred cost per named axis, without applying any budget pressure."""

    def lower_shape(node: Node) -> list[Node]:
        match node:
            case ActionGroup(items=items):
                return [
                    Row(tuple(items[start : start + limits.components.row_buttons]))
                    for start in range(0, len(items), limits.components.row_buttons)
                ]
            case MediaCollection(items=items):
                return [
                    Gallery(tuple(items[start : start + limits.gallery_items]))
                    for start in range(0, len(items), limits.gallery_items)
                ]
            case (
                Panel(children=children)
                | Budget(children=children)
                | Break(children=children)
                | Card(children=children)
            ):
                return [replace(node, children=tuple(child for item in children for child in lower_shape(item)))]
            case Variants(variants=variants):
                return [child for item in variants[0].nodes for child in lower_shape(item)]
            case _:
                return [node]

    lowered = [child for node in nodes for child in lower_shape(node)]
    builder = Builder(limits=limits)
    children = builder.realize_children(lowered)
    text = dict(builder.raw_text_cost)
    for unit in builder.units:
        text[unit.axis] = text.get(unit.axis, 0) + unit.need
    return ResourceCost({**text, **structural_cost(children)})


def prune(children: list[Realized]) -> list[Realized]:
    """Remove dropped text and containers emptied by allocation."""
    pruned: list[Realized] = []
    for child in children:
        match child:
            case MeasuredText(dropped=True):
                continue
            case MeasuredCard(blocks=blocks):
                pruned.append(replace(child, blocks=prune(blocks)))
            case MeasuredContent(slot=slot) if slot.dropped:
                continue
            case MeasuredPanel(children=inner, accent=accent, spoiler=spoiler):
                kept = prune(inner)
                if kept:
                    pruned.append(MeasuredPanel(children=kept, accent=accent, spoiler=spoiler))
            case MeasuredGroup(children=inner):
                pruned.extend(prune(inner))
            case MeasuredSection(texts=texts, accessory=accessory):
                kept_texts = [slot for slot in texts if not slot.dropped]
                if kept_texts:
                    pruned.append(MeasuredSection(texts=kept_texts, accessory=accessory))
            case Gallery(items=()) | Row(items=()):
                continue
            case _:
                pruned.append(child)
    return pruned


def validated_nav(nodes: Sequence[NavNode]) -> list[Node]:
    """Validate that navigation contributes only component-bearing nodes."""
    for node in nodes:
        match node:
            case Row(items=items) if not any(isinstance(item, RawItem) and item.text_cost for item in items):
                continue
            case (
                SelectMenu() | RoutedSelect() | EntitySelect() | Sep() | Thumbnail() | Gallery() | RawItem(text_cost=0)
            ):
                continue
            case _:
                message = f"nav factories may only return component-bearing nodes, got {type(node).__name__}"
                raise ValueError(message)
    return list(nodes)


def _item_component_cost(item: object) -> int:
    return item.component_cost if isinstance(item, RawItem) else 1


def structural_cost(children: Sequence[Realized]) -> dict[str, int]:
    """Count every structural axis at once, whichever target budgets them."""
    totals = {Axis.COMPONENTS: 0, Axis.EMBEDS: 0, Axis.ROWS: 0, Axis.CONTROLS: 0}

    def walk(nodes: Sequence[Realized]) -> None:
        for child in nodes:
            match child:
                case MeasuredPanel(children=inner):
                    totals[Axis.COMPONENTS] += 1
                    walk(inner)
                case MeasuredGroup(children=inner):
                    walk(inner)
                case MeasuredCard(blocks=blocks):
                    totals[Axis.EMBEDS] += 1
                    totals[Axis.COMPONENTS] += 1
                    walk(blocks)
                case MeasuredContent():
                    continue
                case MeasuredSection(texts=texts, accessory=accessory):
                    totals[Axis.COMPONENTS] += 1 + len(texts) + _item_component_cost(accessory)
                case Row(items=items):
                    totals[Axis.COMPONENTS] += 1 + sum(_item_component_cost(item) for item in items)
                    totals[Axis.ROWS] += 1
                    totals[Axis.CONTROLS] += len(items)
                case SelectMenu() | RoutedSelect() | EntitySelect():
                    totals[Axis.COMPONENTS] += 2
                    totals[Axis.ROWS] += 1
                    totals[Axis.CONTROLS] += 1
                case RawItem(component_cost=component_cost):
                    totals[Axis.COMPONENTS] += component_cost
                case _:
                    totals[Axis.COMPONENTS] += 1

    walk(children)
    return totals


def component_count(children: Sequence[Realized]) -> int:
    """Count components in one realized V2 subtree."""
    return structural_cost(children)[Axis.COMPONENTS]
