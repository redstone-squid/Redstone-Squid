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
    """Target identity and capabilities used by caching, diagnostics, and capability checks."""

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
"""Declared the old way, and covariant on purpose, in a package that otherwise uses PEP 695.

`Target` reads its render target and adapter back out — through the `render_target` property
and `AdapterProfile[AdapterT_co]` — so a `Target[.., ComponentsV2Target, DiscordPyAdapter]`
must satisfy a parameter typed for the wider markers those refine. PEP 695 infers variance
rather than letting it be declared, and there is no syntax to override the inference, so the
old spelling is the only one that can say this. `Component` carries the mirror-image note for
its contravariant parameter.
"""


@dataclass(frozen=True, slots=True)
class Target(Generic[LimitsT, BodyT, RenderTargetT_co, AdapterT_co]):
    """Describe the display format and renderer library a layout is being planned for.

    The dialect defines the scene body, supported widgets, and planner backend. The
    adapter profile names the library that will render that body and the behaviors
    it supports. For example, Discord Components V2 and classic messages are separate
    dialects that can use the same discord.py adapter family.

    Capabilities come from both declarations, while limits records the space available
    for this plan. A reservation reduces those limits without changing the dialect
    or adapter, so every planning stage sees the same remaining space.
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
                    self.adapter.frontend,
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
    def extensions(self) -> Mapping[str, ExtensionAdapter[Any, Any]]:
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
        """Use the supplied adapter capabilities, leaving protocol capabilities unchanged.

        The caller must validate that these capabilities are supported by the
        current adapter; this method does not check subset membership.
        """
        if capabilities == self.adapter_capabilities:
            return self
        return replace(self, selected_adapter_capabilities=capabilities)

    @property
    def fingerprint(self) -> str:
        """A cached digest of target identity, capabilities, adapter identity, and limits.

        Recovery uses it to detect incompatible snapshots. It excludes live
        dialect and extension adapter objects, so changes to their implementations
        require an appropriate identity or version change.
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
"""A target with erased type parameters for code that uses multiple target families."""
