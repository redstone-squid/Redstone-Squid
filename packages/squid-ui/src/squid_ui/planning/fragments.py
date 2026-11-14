"""Legal contiguous fragments of a selected composition.

Targets expose leaf units and continuation payloads. Flow offers expose child
boundaries. Nested regions remain one unit in their parent, while unbreakable
offers suppress all internal breaks. The selected presentation never changes
when a fragment is materialized.
"""

from dataclasses import dataclass, replace

from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.composition import (
    CandidateComposition,
    FragmentCoverage,
    OccurrenceId,
    Placement,
    PlanningContext,
)
from squid_ui.presentations import SemanticRef


@dataclass(frozen=True, slots=True)
class FragmentTree[RenderTargetT, ContextT, PayloadT]:
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT]
    children: tuple[FragmentTree[RenderTargetT, ContextT, PayloadT], ...]
    units: tuple[frozenset[SemanticRef], ...]
    legal_ends: tuple[int, ...]

    @property
    def extent(self) -> int:
        return 1 if self.candidate.offer.region is not None else len(self.units)


def fragment_tree[RenderTargetT, ContextT, PayloadT](
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT], context: PlanningContext
) -> FragmentTree[RenderTargetT, ContextT, PayloadT]:
    context.work.consume()
    offer = candidate.offer
    children = tuple(fragment_tree(child, context) for child in candidate.children)
    if offer.fragments is not None:
        if children or offer.flow:
            message = "leaf fragment contracts cannot also expose child breaks"
            raise LayoutInvariantError(message)
        return FragmentTree(candidate, children, offer.fragments.units, offer.fragments.legal_ends)
    if not offer.flow:
        return FragmentTree(candidate, children, (offer.occurrence.sources,), (1,))
    units: list[frozenset[SemanticRef]] = []
    ends: list[int] = []
    for child in children:
        context.work.consume()
        child_offer = child.candidate.offer
        if child_offer.occurrence.placement in (Placement.HEADER, Placement.FOOTER):
            continue
        offset = len(units)
        if child_offer.region is not None:
            units.append(child_offer.occurrence.sources)
            ends.append(len(units))
        else:
            units.extend(child.units)
            ends.extend(offset + end for end in child.legal_ends)
        if child_offer.keep_with_next and ends and ends[-1] == len(units):
            ends.pop()
    # keep_with_next at the end has no following content to keep.
    if units and (not ends or ends[-1] != len(units)):
        ends.append(len(units))
    return FragmentTree(candidate, children, tuple(units), tuple(ends))


def _repeat[RenderTargetT, ContextT, PayloadT](
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT],
    suffix: tuple[str, ...],
    context: PlanningContext,
) -> CandidateComposition[RenderTargetT, ContextT, PayloadT]:
    context.work.consume()
    occurrence = candidate.offer.occurrence
    children = tuple(_repeat(child, suffix, context) for child in candidate.children)
    repeated = replace(
        occurrence,
        identity=OccurrenceId((*occurrence.identity.path, *suffix)),
        fragment=FragmentCoverage(occurrence.identity, 0, 0, frozenset(), occurrence.sources),
    )
    return CandidateComposition(
        replace(candidate.offer, occurrence=repeated, children=tuple(child.offer.occurrence for child in children)),
        children,
    )


def slice_fragment[RenderTargetT, ContextT, PayloadT](
    node: FragmentTree[RenderTargetT, ContextT, PayloadT],
    start: int,
    end: int,
    context: PlanningContext,
) -> CandidateComposition[RenderTargetT, ContextT, PayloadT]:
    """Construct one legal fragment, with explicit placements and source coverage."""
    context.work.consume()
    if (
        not (0 <= start <= end <= len(node.units))
        or (start and start not in node.legal_ends)
        or (end and end not in node.legal_ends)
    ):
        message = "fragment must start and end at declared legal boundaries"
        raise LayoutInvariantError(message)
    if start == end and node.units:
        message = "a nonempty region cannot have an empty page"
        raise LayoutInvariantError(message)
    offer = node.candidate.offer
    occurrence = offer.occurrence
    suffix = ("@fragment", str(start), str(end))
    selected: list[CandidateComposition[RenderTargetT, ContextT, PayloadT]] = []
    payload = offer.payload
    repeated: frozenset[SemanticRef] = frozenset()
    if offer.fragments is not None:
        payload = offer.fragments.payload(start, end)
        repeated = offer.fragments.repeated
    elif offer.flow:
        offset = 0
        for child in node.children:
            context.work.consume()
            child_offer = child.candidate.offer
            if child_offer.occurrence.placement in (Placement.HEADER, Placement.FOOTER):
                selected.append(_repeat(child.candidate, suffix, context))
                continue
            child_end = offset + child.extent
            if start < child_end and end > offset:
                if child_offer.region is not None:
                    selected.append(child.candidate)
                else:
                    selected.append(
                        slice_fragment(child, max(0, start - offset), min(child.extent, end - offset), context)
                    )
            elif child.extent == 0 and start <= offset <= end:
                selected.append(slice_fragment(child, 0, 0, context))
            offset = child_end
    else:
        selected.extend(node.candidate.children)
    sources = frozenset(source for unit in node.units[start:end] for source in unit)
    placed = replace(
        occurrence,
        identity=OccurrenceId((*occurrence.identity.path, *suffix)),
        fragment=FragmentCoverage(occurrence.identity, start, end, sources, repeated),
    )
    return CandidateComposition(
        replace(
            offer,
            occurrence=placed,
            payload=payload,
            children=tuple(child.offer.occurrence for child in selected),
            region=None,
        ),
        tuple(selected),
    )
