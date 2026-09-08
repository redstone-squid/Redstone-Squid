"""Reaction weighting values, holding no Discord library objects."""

from dataclasses import dataclass
from math import isfinite

from squid.core.errors import ValidationError
from squid.core.i18n import tr


@dataclass(frozen=True, slots=True)
class ReactionActor:
    """The member facts that authorize and weight a reaction, as plain values."""

    user_id: int
    guild_id: int = 0
    role_ids: frozenset[int] = frozenset()
    capabilities: frozenset[str] = frozenset()
    """Permission node names this actor was resolved to hold; see `VoteActor`."""


@dataclass(frozen=True, slots=True)
class WeightScope:
    """Which multiplier configuration a weight lookup reads; `kind` names the feature, `scope_id` the instance."""

    guild_id: int
    kind: str
    scope_id: int | None = None


@dataclass(frozen=True, slots=True)
class RoleMultiplier:
    """A role's weight in one reaction scope; raises `ValidationError` unless it is finite and positive."""

    scope: WeightScope
    role_id: int
    multiplier: float

    def __post_init__(self) -> None:
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = tr(t"Role multiplier must be finite and greater than zero.")
            raise ValidationError(msg)
