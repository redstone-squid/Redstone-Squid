"""Framework-neutral reaction weighting values."""

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True, slots=True)
class ReactionActor:
    """Framework-neutral member facts used to authorize and weight a reaction."""

    user_id: int
    guild_id: int = 0
    role_ids: frozenset[int] = frozenset()
    capabilities: frozenset[str] = frozenset()
    """Permission node names this actor was resolved to hold; see `VoteActor`."""


@dataclass(frozen=True, slots=True)
class WeightScope:
    """The configuration bucket a weight lookup belongs to."""

    guild_id: int
    kind: str
    scope_id: int | None = None


@dataclass(frozen=True, slots=True)
class RoleMultiplier:
    """A role multiplier configured for one reaction scope."""

    scope: WeightScope
    role_id: int
    multiplier: float

    def __post_init__(self) -> None:
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = "Role multiplier must be finite and greater than zero."
            raise ValueError(msg)
