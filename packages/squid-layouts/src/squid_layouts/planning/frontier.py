"""Reachable structural decisions in a primitive tree, and how they are priced.

A `Variants` ladder is a decision, not a shape: which rung is selected changes both what the
document contains and which nested ladders exist at all. This module owns that addressing —
positions keyed by the path through the *currently selected* rungs — so the planner's search
can canonicalize, price, and step decisions without re-deriving the traversal each time.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from squid_layouts.planning.degradation import DegradationEffect, DegradationProfile
from squid_layouts.planning.limits import V2Limits
from squid_layouts.planning.measure import (
    SolveNote,
    SolveNoteCode,
    _Builder,
    _component_count,
    _note,
    _prune,
)
from squid_layouts.primitives.nodes import Break, Budget, Node, Panel, Variants

type VariantPath = tuple[int | str, ...]
type Positions = Mapping[VariantPath, int]
type VariantTopology = tuple[tuple[str, int], ...]
"""Which rung each semantic fallback occurrence currently sits on; absent means rung 0."""


def format_path(path: VariantPath) -> str:
    """Render a ladder's path for a note. A reader's landmark, not an addressing scheme."""
    return "$." + ".".join(str(part) for part in path if part != "panel")


def walk_ladders(nodes: Sequence[Node], positions: Positions, visit) -> None:
    """Visit every node reachable through the currently selected rungs, in document order.

    Ladders only occur at the top level, inside a Panel, or inside another ladder's rung:
    `Section.texts`, `Row.items` and `ActionGroup.items` are typed to exclude them, so these
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
            case Panel(children=children) | Budget(children=children) | Break(children=children):
                for index, child in enumerate(children):
                    walk(child, (*path, "panel", index))
            case _:
                return

    for index, node in enumerate(nodes):
        walk(node, (index,))


def steppable(
    nodes: Sequence[Node],
    positions: Positions,
    *,
    locked_semantics: frozenset[str] = frozenset(),
) -> list[tuple[VariantPath, Variants, int]]:
    """Every reachable ladder that still has a rung left, in document order."""
    found: list[tuple[VariantPath, Variants, int]] = []

    def visit(path: VariantPath, node: Variants, rung: int) -> None:
        if node.semantic_path not in locked_semantics and rung + 1 < len(node.variants):
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
    """Price the selected rungs as author-granted structural loss."""
    profile = DegradationProfile()

    def visit(path: VariantPath, node: Variants, rung: int) -> None:
        nonlocal profile
        if rung:
            profile = profile.with_effect(
                DegradationEffect(
                    priority=node.priority,
                    path=format_path(path),
                    semantic_steps=rung,
                )
            )

    walk_ladders(nodes, positions, visit)
    return profile


def variant_notes(nodes: Sequence[Node], positions: Positions) -> list[SolveNote]:
    """One diagnostic per rung each reachable ladder has given up."""
    notes: list[SolveNote] = []

    def visit(path: VariantPath, node: Variants, rung: int) -> None:
        notes.extend(
            _note(
                SolveNoteCode.VARIANT_STEP,
                f"{format_path(path)} stepped to variant {step + 2} of {len(node.variants)} "
                f"(priority {node.priority}) under layout pressure",
            )
            for step in range(rung)
        )

    walk_ladders(nodes, positions, visit)
    return notes


def variant_state_bound(nodes: Sequence[Node], cutoff: int, topology: Mapping[str, int]) -> int:
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
                if node.semantic_path in topology:
                    rung = topology[node.semantic_path]
                    if not 0 <= rung < len(variants):
                        message = f"{node.semantic_path}: fallback topology selected unavailable rung {rung}"
                        raise ValueError(message)
                    return multiply([count_node(child) for child in variants[rung].nodes])
                total = 0
                for variant in variants:
                    total += multiply([count_node(child) for child in variant.nodes])
                    if total > cutoff:
                        return cutoff + 1
                return total
            case Panel(children=children) | Budget(children=children) | Break(children=children):
                return multiply([count_node(child) for child in children])
            case _:
                return 1

    return multiply([count_node(node) for node in nodes])


def static_components(nodes: Sequence[Node], limits: V2Limits) -> int:
    """Component cost of a rung's own subtree, with every nested ladder at rung 0."""
    builder = _Builder(limits=limits)
    return _component_count(_prune(builder.realize_children(resolve_variants(nodes, {}))))


def apply_semantic_topology(
    nodes: Sequence[Node], positions: Positions, topology: Mapping[str, int]
) -> dict[VariantPath, int]:
    """Force every currently reachable semantic ladder to its requested rung."""
    selected = dict(positions)

    def visit(path: VariantPath, node: Variants, _rung: int) -> None:
        if node.semantic_path is None:
            return
        rung = topology.get(node.semantic_path, 0)
        if not 0 <= rung < len(node.variants):
            message = f"{node.semantic_path}: fallback topology selected unavailable rung {rung}"
            raise ValueError(message)
        if rung:
            selected[path] = rung
        else:
            selected.pop(path, None)

    # Selecting one semantic rung can reveal another semantic ladder.
    while True:
        before = dict(selected)
        walk_ladders(nodes, selected, visit)
        selected = canonical_positions(nodes, selected)
        if selected == before:
            return selected


def semantic_topology(nodes: Sequence[Node], positions: Positions) -> VariantTopology:
    """Which rung each reachable semantic fallback occurrence currently sits on."""
    topology: list[tuple[str, int]] = []

    def visit(_path: VariantPath, node: Variants, rung: int) -> None:
        if node.semantic_path is not None:
            topology.append((node.semantic_path, rung))

    walk_ladders(nodes, positions, visit)
    return tuple(topology)


def guided_step(
    nodes: Sequence[Node],
    positions: Positions,
    limits: V2Limits,
    *,
    locked_semantics: frozenset[str] = frozenset(),
    topology: Mapping[str, int] | None = None,
) -> dict[VariantPath, int] | None:
    """Pick the one step a budget-starved product should take next.

    Breadth and priority still decide *which* ladders are eligible; among equals the step
    that frees the most components wins, and document order breaks the remaining tie. This
    keeps an intractable product linear in the budget instead of abandoning it.
    """
    remaining = steppable(nodes, positions, locked_semantics=locked_semantics)
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
        if topology is not None:
            neighbor = apply_semantic_topology(nodes, neighbor, topology)
        saved = static_components(ladder.variants[current + 1].nodes, limits) - static_components(
            ladder.variants[current].nodes, limits
        )
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
            case Budget(children=children) | Break(children=children):
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
