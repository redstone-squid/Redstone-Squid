"""Target descriptions used by the frontend-neutral planner."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol

from squid_layouts.errors import LayoutInvariantError


@dataclass(frozen=True, slots=True)
class ResourceCost:
    """Named resource consumption measured against a target's message-wide budgets.

    One vocabulary for everything a message spends: text on each of its text pools,
    components, embeds, rows, controls, attachments. The planner reads caps through the
    same names a host reservation withholds against, so no stage can price a document in
    units another stage does not recognise.

    Zero axes are dropped and the rest are stored in name order, so two costs that spend
    the same thing compare and fingerprint the same however they were built.
    """

    values: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        negative = sorted(name for name, value in self.values.items() if value < 0)
        if negative:
            message = f"resource {negative[0]!r} cannot cost {self.values[negative[0]]}"
            raise LayoutInvariantError(message)
        object.__setattr__(self, "values", dict(sorted((n, v) for n, v in self.values.items() if v)))

    def get(self, name: str) -> int:
        return self.values.get(name, 0)

    @property
    def axes(self) -> tuple[str, ...]:
        """Every axis this cost actually spends on, in name order."""
        return tuple(self.values)

    def __add__(self, other: ResourceCost) -> ResourceCost:
        """Combine two reservations, summing every axis either one names."""
        if not isinstance(other, ResourceCost):
            return NotImplemented
        merged = dict(self.values)
        for name, value in other.values.items():
            merged[name] = merged.get(name, 0) + value
        return ResourceCost(merged)

    def within(self, capacities: Mapping[str, int]) -> bool:
        """True when no named axis exceeds its capacity. Unknown axes are unconstrained."""
        return not any(self.over(capacities))

    def over(self, capacities: Mapping[str, int]) -> Iterator[tuple[str, int, int]]:
        """Every axis over its capacity as (axis, spent, capacity), in name order.

        All of them, not the first: a document that blows two budgets at once should be
        told about both rather than sent round the loop to discover the second.
        """
        for name, capacity in sorted(capacities.items()):
            spent = self.get(name)
            if spent > capacity:
                yield name, spent, capacity

    def cheaper_anywhere(self, other: ResourceCost) -> bool:
        """True when some axis costs strictly less here than there.

        The Pareto test. A candidate that is no better on any axis and no better on
        fidelity cannot lead anywhere the other cannot already reach.
        """
        return any(self.get(name) < other.get(name) for name in {*self.values, *other.values})


EMPTY_COST = ResourceCost()


@dataclass(frozen=True, slots=True)
class PreparedExtension:
    """One target extension prepared and measured exactly once."""

    cost: ResourceCost
    scene_payload: Mapping[str, object]
    resource: object


class ExtensionAdapter(Protocol):
    """Prepare a logical extension payload for target planning and drawing."""

    def prepare(self, payload: object) -> PreparedExtension: ...


@dataclass(frozen=True, slots=True)
class TargetProfile:
    """Stable target identity, capabilities, limits, and extension adapters."""

    id: str
    version: int
    capabilities: frozenset[str] = frozenset()
    limits: object | None = None
    extensions: Mapping[str, ExtensionAdapter] = field(default_factory=dict)
    resources: Mapping[str, str] = field(default_factory=dict)
    """Resource name to the message-wide limit attribute a reservation withholds from.

    Only whole-message budgets belong here. Local caps describe the target's shape rather
    than the remaining room, and reducing one would change what a legal document is.
    """

    @property
    def budgets(self) -> Mapping[str, str]:
        """Axis name to limit attribute, taken from the limits unless this profile overrides."""
        if self.resources:
            return self.resources
        declared = getattr(self.limits, "budgets", None)
        return declared if isinstance(declared, Mapping) else {}

    def capacity(self, name: str) -> int | None:
        """This target's remaining room on one axis, or None if it does not budget it."""
        attribute = self.budgets.get(name)
        if attribute is None or self.limits is None:
            return None
        return getattr(self.limits, attribute)

    @property
    def capacities(self) -> dict[str, int]:
        """Every message-wide budget by axis name, after any reservation."""
        if self.limits is None:
            return {}
        return {name: getattr(self.limits, attribute) for name, attribute in self.budgets.items()}

    def over_capacity(self, cost: ResourceCost) -> tuple[tuple[str, int, int], ...]:
        """Every axis this cost overspends, as (axis, spent, capacity)."""
        return tuple(cost.over(self.capacities))

    def reserve(self, cost: ResourceCost) -> TargetProfile:
        """Return this profile with every reserved resource withheld from its budget.

        A reservation is a smaller target, not a parameter threaded beside one: planning,
        adaptation, and measurement then all see the same room, and no stage can pick a
        strategy that fits the full budget but not the remaining one.
        """
        if not cost.values:
            return self
        budgets = self.budgets
        unknown = sorted(set(cost.values) - set(budgets))
        if unknown:
            known = ", ".join(sorted(budgets)) or "none"
            message = f"target {self.id!r} has no reservable resource {unknown[0]!r} (known: {known})"
            raise LayoutInvariantError(message)
        limits = self.limits
        if limits is None:
            return self
        reductions = {
            budgets[name]: max(0, getattr(limits, budgets[name]) - amount)
            for name, amount in cost.values.items()
            if amount
        }
        if not reductions:
            return self
        return TargetProfile(
            id=self.id,
            version=self.version,
            capabilities=self.capabilities,
            limits=replace(limits, **reductions),
            extensions=self.extensions,
            resources=self.resources,
        )
