"""Target descriptions used by the frontend-neutral planner."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ResourceCost:
    """Named resource consumption reserved from a target's budgets."""

    values: Mapping[str, int] = field(default_factory=dict)

    def get(self, name: str) -> int:
        return self.values.get(name, 0)


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
