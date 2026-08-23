"""Dependency-neutral profiles for libraries that realize protocol targets."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from squid_layouts.errors import LayoutInvariantError


@dataclass(frozen=True, slots=True)
class ResourceCost:
    """Named resource consumption measured against target-wide budgets."""

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
        return tuple(self.values)

    def __add__(self, other: ResourceCost) -> ResourceCost:
        if not isinstance(other, ResourceCost):
            return NotImplemented
        merged = dict(self.values)
        for name, value in other.values.items():
            merged[name] = merged.get(name, 0) + value
        return ResourceCost(merged)

    def within(self, capacities: Mapping[str, int]) -> bool:
        return not any(self.over(capacities))

    def over(self, capacities: Mapping[str, int]) -> Iterator[tuple[str, int, int]]:
        for name, capacity in sorted(capacities.items()):
            spent = self.get(name)
            if spent > capacity:
                yield name, spent, capacity

    def cheaper_anywhere(self, other: ResourceCost) -> bool:
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


ADAPTER_RENDER_V2 = "adapter.discord.render.components-v2"
ADAPTER_RENDER_CLASSIC = "adapter.discord.render.classic"
ADAPTER_DISPATCH = "adapter.discord.dispatch"
ADAPTER_INTERACTION_DELIVERY = "adapter.discord.interaction-delivery"
ADAPTER_MODAL_FORMS = "adapter.discord.modal-forms"


@dataclass(frozen=True, slots=True)
class AdapterProfile[AdapterT]:
    """Verified behavior supplied by one library family and version range."""

    family: type[AdapterT]
    name: str
    version_expression: str
    capabilities: frozenset[str] = frozenset()
    extensions: Mapping[str, ExtensionAdapter] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("adapter profile name cannot be empty")
        if not self.version_expression:
            raise ValueError("adapter profile version expression cannot be empty")
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "extensions", MappingProxyType(dict(sorted(self.extensions.items()))))

    @property
    def extension_capabilities(self) -> frozenset[str]:
        """Capabilities contributed by this profile's target extensions."""
        return frozenset(f"extension.{kind}" for kind in self.extensions)

    def combine_capabilities(self, protocol: frozenset[str]) -> frozenset[str]:
        """Combine protocol, behavior, and extension capabilities."""
        return protocol | self.capabilities | self.extension_capabilities
