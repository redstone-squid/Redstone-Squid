"""Target descriptions used by the frontend-neutral planner."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Self

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.adapter import (
    EMPTY_COST as EMPTY_COST,
)
from squid_layouts.planning.adapter import (
    AdapterProfile,
    ExtensionAdapter,
    ResourceCost,
)
from squid_layouts.planning.adapter import (
    PreparedExtension as PreparedExtension,
)
from squid_layouts.planning.limits import Axis, DiscordLimits
from squid_layouts.target_types import DiscordTarget


@dataclass(frozen=True, slots=True)
class TargetProfile[ModeT = DiscordTarget, AdapterT = Any, BodyT = Any]:
    """Stable target identity, capabilities, limits, and extension adapters."""

    id: str
    version: int
    capabilities: frozenset[str] = frozenset()
    limits: DiscordLimits | None = None
    extensions: Mapping[str, ExtensionAdapter] = field(default_factory=dict)
    dialect: object | None = None
    """This target's `TargetDialect`: its shape, and nothing else about planning.

    Typed loosely for the same reason `limits` is — the protocol lives downstream of this
    module, and a target description should not have to import the machinery that reads it.
    """
    mode: type[ModeT] | None = None
    adapter: AdapterProfile[AdapterT] | None = None
    body_type: type[BodyT] | None = None
    selected_adapter_capabilities: frozenset[str] | None = None

    @property
    def adapter_capabilities(self) -> frozenset[str]:
        """Adapter behaviors and extensions selected for this effective target."""
        if self.selected_adapter_capabilities is not None:
            return self.selected_adapter_capabilities
        if self.adapter is None:
            return frozenset()
        extensions = frozenset(f"extension.{kind}" for kind in self.extensions)
        return self.adapter.capabilities | extensions

    def restrict_adapter_capabilities(self, capabilities: frozenset[str]) -> Self:
        """Freeze planning to a recorded subset supplied by the current adapter."""
        if capabilities == self.adapter_capabilities:
            return self
        protocol = self.capabilities - self.adapter_capabilities
        extensions = {
            kind: extension for kind, extension in self.extensions.items() if f"extension.{kind}" in capabilities
        }
        return replace(
            self,
            capabilities=protocol | capabilities,
            extensions=extensions,
            selected_adapter_capabilities=capabilities,
        )

    @property
    def fingerprint(self) -> str:
        """A digest of everything about this profile that changes what a legal document is.

        Recovery compares it against the one a snapshot recorded. Two targets sharing an id
        but differing in capabilities or limits would rebuild the mount against budgets the
        stored render was never fitted to, and the resulting message would be legal only by
        luck. Extensions and the dialect are excluded deliberately: they are process-local
        objects, not facts about the message.
        """
        from squid_layouts.planning.identity import stable_fingerprint

        digest = () if self.limits is None else self.limits.digest()
        return stable_fingerprint((self.id, self.version, sorted(self.capabilities), digest))

    def capacity(self, axis: Axis) -> int | None:
        """This target's remaining room on one axis, or None if it does not budget it."""
        return self.capacities.get(axis)

    @property
    def capacities(self) -> Mapping[Axis, int]:
        """Every message-wide budget by axis, after any reservation."""
        return {} if self.limits is None else self.limits.capacities

    def over_capacity(self, cost: ResourceCost) -> tuple[tuple[str, int, int], ...]:
        """Every axis this cost overspends, as (axis, spent, capacity)."""
        return tuple(cost.over(self.capacities))

    def reserve(self, cost: ResourceCost) -> Self:
        """Return this profile with every reserved resource withheld from its budget.

        A reservation is a smaller target, not a parameter threaded beside one: planning,
        adaptation, and measurement then all see the same room, and no stage can pick a
        strategy that fits the full budget but not the remaining one.
        """
        if not cost.values:
            return self
        capacities = self.capacities
        unknown = sorted(set(cost.values) - set(capacities))
        if unknown:
            known = ", ".join(sorted(capacities)) or "none"
            message = f"target {self.id!r} has no reservable resource {unknown[0]!r} (known: {known})"
            raise LayoutInvariantError(message)
        limits = self.limits
        if limits is None:
            return self
        return replace(self, limits=limits.with_capacities(cost.values))
