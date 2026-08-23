"""Target descriptions used by the frontend-neutral planner."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Self, cast

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.adapter import (
    EMPTY_COST,
    AdapterProfile,
    ExtensionAdapter,
    PreparedExtension,
    ResourceCost,
)
from squid_layouts.target_types import DiscordTarget


def _limit_values(limits: object) -> tuple[tuple[str, object], ...]:
    """A limits object's fields, in name order, for a stable digest."""
    if limits is None:
        return ()
    fields = getattr(limits, "__dataclass_fields__", None)
    if fields is None:
        return ()
    return tuple(sorted((name, getattr(limits, name)) for name in fields))


@dataclass(frozen=True, slots=True)
class TargetProfile[ModeT = DiscordTarget, AdapterT = Any, BodyT = Any]:
    """Stable target identity, capabilities, limits, and extension adapters."""

    id: str
    version: int
    capabilities: frozenset[str] = frozenset()
    limits: object | None = None
    extensions: Mapping[str, ExtensionAdapter] = field(default_factory=dict)
    dialect: object | None = None
    """This target's `TargetDialect`: its shape, and nothing else about planning.

    Typed loosely for the same reason `limits` is — the protocol lives downstream of this
    module, and a target description should not have to import the machinery that reads it.
    """
    resources: Mapping[str, str] = field(default_factory=dict)
    """Resource name to the message-wide limit attribute a reservation withholds from.

    Only whole-message budgets belong here. Local caps describe the target's shape rather
    than the remaining room, and reducing one would change what a legal document is.
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
            kind: extension
            for kind, extension in self.extensions.items()
            if f"extension.{kind}" in capabilities
        }
        return replace(
            self,
            capabilities=protocol | capabilities,
            extensions=extensions,
            selected_adapter_capabilities=capabilities,
        )

    @property
    def budgets(self) -> Mapping[str, str]:
        """Axis name to limit attribute, taken from the limits unless this profile overrides."""
        if self.resources:
            return self.resources
        declared = getattr(self.limits, "budgets", None)
        return declared if isinstance(declared, Mapping) else {}

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

        return stable_fingerprint((self.id, self.version, sorted(self.capabilities), _limit_values(self.limits)))

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

    def reserve(self, cost: ResourceCost) -> Self:
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
        return replace(self, limits=replace(cast(Any, limits), **reductions))
