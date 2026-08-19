"""Target descriptions used by the frontend-neutral planner."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ResourceCost:
    """Named resource consumption reserved from a target's budgets."""

    values: Mapping[str, int] = field(default_factory=dict)

    def get(self, name: str) -> int:
        return self.values.get(name, 0)


@dataclass(frozen=True, slots=True)
class TargetProfile:
    """Stable target identity, capabilities, and resource limits."""

    id: str
    version: int
    capabilities: frozenset[str] = frozenset()
    limits: object | None = None
