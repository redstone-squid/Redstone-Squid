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
    """What a target is called and what it can do, without how to compile for it.

    The parameter type for layers that only name a target: cache keys, diagnostics and
    capability checks take this rather than `AnyTarget`.
    """

    @property
    def id(self) -> str:
        """The dialect's stable protocol name, such as `discord.components-v2`."""
        ...

    @property
    def version(self) -> int:
        """The dialect's scene format version; a renderer refuses a scene whose version it cannot draw."""
        ...

    @property
    def triple(self) -> str:
        """`{dialect id}+{adapter name}`, the name a durable mount records."""
        ...

    @property
    def fingerprint(self) -> str:
        """A digest of both axes, the capabilities and the limits; two targets with equal fingerprints plan alike."""
        ...

    @property
    def capabilities(self) -> frozenset[str]:
        """Protocol, adapter and extension capabilities as one flat string set."""
        ...


LimitsT = TypeVar("LimitsT", bound=TargetLimits)
BodyT = TypeVar("BodyT", bound=scene.Body)
RenderTargetT_co = TypeVar("RenderTargetT_co", covariant=True)
AdapterT_co = TypeVar("AdapterT_co", covariant=True)
"""Covariant on purpose, spelled the old way because PEP 695 cannot declare variance.

`Target` only reads these back out (`render_target`, `AdapterProfile[AdapterT_co]`), so a
`Target[.., ComponentsV2Target, DiscordPyAdapter]` must satisfy a parameter typed for the wider
markers those refine. `runtime.component.RenderTargetT` is the contravariant counterpart.
"""


@dataclass(frozen=True, slots=True)
class Target(Generic[LimitsT, BodyT, RenderTargetT_co, AdapterT_co]):
    """What a document is compiled to: a protocol dialect and an adapter for it.

    Two axes, like a compiler's `x86_64-unknown-linux-gnu`: the dialect says what a legal
    message is, the adapter which library is verified to produce one. The id, version,
    render target, body type and protocol capabilities are read off the dialect, so none
    can disagree with it. `limits` is stored apart because it is the dialect's table after
    any `reserve` has been withheld from it.
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
        """Freeze planning to a recorded subset of the adapter's capabilities.

        Only `adapter_capabilities` and `extensions` narrow; protocol capabilities live on the
        dialect and are untouched.
        """
        if capabilities == self.adapter_capabilities:
            return self
        return replace(self, selected_adapter_capabilities=capabilities)

    @property
    def fingerprint(self) -> str:
        """A digest of everything about this target that changes what a legal document is.

        Covers the dialect id and version, both capability sets, the adapter name and
        `limits.digest()`; the dialect object and extension adapters are process-local and
        excluded. Recovery compares it against the one a snapshot recorded, so a mount is
        never rebuilt against budgets its stored render was not fitted to.
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
        """This target with each axis of `cost` subtracted from its capacities, clamped at zero.

        Returned as a smaller target rather than a parameter threaded beside one, so planning,
        adaptation and measurement all see the same room. Raises `LayoutInvariantError` when
        `cost` names an axis this target does not budget.
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
"""A target with all four parameters erased.

For layers that need both real axes but are written once for every dialect, such as the
search and the adapter check; a layer that only names a target takes `TargetIdentity`.
"""
