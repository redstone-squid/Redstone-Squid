"""Dependency-neutral profiles for libraries that realize protocol targets."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.scene.model import JsonValue


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
class PreparedExtension[ResourceT]:
    """One target extension prepared and measured exactly once.

    Parameterized by what it produces, so the renderer downcasting `resource` back to a
    frontend object has something to check the downcast against.
    """

    cost: ResourceCost
    scene_payload: Mapping[str, JsonValue]
    resource: ResourceT


class ExtensionAdapter[ResourceT](Protocol):
    """Prepare a logical extension payload for target planning and drawing."""

    def prepare(self, payload: object) -> PreparedExtension[ResourceT]: ...


def extension_capability(kind: str) -> str:
    """The capability string one extension kind contributes.

    One spelling, because the synthesized name was written out at four call sites and a
    typo at any of them would silently drop an extension from planning.
    """
    return f"extension.{kind}"


class AdapterCapability(StrEnum):
    """One behavior a library has been verified to provide.

    A namespace apart from `Capability`, which is what the *protocol* can draw. The two
    unite into `Target.capabilities` as plain strings, so the membership tests that read
    that union are unaffected; keeping the declarations typed apart is what stops an
    adapter behavior being written where a protocol capability is meant.
    """

    RENDER_V2 = "adapter.discord.render.components-v2"
    RENDER_CLASSIC = "adapter.discord.render.classic"
    DISPATCH = "adapter.discord.dispatch"
    INTERACTION_DELIVERY = "adapter.discord.interaction-delivery"
    MODAL_FORMS = "adapter.discord.modal-forms"


@dataclass(frozen=True, slots=True)
class AdapterProfile[AdapterT]:
    """Verified behavior supplied by one library family and version range."""

    family: type[AdapterT]
    name: str
    version_expression: str
    capabilities: frozenset[str] = frozenset()
    extensions: Mapping[str, ExtensionAdapter[Any]] = field(default_factory=dict)
    """Extension adapters by kind.

    `Any` at the container is the honest answer, not a fiction: the mapping is genuinely
    heterogeneous — each kind produces its own frontend object — and there is no single
    resource type for it to be parameterized by. The individual adapter is typed.
    """

    def __post_init__(self) -> None:
        if not self.name:
            message = "adapter profile name cannot be empty"
            raise ValueError(message)
        if not self.version_expression:
            message = "adapter profile version expression cannot be empty"
            raise ValueError(message)
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "extensions", MappingProxyType(dict(sorted(self.extensions.items()))))

    @property
    def extension_capabilities(self) -> frozenset[str]:
        """Capabilities contributed by this profile's target extensions."""
        return frozenset(extension_capability(kind) for kind in self.extensions)

    def combine_capabilities(self, protocol: frozenset[str]) -> frozenset[str]:
        """Combine protocol, behavior, and extension capabilities."""
        return protocol | self.capabilities | self.extension_capabilities
