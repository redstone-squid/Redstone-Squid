"""Reachable structural decisions in a primitive tree, and how they are priced.

A `Variants` ladder is a decision, not a shape: which rung is selected changes both what the
document contains and which nested ladders exist at all. This module owns that addressing —
positions keyed by the path through the *currently selected* rungs — so the planner's search
can canonicalize, price, and step decisions without re-deriving the traversal each time.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from squid_ui.planning.adapter import ResourceCost
from squid_ui.planning.degradation import DegradationEffect, DegradationProfile
from squid_ui.planning.layout_measurement.costing import component_count, prune
from squid_ui.planning.layout_measurement.diagnostics import (
    SolveNote,
    SolveNoteCode,
    SolveNoteSeverity,
    note,
)
from squid_ui.planning.layout_measurement.realization import Builder
from squid_ui.planning.limits import Axis, MessageLimits
from squid_ui.primitives.nodes import Break, Budget, Card, Fidelity, Node, Panel, Variants

type VariantPath = tuple[int | str, ...]
type Positions = Mapping[VariantPath, int]


def format_path(path: VariantPath) -> str:
    """Render a ladder's path for a note. A reader's landmark, not an addressing scheme."""
    return "$." + ".".join(str(part) for part in path if part != "panel")


def walk_ladders(nodes: Sequence[Node], positions: Positions, visit) -> None:
    """Visit every node reachable through the currently selected rungs, in document order.

    Ladders only occur at the top level, inside a Panel, or inside another ladder's rung:
    `Section.texts`, `Row.items` and `ControlGroup.items` are typed to exclude them, so these
    two recursive arms are exhaustive.
    """

    def walk(node: Node, path: VariantPath) -> None:
        match node:
            case Variants(variants=variants):
                rung = min(positions.get(path, 0), len(variants) - 1)
                visit(path, node, rung)
                # The rung is part of the descendants' path, so stepping this ladder abandons
                # their positions rather than reinterpreting them against a different subtree.
                for index, child in enumerate(variants[rung].nodes):
                    walk(child, (*path, rung, index))
            case (
                Panel(children=children)
                | Budget(children=children)
                | Break(children=children)
                | Card(children=children)
            ):
                for index, child in enumerate(children):
                    walk(child, (*path, "panel", index))
            case _:
                return

    for index, node in enumerate(nodes):
        walk(node, (index,))


def steppable(nodes: Sequence[Node], positions: Positions) -> list[tuple[VariantPath, Variants, int]]:
    """Every reachable ladder that still has a rung left, in document order."""
    found: list[tuple[VariantPath, Variants, int]] = []

    def visit(path: VariantPath, node: Variants, rung: int) -> None:
        if rung + 1 < len(node.variants):
            found.append((path, node, rung))

    walk_ladders(nodes, positions, visit)
    return found


def canonical_positions(nodes: Sequence[Node], positions: Positions) -> dict[VariantPath, int]:
    """Discard zero and unreachable positions after a parent changes rungs."""
    canonical: dict[VariantPath, int] = {}

    def visit(path: VariantPath, _node: Variants, rung: int) -> None:
        if rung:
            canonical[path] = rung

    walk_ladders(nodes, positions, visit)
    return canonical


def variant_profile(nodes: Sequence[Node], positions: Positions) -> DegradationProfile:
    """Price the selected rungs: fidelity as loss, distance as the tie beneath it.

    Two things are being ordered and they are not the same thing. A rung that says it
    reformats or discards content costs the reader something, and must lose to any faithful
    alternative however far down the ladder that alternative sits — that is what makes
    exact pagination beat a lossy one-pager. A rung's *distance* from rung 0 costs the
    reader nothing; it is only the author's stated preference, so it ranks below every real
    loss axis. It stays in the profile rather than moving to the cost vector because
    `Variants.priority` groups it, and priority has to keep steering which ladder gives way
    first even when every rung on offer is exact.
    """
    profile = DegradationProfile()

    def visit(path: VariantPath, node: Variants, rung: int) -> None:
        nonlocal profile
        if not rung:
            return
        fidelity = node.variants[rung].fidelity
        profile = profile.with_effect(
            DegradationEffect(
                priority=node.priority,
                path=format_path(path),
                semantic_steps=rung,
                reformatted_nodes=int(fidelity is Fidelity.REFORMATTED),
                lossy_nodes=int(fidelity is Fidelity.LOSSY),
            )
        )

    walk_ladders(nodes, positions, visit)
    return profile


def variant_notes(nodes: Sequence[Node], positions: Positions) -> list[SolveNote]:
    """One diagnostic per rung each reachable ladder has given up, priced by fidelity.

    An exact rung is reported as adaptation rather than degradation, so `strict=True` accepts
    a document that merely took a smaller faithful shape and still rejects one that
    reformatted or dropped anything.
    """
    notes: list[SolveNote] = []

    def visit(path: VariantPath, node: Variants, rung: int) -> None:
        for step in range(rung):
            fidelity = node.variants[step + 1].fidelity
            notes.append(
                note(
                    _FIDELITY_NOTES[fidelity],
                    f"{format_path(path)} stepped to {_FIDELITY_WORDS[fidelity]}variant "
                    f"{step + 2} of {len(node.variants)} (priority {node.priority}) under layout pressure",
                    _FIDELITY_SEVERITY[fidelity],
                )
            )

    walk_ladders(nodes, positions, visit)
    return notes


_FIDELITY_NOTES = {
    Fidelity.EXACT: SolveNoteCode.VARIANT_STEP,
    Fidelity.REFORMATTED: SolveNoteCode.VARIANT_REFORMATTED,
    Fidelity.LOSSY: SolveNoteCode.VARIANT_LOSSY,
}
_FIDELITY_WORDS = {Fidelity.EXACT: "", Fidelity.REFORMATTED: "reformatted ", Fidelity.LOSSY: "lossy "}
_FIDELITY_SEVERITY = {
    Fidelity.EXACT: SolveNoteSeverity.ADAPTATION,
    Fidelity.REFORMATTED: SolveNoteSeverity.DEGRADATION,
    Fidelity.LOSSY: SolveNoteSeverity.DEGRADATION,
}


def variant_state_bound(nodes: Sequence[Node], cutoff: int) -> int:
    """Count reachable rung assignments, stopping once a bounded search cannot exhaust them."""

    def multiply(values: Sequence[int]) -> int:
        product = 1
        for value in values:
            product *= value
            if product > cutoff:
                return cutoff + 1
        return product

    def count_node(node: Node) -> int:
        match node:
            case Variants(variants=variants):
                total = 0
                for variant in variants:
                    total += multiply([count_node(child) for child in variant.nodes])
                    if total > cutoff:
                        return cutoff + 1
                return total
            case (
                Panel(children=children)
                | Budget(children=children)
                | Break(children=children)
                | Card(children=children)
            ):
                return multiply([count_node(child) for child in children])
            case _:
                return 1

    return multiply([count_node(node) for node in nodes])


def static_cost(nodes: Sequence[Node], limits: MessageLimits) -> ResourceCost:
    """A rung's own resource cost, with every nested ladder left at rung 0."""
    builder = Builder(limits=limits)
    children = prune(builder.realize_children(resolve_variants(nodes, {})))
    text = dict(builder.raw_text_cost)
    for unit in builder.units:
        text[unit.axis] = text.get(unit.axis, 0) + unit.need
    return ResourceCost({**text, Axis.COMPONENTS: component_count(children)})


def guided_step(nodes: Sequence[Node], positions: Positions, limits: MessageLimits) -> dict[VariantPath, int] | None:
    """Pick the one step a budget-starved product should take next.

    Breadth and priority still decide *which* ladders are eligible; among equals the step
    that frees the most, summed over every axis it frees anything on, wins, and document
    order breaks the remaining tie. This keeps an intractable product linear in the budget
    instead of abandoning it.

    Summing across axes is a heuristic and is only used here, where the search has already
    given up its guarantee. Everywhere a decision is actually *made*, axes are compared one
    at a time and never traded against each other.
    """
    remaining = steppable(nodes, positions)
    if not remaining:
        return None
    priority, rung = min((ladder.priority, current) for _path, ladder, current in remaining)
    peers = [
        (path, ladder, current)
        for path, ladder, current in remaining
        if ladder.priority == priority and current == rung
    ]

    def candidate(
        order: int, path: VariantPath, ladder: Variants, current: int
    ) -> tuple[int, int, dict[VariantPath, int]]:
        neighbor = canonical_positions(nodes, {**positions, path: current + 1})
        before = static_cost(ladder.variants[current].nodes, limits)
        after = static_cost(ladder.variants[current + 1].nodes, limits)
        saved = sum(after.get(axis) - before.get(axis) for axis in {*before.axes, *after.axes})
        return saved, order, neighbor

    _saved, _order, selected = min(
        candidate(order, path, ladder, current) for order, (path, ladder, current) in enumerate(peers)
    )
    return selected


def resolve_variants(nodes: Sequence[Node], positions: Positions) -> list[Node]:
    """Splice each ladder's selected rung into its parent for one measuring pass."""

    def rewrite(node: Node, path: VariantPath) -> list[Node]:
        match node:
            case Variants(variants=variants):
                rung = min(positions.get(path, 0), len(variants) - 1)
                resolved: list[Node] = []
                for index, child in enumerate(variants[rung].nodes):
                    resolved.extend(rewrite(child, (*path, rung, index)))
                return resolved
            case Panel(children=children, accent=accent):
                inner: list[Node] = []
                for index, child in enumerate(children):
                    inner.extend(rewrite(child, (*path, "panel", index)))
                return [Panel(children=tuple(inner), accent=accent)]
            case Budget(children=children) | Break(children=children) | Card(children=children):
                inner = []
                for index, child in enumerate(children):
                    inner.extend(rewrite(child, (*path, "panel", index)))
                return [replace(node, children=tuple(inner))]
            case _:
                return [node]

    resolved: list[Node] = []
    for index, node in enumerate(nodes):
        resolved.extend(rewrite(node, (index,)))
    return resolved
