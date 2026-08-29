"""Target-neutral resource axes, costs, and limit contracts."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, Self

from squid_ui.errors import LayoutInvariantError


class Axis(StrEnum):
    """One target-wide resource budget measured during planning."""

    DISPLAY_TEXT = "display_text"
    CONTENT_TEXT = "content_text"
    EMBED_TEXT = "embed_text"
    COMPONENTS = "components"
    ATTACHMENTS = "attachments"
    EMBEDS = "embeds"
    ROWS = "rows"
    CONTROLS = "controls"
    BLOCKS = "blocks"


TEXT_AXES = frozenset({Axis.DISPLAY_TEXT, Axis.CONTENT_TEXT, Axis.EMBED_TEXT})
"""Every built-in text axis, whichever target declares it."""


@dataclass(frozen=True, slots=True)
class ResourceCost:
    """Named resource consumption measured against target-wide budgets."""

    values: Mapping[Axis, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        negative = sorted(name for name, value in self.values.items() if value < 0)
        if negative:
            message = f"resource {negative[0]!r} cannot cost {self.values[negative[0]]}"
            raise LayoutInvariantError(message)
        values = dict(sorted((name, value) for name, value in self.values.items() if value))
        object.__setattr__(self, "values", MappingProxyType(values))

    def get(self, name: Axis) -> int:
        return self.values.get(name, 0)

    @property
    def axes(self) -> tuple[Axis, ...]:
        return tuple(self.values)

    def __add__(self, other: ResourceCost) -> ResourceCost:
        if not isinstance(other, ResourceCost):
            return NotImplemented
        merged = dict(self.values)
        for name, value in other.values.items():
            merged[name] = merged.get(name, 0) + value
        return ResourceCost(merged)

    def within(self, capacities: Mapping[Axis, int]) -> bool:
        return not any(self.over(capacities))

    def over(self, capacities: Mapping[Axis, int]) -> Iterator[tuple[Axis, int, int]]:
        for name, capacity in sorted(capacities.items()):
            spent = self.get(name)
            if spent > capacity:
                yield name, spent, capacity

    def cheaper_anywhere(self, other: ResourceCost) -> bool:
        return any(self.get(name) < other.get(name) for name in {*self.values, *other.values})


EMPTY_COST = ResourceCost()


class TargetLimits(Protocol):
    """The small limits surface shared by every planning target."""

    @property
    def capacities(self) -> Mapping[Axis, int]: ...

    def with_capacities(self, reductions: Mapping[Axis, int]) -> Self: ...

    def digest(self) -> tuple[tuple[str, object], ...]: ...


__all__ = ["EMPTY_COST", "TEXT_AXES", "Axis", "ResourceCost", "TargetLimits"]
