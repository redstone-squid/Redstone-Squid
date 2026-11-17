"""Isolated membership and fixed-point experiments, not a production layout engine."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from squid_ui.authoring.presentations import SemanticRef
from squid_ui.planning.allocation import AllocationSelection, RegionPages
from squid_ui.planning.composition import OccurrenceId

type Fact = tuple[str, str]
type Facts = Mapping[Fact, int]
type Bodies = Mapping[str, str]
type Layout = Callable[[Bodies, Budget], Facts]


class WorkExhausted(RuntimeError):
    pass


class PendingFact(LookupError):
    pass


@dataclass
class Budget:
    limit: int
    used: int = 0

    def consume(self, amount: int = 1) -> None:
        if self.used + amount > self.limit:
            msg = "shared planning work allowance exhausted"
            raise WorkExhausted(msg)
        self.used += amount


@dataclass(frozen=True)
class Read:
    fact: Fact
    value: int | None


class FactContext:
    def __init__(self, facts: Facts, budget: Budget) -> None:
        self.facts = facts
        self.budget = budget
        self.reads: dict[Fact, int | None] = {}

    def read(self, fact: Fact) -> int:
        self.budget.consume()
        value = self.facts.get(fact)
        self.reads[fact] = value
        if value is None:
            raise PendingFact(fact)
        return value


@dataclass(frozen=True)
class Deferred:
    key: str
    callback: Callable[[FactContext], str]
    initial: str = ""


@dataclass(frozen=True)
class Evaluation:
    callback: Callable[[FactContext], str]
    reads: tuple[Read, ...]
    pending: bool


@dataclass(frozen=True)
class Step:
    round: int
    node: str
    reads: tuple[Read, ...]
    output: str
    pending: bool


@dataclass(frozen=True)
class Certificate:
    snapshot: str
    candidate: str
    bodies: Bodies
    facts: Facts
    cache: Mapping[str, Evaluation]
    rounds: int
    callbacks: int
    work: int
    trace: tuple[Step, ...]


class ResolutionError(RuntimeError):
    def __init__(self, reason: str, trace: tuple[Step, ...]) -> None:
        self.reason = reason
        self.trace = trace
        details = "; ".join(f"{step.node} round {step.round}: {step.reads}" for step in trace)
        super().__init__(f"{reason}: {details}")


class Resolver:
    """Resolve one deterministic candidate; each layout pass supplies a fresh fact index."""

    def __init__(self, budget: Budget, *, max_rounds: int = 8) -> None:
        self.budget = budget
        self.max_rounds = max_rounds

    def solve(
        self,
        nodes: tuple[Deferred, ...],
        layout: Layout,
        *,
        snapshot: str,
        candidate: str,
        prior: Certificate | None = None,
    ) -> Certificate:
        if len({node.key for node in nodes}) != len(nodes):
            msg = "context keys must be unique within this candidate"
            raise ValueError(msg)
        reusable = prior is not None and (prior.snapshot, prior.candidate) == (snapshot, candidate)
        cache = dict(prior.cache) if reusable and prior is not None else {}
        bodies = {
            node.key: prior.bodies.get(node.key, node.initial) if reusable and prior is not None else node.initial
            for node in nodes
        }
        seen = {tuple(sorted(bodies.items()))}
        trace: list[Step] = []
        calls = 0
        started = self.budget.used
        try:
            for round_number in range(1, self.max_rounds + 1):
                self.budget.consume()
                facts = MappingProxyType(dict(layout(MappingProxyType(bodies), self.budget)))
                changed = dict(bodies)
                pending = False
                for node in nodes:
                    previous = cache.get(node.key)
                    if (
                        previous is not None
                        and previous.callback is node.callback
                        and all(facts.get(read.fact) == read.value for read in previous.reads)
                    ):
                        pending |= previous.pending
                        continue
                    self.budget.consume()
                    calls += 1
                    context = FactContext(facts, self.budget)
                    unresolved = False
                    try:
                        changed[node.key] = node.callback(context)
                    except PendingFact:
                        unresolved = True
                    reads = tuple(Read(key, value) for key, value in context.reads.items())
                    cache[node.key] = Evaluation(node.callback, reads, unresolved)
                    trace.append(Step(round_number, node.key, reads, changed[node.key], unresolved))
                    pending |= unresolved
                if changed == bodies:
                    if pending:
                        msg = "unresolved required fact"
                        raise ResolutionError(msg, tuple(trace))
                    return Certificate(
                        snapshot,
                        candidate,
                        MappingProxyType(changed),
                        facts,
                        MappingProxyType(cache),
                        round_number,
                        calls,
                        self.budget.used - started,
                        tuple(trace),
                    )
                signature = tuple(sorted(changed.items()))
                if signature in seen:
                    msg = "oscillation"
                    raise ResolutionError(msg, tuple(trace))
                seen.add(signature)
                bodies = changed
        except WorkExhausted as error:
            msg = "work exhausted"
            raise ResolutionError(msg, tuple(trace)) from error
        msg = "round limit"
        raise ResolutionError(msg, tuple(trace))


@dataclass(frozen=True)
class RowLayout:
    """A deliberately small model: every page repeats the same measured footer."""

    rows: int
    capacity: int
    selected: int = 0

    def __call__(self, bodies: Bodies, budget: Budget) -> Facts:
        budget.consume(self.rows)
        available = self.capacity - len(bodies.get("footer", ""))
        if available < 1:
            msg = "footer leaves no capacity for content"
            raise ValueError(msg)
        pages = max(1, (self.rows + available - 1) // available)
        return {
            ("results", "count"): pages,
            ("results", "selection"): min(max(0, self.selected), pages - 1),
        }


@dataclass(frozen=True)
class Membership:
    region: OccurrenceId
    page: int


@dataclass(frozen=True)
class RegionMembership:
    count: int
    selection: int | None


@dataclass(frozen=True)
class MembershipView:
    """Read direct source/page membership from existing retained allocation metadata.

    Anchors do not contain exact fragment ordinals or repetition provenance.
    Nested page addresses require a richer index than this adapter provides.
    """

    regions: Mapping[OccurrenceId, RegionPages]
    selected: Mapping[OccurrenceId, int]

    @classmethod
    def capture[OutputT](
        cls,
        allocation: AllocationSelection[OutputT],
        coordinates: Mapping[OccurrenceId, int],
    ) -> MembershipView:
        return cls(MappingProxyType(dict(allocation.regions)), allocation.visible(coordinates))

    def region(self, identity: OccurrenceId) -> RegionMembership:
        return RegionMembership(len(self.regions[identity].cuts), self.selected.get(identity))

    def locate(self, source: SemanticRef, *, within: OccurrenceId | None = None) -> tuple[Membership, ...]:
        return tuple(
            Membership(identity, index)
            for identity, region in self.regions.items()
            if within is None or identity == within
            for index, sources in enumerate(region.anchors)
            if source in sources
        )

    @property
    def visible_sources(self) -> frozenset[SemanticRef]:
        return frozenset(
            source for identity, page in self.selected.items() for source in self.regions[identity].anchors[page]
        )
