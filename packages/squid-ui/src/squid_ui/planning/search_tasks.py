"""Queue states shared by representation and partition exploration."""

from collections.abc import Mapping
from dataclasses import dataclass

from squid_ui.planning.composition import CandidateComposition, OccurrenceId, RealizationOffer, SemanticOccurrence
from squid_ui.planning.fragments import FragmentTree


@dataclass(frozen=True, slots=True)
class AssignmentTask[RenderTargetT, ContextT, PayloadT]:
    pending: tuple[SemanticOccurrence[RenderTargetT, ContextT], ...]
    choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...] = ()
    probe: bool = False
    fraction: float = 1.0
    repair: Mapping[OccurrenceId, str] | None = None


@dataclass(frozen=True, slots=True)
class PartitionTask[RenderTargetT, ContextT, PayloadT]:
    assignment: int
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT]
    choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...]
    regions: tuple[FragmentTree[RenderTargetT, ContextT, PayloadT], ...]
    cuts: tuple[tuple[int, ...], ...]
    probe: bool = False
    expand_only: bool = False
    repair: bool = False
