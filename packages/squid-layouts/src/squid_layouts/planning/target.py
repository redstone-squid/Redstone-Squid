"""Target descriptions used by the frontend-neutral planner."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol

from squid_layouts.errors import LayoutInvariantError


@dataclass(frozen=True, slots=True)
class ResourceCost:
    """Named resource consumption reserved from a target's budgets."""

    values: Mapping[str, int] = field(default_factory=dict)

    def get(self, name: str) -> int:
        return self.values.get(name, 0)

    def __add__(self, other: ResourceCost) -> ResourceCost:
        """Combine two reservations, summing every axis either one names."""
        if not isinstance(other, ResourceCost):
            return NotImplemented
        merged = dict(self.values)
        for name, value in other.values.items():
            merged[name] = merged.get(name, 0) + value
        return ResourceCost(merged)


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

    def reserve(self, cost: ResourceCost) -> TargetProfile:
        """Return this profile with every reserved resource withheld from its budget.

        A reservation is a smaller target, not a parameter threaded beside one: planning,
        adaptation, and measurement then all see the same room, and no stage can pick a
        strategy that fits the full budget but not the remaining one.
        """
        if not cost.values:
            return self
        unknown = sorted(set(cost.values) - set(self.resources))
        if unknown:
            known = ", ".join(sorted(self.resources)) or "none"
            message = f"target {self.id!r} has no reservable resource {unknown[0]!r} (known: {known})"
            raise LayoutInvariantError(message)
        limits = self.limits
        if limits is None:
            return self
        reductions = {
            self.resources[name]: max(0, getattr(limits, self.resources[name]) - amount)
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
