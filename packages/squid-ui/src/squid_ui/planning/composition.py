"""Typed candidate composition shared by target realization rules.

This boundary evaluates an explicitly selected composition. Choosing offers and
certifying all reachable page combinations belongs to the joint solver.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from squid_ui.errors import DrawInvariantError, LayoutInvariantError, LayoutSearchExhaustedError
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.resources import EMPTY_COST, Axis, ResourceCost
from squid_ui.planning.semantics import SemanticGraph
from squid_ui.presentations import Budget, FidelityEffect, SemanticRef
from squid_ui.text import NEUTRAL, Localization


@dataclass(frozen=True, slots=True)
class OccurrenceId:
    """One placement, distinct from semantic identity and representation choice."""

    path: tuple[str, ...]


class Placement(StrEnum):
    CONTENT = "content"
    HEADER = "header"
    FOOTER = "footer"
    NAVIGATION = "navigation"


@dataclass(frozen=True, slots=True)
class SemanticOccurrence[RenderTargetT, ContextT]:
    identity: OccurrenceId
    graph: SemanticGraph[RenderTargetT, ContextT]
    reference: SemanticRef
    placement: Placement = Placement.CONTENT

    def __post_init__(self) -> None:
        if self.reference not in self.graph.units:
            message = "an occurrence needs a resolved reference in its semantic graph"
            raise LayoutInvariantError(message)


@dataclass(frozen=True, slots=True)
class RealizationOffer[RenderTargetT, ContextT, PayloadT]:
    """A target's declared representation, with its source structure still available.

    Payloads describe the selected representation; they cannot authorize hidden
    fitting. Children identify the occurrences this representation needs composed.
    The target must expose further structure before the core can split an atom.
    """

    occurrence: SemanticOccurrence[RenderTargetT, ContextT]
    key: str
    payload: PayloadT
    children: tuple[SemanticOccurrence[RenderTargetT, ContextT], ...] = ()
    effects: tuple[FidelityEffect, ...] = ()
    preference: int = 0


@dataclass(frozen=True, slots=True)
class CandidateComposition[RenderTargetT, ContextT, PayloadT]:
    offer: RealizationOffer[RenderTargetT, ContextT, PayloadT]
    children: tuple[CandidateComposition[RenderTargetT, ContextT, PayloadT], ...] = ()

    def __post_init__(self) -> None:
        expected = tuple(child.identity for child in self.offer.children)
        actual = tuple(child.offer.occurrence.identity for child in self.children)
        if actual != expected:
            message = "candidate children must realize the offer's declared occurrences in order"
            raise LayoutInvariantError(message)
        if any(
            child.offer.occurrence.graph is not expected_child.graph
            or child.offer.occurrence.reference != expected_child.reference
            or child.offer.occurrence.placement != expected_child.placement
            for child, expected_child in zip(self.children, self.offer.children, strict=True)
        ):
            message = "candidate children must retain their declared semantic source and placement"
            raise LayoutInvariantError(message)
        if len(set(actual)) != len(actual):
            message = "repeated content needs separate occurrence identities"
            raise LayoutInvariantError(message)


class WorkBudget:
    """One shared allowance for offers, evaluation, construction, and validation."""

    __slots__ = ("_limit", "_spent")

    def __init__(self, limit: int) -> None:
        if limit < 0:
            message = "planning work limit cannot be negative"
            raise LayoutInvariantError(message)
        self._limit = limit
        self._spent = 0

    @property
    def spent(self) -> int:
        return self._spent

    @property
    def remaining(self) -> int:
        return self._limit - self._spent

    def consume(self, amount: int = 1) -> None:
        if amount < 0:
            message = "planning operations cannot refund work"
            raise LayoutInvariantError(message)
        if amount > self.remaining:
            message = "shared composition work budget exhausted"
            raise LayoutSearchExhaustedError(message)
        self._spent += amount


@dataclass(frozen=True, slots=True)
class PlanningContext:
    work: WorkBudget
    localization: Localization = NEUTRAL
    strict: bool = True
    palette: Palette = DEFAULT_PALETTE


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    kind: str
    key: str


@dataclass(frozen=True, slots=True)
class SharedUse[ResourceT]:
    identity: ResourceIdentity
    value: ResourceT
    cost: ResourceCost


@dataclass(frozen=True, slots=True)
class SharedResources[ResourceT]:
    uses: Mapping[ResourceIdentity, SharedUse[ResourceT]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "uses", MappingProxyType(dict(self.uses)))

    @property
    def cost(self) -> ResourceCost:
        result = EMPTY_COST
        for use in self.uses.values():
            result += use.cost
        return result

    @classmethod
    def combine(cls, uses: Iterable[SharedUse[ResourceT]]) -> SharedResources[ResourceT]:
        combined: dict[ResourceIdentity, SharedUse[ResourceT]] = {}
        for use in uses:
            previous = combined.get(use.identity)
            if previous is not None and (previous.value != use.value or previous.cost != use.cost):
                message = f"conflicting declarations for shared resource {use.identity}"
                raise LayoutInvariantError(message)
            combined.setdefault(use.identity, use)
        return cls(combined)


@dataclass(frozen=True, slots=True)
class CapacityExceeded:
    occurrence: OccurrenceId
    axis: Axis
    used: int
    capacity: int


@dataclass(frozen=True, slots=True)
class UnsupportedCapability:
    occurrence: OccurrenceId
    feature: str


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    occurrence: OccurrenceId
    constraint: str


@dataclass(frozen=True, slots=True)
class LocalCapacityExceeded:
    occurrence: OccurrenceId
    limit: str
    used: int
    capacity: int


type CandidateViolation = CapacityExceeded | UnsupportedCapability | ConstraintViolation | LocalCapacityExceeded


@dataclass(frozen=True, slots=True)
class Evaluation[ResourceT, PreparationT = None]:
    """Facts about one composition, never a proof that every layout is infeasible.

    Additive cost excludes identified shared resources. Their costs are charged
    once after composing the complete visible tree, including repeated chrome.
    """

    additive_cost: ResourceCost = EMPTY_COST
    resources: SharedResources[ResourceT] = field(default_factory=SharedResources)
    violations: tuple[CandidateViolation, ...] = ()
    effects: tuple[FidelityEffect, ...] = ()
    interactions: frozenset[SemanticRef] = frozenset()
    preparation: PreparationT | None = None
    """Immutable target preparation; required for a feasible, selected candidate."""
    text: tuple[str, ...] = ()
    """Expected visible text in target traversal order, for independent construction checks."""

    resource_axes: frozenset[Axis] = frozenset()
    """Axes the target measures, including supported axes whose usage is zero."""
    scoped_costs: Mapping[OccurrenceId, ResourceCost] = field(default_factory=dict)
    """Subtree usage with shared resources deduplicated independently within each scope."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "scoped_costs", MappingProxyType(dict(self.scoped_costs)))

    @property
    def cost(self) -> ResourceCost:
        return self.additive_cost + self.resources.cost

    @property
    def feasible(self) -> bool:
        return not self.violations


class TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT = None](Protocol):
    def offers(
        self, occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
    ) -> tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], ...]: ...

    def evaluate(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT], context: PlanningContext
    ) -> Evaluation[ResourceT, PreparationT]: ...

    def build(self, preparation: PreparationT, context: PlanningContext) -> BodyT: ...

    def validate(
        self, body: BodyT, expected: Evaluation[ResourceT, PreparationT], context: PlanningContext
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class BuiltComposition[ResourceT, BodyT, PreparationT = None]:
    body: BodyT
    evaluation: Evaluation[ResourceT, PreparationT]


def build_composition[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT](
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT],
    rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
    context: PlanningContext,
) -> BuiltComposition[ResourceT, BodyT, PreparationT] | Evaluation[ResourceT, PreparationT]:
    """Evaluate and validate a fixed selection without publishing state or retrying it."""
    allowed: set[FidelityEffect] = set()
    pending = [candidate]
    seen: set[OccurrenceId] = set()
    budgets: dict[OccurrenceId, Mapping[Axis, int]] = {}
    while pending:
        context.work.consume()
        part = pending.pop()
        identity = part.offer.occurrence.identity
        if identity in seen:
            message = "composition occurrence identities must be globally unique"
            raise LayoutInvariantError(message)
        seen.add(identity)
        allowed.update(part.offer.effects)
        semantic = part.offer.occurrence.graph.units[part.offer.occurrence.reference].value
        if isinstance(semantic, Budget) and semantic.limits:
            budgets[identity] = semantic.limits
        pending.extend(part.children)
    evaluation = rules.evaluate(candidate, context)
    if set(evaluation.effects) - allowed:
        message = "target evaluation introduced an undeclared fidelity effect"
        raise DrawInvariantError(message)
    if context.strict and evaluation.effects:
        violation = ConstraintViolation(candidate.offer.occurrence.identity, "strict preservation")
        return replace(evaluation, violations=(*evaluation.violations, violation))
    if not evaluation.feasible:
        return evaluation
    violations: list[CandidateViolation] = []
    for identity, limits in budgets.items():
        unsupported = limits.keys() - evaluation.resource_axes
        violations.extend(
            UnsupportedCapability(identity, f"resource budget: {axis.value}") for axis in sorted(unsupported)
        )
        supported = {axis: capacity for axis, capacity in limits.items() if axis not in unsupported}
        if not supported:
            continue
        cost = evaluation.scoped_costs.get(identity)
        if cost is None:
            message = f"target evaluation did not measure resources for {identity.path!r}"
            raise DrawInvariantError(message)
        violations.extend(
            CapacityExceeded(identity, axis, used, capacity) for axis, used, capacity in cost.over(supported)
        )
    if violations:
        return replace(evaluation, violations=tuple(violations))
    if evaluation.preparation is None:
        message = "feasible target evaluation did not prepare construction"
        raise DrawInvariantError(message)
    context.work.consume()
    body = rules.build(evaluation.preparation, context)
    context.work.consume()
    rules.validate(body, evaluation, context)
    return BuiltComposition(body, evaluation)
