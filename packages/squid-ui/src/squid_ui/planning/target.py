"""A render target: one protocol dialect paired with the adapter that realizes it."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Generic, Protocol, Self, TypeVar, cast

from squid_ui import scene
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.adapter import (
    AdapterProfile,
    ExtensionAdapter,
    extension_capability,
)
from squid_ui.planning.adapter import (
    PreparedExtension as PreparedExtension,
)
from squid_ui.planning.dialect import TargetDialect
from squid_ui.planning.resources import EMPTY_COST as EMPTY_COST
from squid_ui.planning.resources import Axis, ResourceCost, TargetLimits


class TargetIdentity(Protocol):
    """What a target is called and what it can do -- everything but how to compile for it.

    Enough for the layers that only need to *name* a target: cache keys, diagnostics,
    capability checks. They took `Target[Any, Any, Any, Any]` before, which said nothing
    about what they read and erased four parameters to say it.
    """

    @property
    def id(self) -> str: ...

    @property
    def version(self) -> int: ...

    @property
    def triple(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...

    @property
    def capabilities(self) -> frozenset[str]: ...


LimitsT = TypeVar("LimitsT", bound=TargetLimits)
BodyT = TypeVar("BodyT", bound=scene.Body)
RenderTargetT_co = TypeVar("RenderTargetT_co", covariant=True)
AdapterT_co = TypeVar("AdapterT_co", covariant=True)


@dataclass(frozen=True, slots=True)
class Target(Generic[LimitsT, BodyT, RenderTargetT_co, AdapterT_co]):
    """What a document is compiled to: a protocol dialect and an adapter for it.

    Two axes and nothing else, the way a compiler names `x86_64-unknown-linux-gnu` rather
    than threading arch, OS and ABI separately. The dialect says what a legal message is;
    the adapter says which library has been verified to produce one. Everything the planner
    used to be handed alongside a target — its id, version, render target, body type and protocol
    capabilities — is derived from one of the two, so no two of them can fall out of step.

    Only `limits` is stored separately, because it is not a fact about either axis: it is
    the dialect's table after any reservation has been withheld from it.
    """

    dialect: TargetDialect[LimitsT, BodyT, Any]
    adapter: AdapterProfile[AdapterT_co]
    limits: LimitsT
    selected_adapter_capabilities: frozenset[str] | None = None
    """The adapter capabilities planning was frozen to, when a snapshot recorded a subset."""
    _fingerprint: str = field(init=False, repr=False, compare=False, metadata={"stable_identity": False})

    def __post_init__(self) -> None:
        from squid_ui.planning.identity import stable_fingerprint

        object.__setattr__(
            self,
            "_fingerprint",
            stable_fingerprint(
                (
                    self.dialect.id,
                    self.dialect.version,
                    sorted(self.protocol_capabilities),
                    self.adapter.name,
                    sorted(self.adapter_capabilities),
                    self.limits.digest(),
                )
            ),
        )

    @property
    def id(self) -> str:
        return self.dialect.id

    @property
    def version(self) -> int:
        return self.dialect.version

    @property
    def render_target(self) -> type[RenderTargetT_co]:
        return cast(type[RenderTargetT_co], self.dialect.render_target)

    @property
    def body_type(self) -> type[BodyT]:
        return self.dialect.body_type

    @property
    def triple(self) -> str:
        """This target's full name: both axes, so two adapters for one protocol differ.

        Recorded by a durable mount, which must rebuild against the same budgets. A planned
        *scene* records the dialect id alone, because any renderer for that protocol may
        draw it.
        """
        return f"{self.dialect.id}+{self.adapter.name}"

    @property
    def extensions(self) -> Mapping[str, ExtensionAdapter[Any]]:
        """The extension adapters in play, which a dialect that draws none never has."""
        if not self.dialect.realizes_extensions:
            return {}
        selected = self.selected_adapter_capabilities
        if selected is None:
            return self.adapter.extensions
        return {
            kind: adapter for kind, adapter in self.adapter.extensions.items() if extension_capability(kind) in selected
        }

    @property
    def protocol_capabilities(self) -> frozenset[str]:
        """What the dialect can draw, independent of who draws it."""
        return frozenset(self.dialect.capabilities)

    @property
    def adapter_capabilities(self) -> frozenset[str]:
        """Adapter behaviors and extensions selected for this effective target."""
        if self.selected_adapter_capabilities is not None:
            return self.selected_adapter_capabilities
        return self.adapter.capabilities | frozenset(extension_capability(kind) for kind in self.extensions)

    @property
    def capabilities(self) -> frozenset[str]:
        """Everything this target can do: the protocol's, the adapter's, and its extensions."""
        return self.protocol_capabilities | self.adapter_capabilities

    def restrict_adapter_capabilities(self, capabilities: frozenset[str]) -> Self:
        """Freeze planning to a recorded subset supplied by the current adapter.

        Nothing is subtracted back out: protocol capabilities live on the dialect and were
        never mixed into the adapter's set to begin with.
        """
        if capabilities == self.adapter_capabilities:
            return self
        return replace(self, selected_adapter_capabilities=capabilities)

    @property
    def fingerprint(self) -> str:
        """A digest of everything about this target that changes what a legal document is.

        Recovery compares it against the one a snapshot recorded. Two targets sharing a
        triple but differing in capabilities or limits would rebuild the mount against
        budgets the stored render was never fitted to, and the resulting message would be
        legal only by luck. The dialect object and the extension adapters are excluded
        deliberately: they are process-local objects, not facts about the message.
        """
        return self._fingerprint

    def capacity(self, axis: Axis) -> int | None:
        """This target's remaining room on one axis, or None if it does not budget it."""
        return self.capacities.get(axis)

    @property
    def capacities(self) -> Mapping[Axis, int]:
        """Every message-wide budget by axis, after any reservation."""
        return self.limits.capacities

    def over_capacity(self, cost: ResourceCost) -> tuple[tuple[Axis, int, int], ...]:
        """Every axis this cost overspends, as (axis, spent, capacity)."""
        return tuple(cost.over(self.capacities))

    def reserve(self, cost: ResourceCost) -> Self:
        """Return this target with every reserved resource withheld from its budget.

        A reservation is a smaller target, not a parameter threaded beside one: planning,
        adaptation, and measurement then all see the same room, and no stage can pick a
        strategy that fits the full budget but not the remaining one.
        """
        if not cost.values:
            return self
        unknown = sorted(set(cost.values) - set(self.capacities))
        if unknown:
            known = ", ".join(sorted(self.capacities)) or "none"
            # str() first: an Axis member reprs as `<Axis.X: 'x'>`, and this message names axes.
            message = f"target {self.triple!r} has no reservable resource {str(unknown[0])!r} (known: {known})"
            raise LayoutInvariantError(message)
        return replace(self, limits=self.limits.with_capacities(cost.values))


type AnyTarget = Target[Any, Any, Any, Any]
"""A target whose four parameters are deliberately erased.

For the layers that need both real axes -- the search hands the target to its own dialect,
the adapter check reads the adapter -- but are written once for every dialect. Stating the
erasure once beats spelling `Any` four times at each site, and distinguishes it from the
places that only need `TargetIdentity`.
"""
