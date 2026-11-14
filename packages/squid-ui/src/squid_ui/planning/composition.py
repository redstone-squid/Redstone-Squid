"""Typed candidate composition shared by target realization rules.

This boundary evaluates an explicitly selected composition. Choosing offers and
certifying all reachable page combinations belongs to the joint solver.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.errors import DrawInvariantError, LayoutInvariantError, LayoutSearchExhaustedError
from squid_ui.forms import FormBinding
from squid_ui.interactions import ActionBinding, PressEvent
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.resources import EMPTY_COST, Axis, ResourceCost
from squid_ui.planning.resources import ResourceIdentity as ResourceIdentity
from squid_ui.planning.semantics import SemanticGraph, SourceScope
from squid_ui.presentations import AtomicNode, Budget, FidelityEffect, SemanticRef
from squid_ui.semantic import Budgeted
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
class FragmentCoverage:
    logical: OccurrenceId
    start: int
    end: int
    sources: frozenset[SemanticRef]
    repeated: frozenset[SemanticRef] = frozenset()


@dataclass(frozen=True, slots=True)
class NavigationInput:
    region: OccurrenceId
    route: str
    page: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class FragmentContract[PayloadT]:
    """Ordered source units and a pure constructor for an exact contiguous fragment.

    The target declares legal ends, including the final end. Repeated sources are
    continuation chrome (for example table headings), never ordinary content.
    """

    units: tuple[frozenset[SemanticRef], ...]
    legal_ends: tuple[int, ...]
    payload: Callable[[int, int], PayloadT]
    repeated: frozenset[SemanticRef] = frozenset()

    def __post_init__(self) -> None:
        if (
            not self.units
            or self.legal_ends != tuple(sorted(set(self.legal_ends)))
            or (not self.legal_ends or self.legal_ends[0] < 1 or self.legal_ends[-1] != len(self.units))
        ):
            message = "fragment contract needs ordered legal ends covering every unit"
            raise LayoutInvariantError(message)
        seen: set[SemanticRef] = set()
        for unit in self.units:
            if not unit or seen.intersection(unit) or self.repeated.intersection(unit):
                message = "ordinary fragment sources must be nonempty, disjoint, and separate from continuation sources"
                raise LayoutInvariantError(message)
            seen.update(unit)


@dataclass(frozen=True, slots=True)
class RegionContract[RenderTargetT, ContextT, PayloadT]:
    """Target-declared pagination chrome, composed and charged on every visible page."""

    key: str
    navigation: Callable[
        [int, int, PlanningContext], tuple[CandidateComposition[RenderTargetT, ContextT, PayloadT], ...]
    ]
    chars: int | None = None
    min_fill: int = 0
    widows: int = 1


@dataclass(frozen=True, slots=True)
class SemanticOccurrence[RenderTargetT, ContextT]:
    identity: OccurrenceId
    graph: SemanticGraph[RenderTargetT, ContextT]
    reference: SemanticRef
    placement: Placement = Placement.CONTENT
    origin: SourceScope[RenderTargetT, ContextT] | None = None
    fragment: FragmentCoverage | None = None

    def __post_init__(self) -> None:
        if self.reference not in self.graph.units:
            message = "an occurrence needs a resolved reference in its semantic graph"
            raise LayoutInvariantError(message)

    @property
    def sources(self) -> frozenset[SemanticRef]:
        if self.fragment is not None:
            return self.fragment.sources | self.fragment.repeated
        return self.origin.references(self.reference) if self.origin is not None else self.graph.atoms(self.reference)

    def scope_effects(self, effects: tuple[FidelityEffect, ...]) -> tuple[FidelityEffect, ...]:
        """Resolve an extension's local effect references into the shared source graph."""
        scoped: list[FidelityEffect] = []
        for effect in effects:
            reference = (
                self.reference if effect.source.keys == self.reference.keys[-1:] else self.graph.resolve(effect.source)
            )
            sources = self.origin.references(reference) if self.origin is not None else frozenset({reference})
            scoped.extend(replace(effect, source=source) for source in sorted(sources, key=lambda ref: ref.keys))
        return tuple(scoped)


@dataclass(frozen=True, slots=True)
class AtomicContract:
    """Declared costs and legal placements for one opaque realization.

    Shared costs identify reusable resources; their live values come from evaluation.
    The complete target must still validate the prepared native output independently.
    """

    cost: ResourceCost = EMPTY_COST
    shared_costs: Mapping[ResourceIdentity, ResourceCost] = field(default_factory=dict)
    capabilities: frozenset[str] = frozenset()
    placements: frozenset[Placement] = frozenset(Placement)

    def __post_init__(self) -> None:
        object.__setattr__(self, "shared_costs", MappingProxyType(dict(self.shared_costs)))

    @property
    def total(self) -> ResourceCost:
        result = self.cost
        for cost in self.shared_costs.values():
            result += cost
        return result


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
    interaction: bool = False
    binding: ActionBinding[PressEvent[ContextT]] | None = None
    form: FormBinding[ContextT] | None = None
    atomic: AtomicContract | None = None
    flow: bool = False
    """Allow contiguous fragments of the declared children, preserving this shell."""
    keep_with_next: bool = False
    fragments: FragmentContract[PayloadT] | None = None
    region: RegionContract[RenderTargetT, ContextT, PayloadT] | None = None
    navigation: NavigationInput | None = None


class DiscoveryCompletion(StrEnum):
    COMPLETE = "complete"
    UNSUPPORTED = "unsupported"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class OfferDiscovery[RenderTargetT, ContextT, PayloadT]:
    """Explicit completeness for extensions; a complete empty domain is infeasible.

    Existing tuple-returning rules declare complete nonempty domains and use an
    empty tuple for unsupported vocabulary. Interrupted discovery must never look
    like a complete empty domain.
    """

    offers: tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], ...]
    completion: DiscoveryCompletion = DiscoveryCompletion.COMPLETE


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
            or child.offer.occurrence.origin is not expected_child.origin
            or child.offer.occurrence.fragment != expected_child.fragment
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
    def limit(self) -> int:
        return self._limit

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
    capabilities: frozenset[str] = frozenset()
    capacities: Mapping[Axis, int] = field(default_factory=dict)
    chrome: Chrome = DEFAULT_CHROME


@dataclass(frozen=True, slots=True)
class SharedUse[ResourceT]:
    identity: ResourceIdentity
    value: ResourceT
    cost: ResourceCost


class SharedResourceConflictError(LayoutInvariantError):
    """Two declarations cannot coexist in one visible resource scope."""

    def __init__(self, identity: ResourceIdentity) -> None:
        self.identity = identity
        super().__init__(f"conflicting declarations for shared resource {identity}")


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
                raise SharedResourceConflictError(use.identity)
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

    scoped_resources: Mapping[OccurrenceId, frozenset[ResourceIdentity]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scoped_costs", MappingProxyType(dict(self.scoped_costs)))
        object.__setattr__(self, "scoped_resources", MappingProxyType(dict(self.scoped_resources)))

    @property
    def cost(self) -> ResourceCost:
        return self.additive_cost + self.resources.cost

    @property
    def feasible(self) -> bool:
        return not self.violations


class TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT = None](Protocol):
    def offers(
        self, occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
    ) -> (
        tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], ...]
        | OfferDiscovery[RenderTargetT, ContextT, PayloadT]
    ): ...

    def evaluate(
        self, candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT], context: PlanningContext
    ) -> Evaluation[ResourceT, PreparationT]: ...

    def build(self, preparation: PreparationT, context: PlanningContext) -> BodyT: ...

    def validate(
        self, body: BodyT, expected: Evaluation[ResourceT, PreparationT], context: PlanningContext
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class BuiltComposition[ResourceT, BodyT, PreparationT = None, ContextT = object]:
    body: BodyT
    evaluation: Evaluation[ResourceT, PreparationT]
    bindings: Mapping[str, ActionBinding[PressEvent[ContextT]]] = field(default_factory=dict)
    forms: Mapping[str, FormBinding[ContextT]] = field(default_factory=dict)


def evaluate_composition[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT](
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT],
    rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
    context: PlanningContext,
) -> AdmittedComposition[ResourceT, PreparationT, ContextT] | Evaluation[ResourceT, PreparationT]:
    """Admit and prepare a selection, retaining all shared policy checks."""
    allowed: set[FidelityEffect] = set()
    pending = [candidate]
    seen: set[OccurrenceId] = set()
    budgets: dict[OccurrenceId, Mapping[Axis, int]] = {}
    floors: dict[OccurrenceId, int] = {}
    bindings: dict[str, ActionBinding[PressEvent[ContextT]]] = {}
    forms: dict[str, FormBinding[ContextT]] = {}
    interactions: set[SemanticRef] = set()
    atoms: dict[OccurrenceId, AtomicContract] = {}
    preflight: list[CandidateViolation] = []
    while pending:
        context.work.consume()
        part = pending.pop()
        identity = part.offer.occurrence.identity
        if identity in seen:
            message = "composition occurrence identities must be globally unique"
            raise LayoutInvariantError(message)
        seen.add(identity)
        allowed.update(part.offer.effects)
        if part.offer.interaction:
            interactions.update(part.offer.occurrence.sources)
        if (binding := part.offer.binding) is not None:
            if binding.key in bindings:
                message = f"duplicate visible interaction binding {binding.key!r}"
                raise LayoutInvariantError(message)
            bindings[binding.key] = binding
        if (form := part.offer.form) is not None:
            if form.key in forms:
                message = f"duplicate visible form binding {form.key!r}"
                raise LayoutInvariantError(message)
            forms[form.key] = form
        semantic = part.offer.occurrence.graph.units[part.offer.occurrence.reference].value
        if isinstance(semantic, AtomicNode):
            contract = part.offer.atomic
            if contract is None or part.children:
                message = "opaque atomic offers require a contract and cannot expose invented children"
                raise LayoutInvariantError(message)
            if part.offer.effects != part.offer.occurrence.scope_effects(semantic.effects):
                message = "atomic offers must retain the author's declared effects"
                raise LayoutInvariantError(message)
            atoms[identity] = contract
            if contract.capabilities - context.capabilities:
                preflight.append(
                    UnsupportedCapability(
                        identity,
                        "atomic requirements: " + ", ".join(sorted(contract.capabilities - context.capabilities)),
                    )
                )
            if part.offer.occurrence.placement not in contract.placements:
                preflight.append(ConstraintViolation(identity, "atomic placement"))
        if isinstance(semantic, Budget) and semantic.limits:
            budgets[identity] = semantic.limits
        if isinstance(semantic, Budgeted):
            floors[identity] = semantic.minimum
            if not semantic.stretch:
                budgets[identity] = {Axis.DISPLAY_TEXT: semantic.preferred}
        pending.extend(part.children)
    if preflight:
        return Evaluation(violations=tuple(preflight))
    evaluation = rules.evaluate(candidate, context)
    if set(evaluation.effects) - allowed:
        message = "target evaluation introduced an undeclared fidelity effect"
        raise DrawInvariantError(message)
    if evaluation.feasible and (set(evaluation.effects) != allowed or evaluation.interactions != interactions):
        message = "target evaluation lost declared effects or visible interaction references"
        raise DrawInvariantError(message)
    if context.strict and evaluation.effects:
        violation = ConstraintViolation(candidate.offer.occurrence.identity, "strict preservation")
        return replace(evaluation, violations=(*evaluation.violations, violation))
    if not evaluation.feasible:
        return evaluation
    reserved: dict[OccurrenceId, ResourceCost] = {}
    navigation_chars: dict[OccurrenceId, int] = {}

    def reserve(part: CandidateComposition[RenderTargetT, ContextT, PayloadT]) -> ResourceCost:
        extra = EMPTY_COST
        for child in part.children:
            extra += reserve(child)
        identity = part.offer.occurrence.identity
        navigation_chars[identity] = (
            evaluation.scoped_costs.get(identity, EMPTY_COST).get(Axis.DISPLAY_TEXT)
            if part.offer.occurrence.placement == Placement.NAVIGATION
            else sum(navigation_chars[child.offer.occurrence.identity] for child in part.children)
        )
        if identity in floors:
            context.work.consume()
            if Axis.DISPLAY_TEXT not in evaluation.resource_axes:
                return extra
            actual = (
                evaluation.scoped_costs.get(identity, EMPTY_COST).get(Axis.DISPLAY_TEXT) - navigation_chars[identity]
            )
            extra += ResourceCost({Axis.DISPLAY_TEXT: max(0, floors[identity] - actual - extra.get(Axis.DISPLAY_TEXT))})
        reserved[identity] = extra
        return extra

    extra = reserve(candidate) if floors else EMPTY_COST
    violations: list[CandidateViolation] = [
        CapacityExceeded(candidate.offer.occurrence.identity, axis, used, capacity)
        for axis, used, capacity in (evaluation.cost + extra).over(context.capacities)
    ]
    violations.extend(
        UnsupportedCapability(identity, "character allocation floor")
        for identity in floors
        if Axis.DISPLAY_TEXT not in evaluation.resource_axes
    )
    for identity, contract in atoms.items():
        if evaluation.scoped_costs.get(identity) != contract.total:
            message = "atomic evaluation disagrees with its declared resource cost"
            raise DrawInvariantError(message)
        if evaluation.scoped_resources.get(identity, frozenset()) != contract.shared_costs.keys():
            message = "atomic evaluation disagrees with its declared shared resources"
            raise DrawInvariantError(message)
        for resource, cost in contract.shared_costs.items():
            use = evaluation.resources.uses.get(resource)
            if use is None or use.cost != cost:
                message = "atomic evaluation lost a declared shared resource"
                raise DrawInvariantError(message)
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
        if identity in floors:
            cost = ResourceCost(
                {**cost.values, Axis.DISPLAY_TEXT: max(0, cost.get(Axis.DISPLAY_TEXT) - navigation_chars[identity])}
            )
        violations.extend(
            CapacityExceeded(identity, axis, used, capacity)
            for axis, used, capacity in (cost + reserved.get(identity, EMPTY_COST)).over(supported)
        )
    if violations:
        return replace(evaluation, violations=tuple(violations))
    if evaluation.preparation is None:
        message = "feasible target evaluation did not prepare construction"
        raise DrawInvariantError(message)
    return AdmittedComposition(evaluation, MappingProxyType(bindings), MappingProxyType(forms))


@dataclass(frozen=True, slots=True)
class AdmittedComposition[ResourceT, PreparationT, ContextT]:
    evaluation: Evaluation[ResourceT, PreparationT]
    bindings: Mapping[str, ActionBinding[PressEvent[ContextT]]]
    forms: Mapping[str, FormBinding[ContextT]]


def construct_composition[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT](
    admitted: AdmittedComposition[ResourceT, PreparationT, ContextT],
    rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
    context: PlanningContext,
) -> BuiltComposition[ResourceT, BodyT, PreparationT, ContextT]:
    """Build admitted immutable preparation and independently validate it."""
    evaluation = admitted.evaluation
    assert evaluation.preparation is not None
    context.work.consume()
    body = rules.build(evaluation.preparation, context)
    context.work.consume()
    rules.validate(body, evaluation, context)
    return BuiltComposition(body, evaluation, admitted.bindings, admitted.forms)


def build_composition[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT](
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT],
    rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
    context: PlanningContext,
) -> BuiltComposition[ResourceT, BodyT, PreparationT, ContextT] | Evaluation[ResourceT, PreparationT]:
    """Evaluate and validate a fixed selection without publishing state or retrying it."""
    admitted = evaluate_composition(candidate, rules, context)
    if isinstance(admitted, Evaluation):
        return admitted
    return construct_composition(admitted, rules, context)
