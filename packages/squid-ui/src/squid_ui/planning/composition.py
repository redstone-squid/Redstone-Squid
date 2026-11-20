"""Typed candidate composition shared by target realization rules.

This boundary evaluates an explicitly selected composition. Choosing offers and
certifying all reachable page combinations belongs to the joint solver.
"""

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from squid_ui.authoring.document import Document
from squid_ui.authoring.presentations import AtomicNode, Budget, FidelityEffect, SemanticRef
from squid_ui.authoring.semantic import Budgeted, ComponentPlacement, Contextual
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.content.assets import Asset
from squid_ui.content.text import NEUTRAL, Localization
from squid_ui.errors import DrawInvariantError, LayoutInvariantError, LayoutSearchExhaustedError
from squid_ui.interactions import ActionBinding
from squid_ui.interactions.forms import FormBinding
from squid_ui.native import NativeTraits
from squid_ui.overflow import FIRST_PAGE, PageOwnership
from squid_ui.pages import PagePosition
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.pagination.navigation import PlannedNav
from squid_ui.planning.resources import EMPTY_COST, Axis, ResourceCost, TargetLimits
from squid_ui.planning.resources import ResourceIdentity as ResourceIdentity
from squid_ui.planning.semantic_correspondence import SourceScope, budget_floor
from squid_ui.planning.semantic_index import SemanticGraph, SemanticValue
from squid_ui.presentation_state import PresentationState

if TYPE_CHECKING:
    from squid_ui.layout import LayoutContext
    from squid_ui.planning.adapter import ExtensionAdapter


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
class FragmentContract[PayloadT]:
    """Ordered source units and a pure constructor for an exact contiguous fragment.

    The target declares legal ends, including the final end. Repeated sources are
    continuation chrome (for example table headings), never ordinary content.
    """

    units: tuple[frozenset[SemanticRef], ...]
    legal_ends: tuple[int, ...]
    payload: Callable[[int, int], PayloadT]
    repeated: frozenset[SemanticRef] = frozenset()
    continuations: bool = False

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
            if not unit or (not self.continuations and seen.intersection(unit)) or self.repeated.intersection(unit):
                message = "ordinary fragment sources must be nonempty, disjoint, and separate from continuation sources"
                raise LayoutInvariantError(message)
            seen.update(unit)


@dataclass(frozen=True, slots=True)
class ContextualChrome[RenderTargetT, ContextT, PayloadT]:
    content: Contextual[RenderTargetT, ContextT]
    realize: Callable[
        [Document[RenderTargetT, ContextT], PlanningContext],
        tuple[CandidateComposition[RenderTargetT, ContextT, PayloadT], ...],
    ]


@dataclass(frozen=True, slots=True)
class PageWeights:
    """A lower bound per source unit, stored as cumulative costs for contiguous cuts."""

    axis: Axis
    prefix: tuple[int, ...]
    overhead: int = 0

    def __post_init__(self) -> None:
        if (
            not self.prefix
            or self.prefix[0] != 0
            or self.overhead < 0
            or any(left > right for left, right in pairwise(self.prefix))
        ):
            message = "page weights require nonnegative costs and a prefix starting at zero"
            raise LayoutInvariantError(message)


@dataclass(frozen=True, slots=True)
class RegionContract[RenderTargetT, ContextT, PayloadT]:
    """Target-declared pagination chrome, composed and charged on every visible page."""

    key: str
    chrome: ContextualChrome[RenderTargetT, ContextT, PayloadT]
    chars: int | None = None
    min_fill: int = 0
    widows: int = 1
    owner: PageOwnership = FIRST_PAGE
    cursor_key: str | None = None
    per: int | None = None
    required_ends: tuple[int, ...] = ()
    max_units: int | None = None
    """Hard maximum source units per page, independent of page contents and chrome."""
    weights: PageWeights | None = None

    def __post_init__(self) -> None:
        if self.max_units is not None and self.max_units < 1:
            message = "region page unit limit must be positive"
            raise LayoutInvariantError(message)


@dataclass(frozen=True, slots=True)
class SemanticOccurrence[RenderTargetT, ContextT]:
    identity: OccurrenceId
    graph: SemanticGraph[RenderTargetT, ContextT]
    reference: SemanticRef
    placement: Placement = Placement.CONTENT
    origin: SourceScope[RenderTargetT, ContextT] | None = None
    fragment: FragmentCoverage | None = None
    palette: Palette | None = None
    """Inherited palette for this presentation, including across alternative graphs."""
    enclosing: SemanticOccurrence[RenderTargetT, ContextT] | None = None
    """The original scope surrounding an alternative's separate presentation graph."""

    def __post_init__(self) -> None:
        if self.reference not in self.graph.units:
            message = "an occurrence needs a resolved reference in its semantic graph"
            raise LayoutInvariantError(message)

    def ancestors(self) -> Iterator[SemanticValue[RenderTargetT, ContextT]]:
        occurrence: SemanticOccurrence[RenderTargetT, ContextT] | None = self
        while occurrence is not None:
            parent = occurrence.graph.units[occurrence.reference].parent
            while parent is not None:
                unit = occurrence.graph.units[parent]
                yield unit.value
                parent = unit.parent
            occurrence = occurrence.enclosing

    @property
    def sources(self) -> frozenset[SemanticRef]:
        if self.fragment is not None:
            return self.fragment.sources | self.fragment.repeated
        return self.origin.references(self.reference) if self.origin is not None else self.graph.atoms(self.reference)

    @property
    def page_source(self) -> SemanticRef:
        return self.origin.page_boundary(self.reference) if self.origin is not None else self.reference

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
    binding: ActionBinding | None = None
    form: FormBinding[ContextT] | None = None
    atomic: AtomicContract | None = None
    flow: bool = False
    """Allow contiguous fragments of the declared children, preserving this shell."""
    keep_with_next: bool = False
    fragments: FragmentContract[PayloadT] | None = None
    region: RegionContract[RenderTargetT, ContextT, PayloadT] | None = None
    preference_priority: int | None = None
    """Compare this subtree as a sibling preference scope, even without a pager."""
    rejections: tuple[CandidateViolation, ...] = ()
    """Intrinsic failures that rule out this offer for every partition."""

    def atomic_violations(self, context: PlanningContext) -> tuple[CandidateViolation, ...]:
        """Check an opaque offer using its declared placement, capabilities, and costs."""
        occurrence = self.occurrence
        semantic = occurrence.graph.units[occurrence.reference].value
        contract = self.atomic
        if contract is None and not isinstance(semantic, AtomicNode):
            return ()
        if contract is None or self.children:
            message = "opaque atomic offers require a contract and cannot expose invented children"
            raise LayoutInvariantError(message)
        if isinstance(semantic, AtomicNode) and self.effects != occurrence.scope_effects(semantic.effects):
            message = "atomic offers must retain the author's declared effects"
            raise LayoutInvariantError(message)
        failures: list[CandidateViolation] = []
        if missing := contract.capabilities - context.capabilities:
            failures.append(
                UnsupportedCapability(occurrence.identity, "atomic requirements: " + ", ".join(sorted(missing)))
            )
        if occurrence.placement not in contract.placements:
            failures.append(ConstraintViolation(occurrence.identity, "atomic placement"))
        failures.extend(
            CapacityExceeded(occurrence.identity, axis, used, capacity)
            for axis, used, capacity in contract.total.over(context.capacities)
        )
        return tuple(failures)


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
    limitation: str | None = None


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
            or child.offer.occurrence.enclosing is not expected_child.enclosing
            for child, expected_child in zip(self.children, self.offer.children, strict=True)
        ):
            message = "candidate children must retain their declared semantic source and placement"
            raise LayoutInvariantError(message)
        if len(set(actual)) != len(actual):
            message = "repeated content needs separate occurrence identities"
            raise LayoutInvariantError(message)


class WorkBudget:
    """One shared allowance for offers, evaluation, construction, and validation."""

    __slots__ = ("_evaluations", "_limit", "_spent")

    def __init__(self, limit: int) -> None:
        if limit < 0:
            message = "planning work limit cannot be negative"
            raise LayoutInvariantError(message)
        self._limit = limit
        self._spent = 0
        self._evaluations = 0

    @property
    def spent(self) -> int:
        return self._spent

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def target_evaluations(self) -> int:
        return self._evaluations

    @property
    def remaining(self) -> int:
        return self._limit - self._spent

    @contextmanager
    def allowance(self, limit: int) -> Iterator[None]:
        """Bound one phase without spending the allowance reserved for later phases."""
        if limit < 0:
            message = "phase work limit cannot be negative"
            raise LayoutInvariantError(message)
        previous = self._limit
        self._limit = min(previous, self._spent + limit)
        try:
            yield
        finally:
            self._limit = previous

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
    navigation: PlannedNav | None = None
    mounted: bool = False
    assets: tuple[Asset, ...] = ()
    escape_text: Callable[[str], str] | None = None
    presentation: PresentationState = field(default_factory=PresentationState)
    extensions: Mapping[str, ExtensionAdapter[Any, Any]] = field(default_factory=dict)
    limits: TargetLimits | None = None
    inspect_layout: bool = False
    local_view: bool = False
    page_overrides: Mapping[OccurrenceId, int] = field(default_factory=dict)
    positions: Mapping[str, PagePosition] = field(default_factory=dict)


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
    resources: SharedResources[ResourceT] = field(default_factory=lambda: SharedResources[ResourceT]())
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
    validation: Mapping[str, object] = field(default_factory=dict)
    """Target-owned declarations retained for independent output validation."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "validation", MappingProxyType(dict(self.validation)))
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
    ):
        """Discover realizations for an immutable occurrence and planning context.

        Search memoizes discovery within one call. Offers must not depend on the
        order in which other occurrences are explored or on mutable external state.
        """
        ...

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
    bindings: Mapping[str, ActionBinding] = field(default_factory=dict)
    forms: Mapping[str, FormBinding[ContextT]] = field(default_factory=dict)
    layout: LayoutContext | None = field(default=None, kw_only=True)
    choices: tuple[tuple[OccurrenceId, str], ...] = field(default=(), kw_only=True)
    visible_components: frozenset[str] = field(default=frozenset(), kw_only=True)


def select_composition[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT],
    rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
    context: PlanningContext,
    *,
    selections: Mapping[OccurrenceId, str],
) -> CandidateComposition[RenderTargetT, ContextT, PayloadT] | UnsupportedCapability:
    """Compose explicit choices without running a fit search or deciding page allocations.

    An occurrence with one offer needs no selection entry. Every choice among
    multiple offers must be explicit until the joint solver takes over selection.
    """
    context = replace(context, palette=occurrence.palette) if occurrence.palette is not None else context
    offers = rules.offers(occurrence, context)
    if isinstance(offers, OfferDiscovery):
        offers = offers.offers
    if not offers:
        return UnsupportedCapability(
            occurrence.identity, type(occurrence.graph.units[occurrence.reference].value).__name__
        )
    choice = selections.get(occurrence.identity)
    if choice is None and len(offers) == 1:
        selected = offers[0]
    else:
        selected = next((offer for offer in offers if offer.key == choice), None)
        if selected is None:
            message = f"an explicit valid representation is required for {occurrence.identity.path!r}"
            raise LayoutInvariantError(message)
    children: list[CandidateComposition[RenderTargetT, ContextT, PayloadT]] = []
    for child in selected.children:
        candidate = select_composition(child, rules, context, selections=selections)
        if isinstance(candidate, UnsupportedCapability):
            return candidate
        children.append(candidate)
    return CandidateComposition(selected, tuple(children))


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
    bindings: dict[str, ActionBinding] = {}
    forms: dict[str, FormBinding[ContextT]] = {}
    interactions: set[SemanticRef] = set()
    atoms: dict[OccurrenceId, AtomicContract] = {}
    visible_components: set[str] = set()
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
        if isinstance(semantic, ComponentPlacement):
            visible_components.add(semantic.placement)
        if isinstance(semantic, AtomicNode) or part.offer.atomic is not None:
            preflight.extend(part.offer.atomic_violations(context))
        if (contract := part.offer.atomic) is not None:
            atoms[identity] = contract
        if isinstance(semantic, Budget) and semantic.limits:
            budgets[identity] = semantic.limits
        character_budget = semantic if isinstance(semantic, Budgeted) else NativeTraits.of(semantic).budget
        if character_budget is not None:
            floors[identity] = budget_floor(
                part.offer.occurrence.graph, part.offer.occurrence.reference, part.offer.occurrence.ancestors()
            )
            budgets[identity] = {Axis.DISPLAY_TEXT: character_budget.preferred + character_budget.stretch}
        pending.extend(part.children)
    if preflight:
        return Evaluation(violations=tuple(preflight))
    context.work._evaluations += 1
    evaluation = rules.evaluate(candidate, context)
    if set(evaluation.effects) - allowed:
        message = "target evaluation introduced an undeclared fidelity effect"
        raise DrawInvariantError(message)
    if evaluation.feasible and (
        set(evaluation.effects) != allowed or evaluation.interactions != frozenset(interactions)
    ):
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
        if evaluation.scoped_resources.get(identity, frozenset()) != frozenset(contract.shared_costs):
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
    return AdmittedComposition(
        evaluation, MappingProxyType(bindings), MappingProxyType(forms), frozenset(visible_components)
    )


@dataclass(frozen=True, slots=True)
class AdmittedComposition[ResourceT, PreparationT, ContextT]:
    evaluation: Evaluation[ResourceT, PreparationT]
    bindings: Mapping[str, ActionBinding]
    forms: Mapping[str, FormBinding[ContextT]]
    visible_components: frozenset[str] = frozenset()


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
    return BuiltComposition(
        body, evaluation, admitted.bindings, admitted.forms, visible_components=admitted.visible_components
    )


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
