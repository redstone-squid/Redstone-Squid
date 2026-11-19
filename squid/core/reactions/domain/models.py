"""Shared inputs for weighting voting and starboard reactions.

Actor carries Discord member facts and already-resolved capability names.
WeightScope selects the guild and feature configuration. A policy combines those
inputs with configured role multipliers to return a positive weight or reject an
ineligible actor, without depending on Discord library objects.
"""

from dataclasses import dataclass
from math import isfinite

from squid.core.errors import ValidationError
from squid.core.i18n import tr


@dataclass(frozen=True, slots=True)
class WeightScope:
    """The server and feature whose reaction multipliers should be used."""

    guild_id: int
    kind: str
    scope_id: int | None = None
    """Optional configuration ID within the feature, such as one starboard's ID."""


@dataclass(frozen=True, slots=True)
class RoleMultiplier:
    """A role multiplier configured for one reaction scope."""

    scope: WeightScope
    role_id: int
    multiplier: float

    def __post_init__(self) -> None:
        if not isfinite(self.multiplier) or self.multiplier <= 0:
            msg = tr(t"reactions-role-multiplier-finite = Role multiplier must be finite and greater than zero.")
            raise ValidationError(msg)
