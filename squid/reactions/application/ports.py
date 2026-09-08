"""Shared reaction application ports."""

from collections.abc import Sequence
from typing import Protocol

from squid.reactions.domain import ReactionActor, RoleMultiplier, WeightScope


class WeightPolicy(Protocol):
    """Calculate a positive reaction weight or reject an ineligible actor."""

    async def calculate(self, actor: ReactionActor, scope: WeightScope) -> float | None:
        """The actor's weight in this scope, or `None` if they may not vote here at all."""
        ...


class ActorResolver(Protocol):
    """Resolve current member facts as plain values, not library objects."""

    async def resolve(self, user_id: int, guild_id: int, scope: WeightScope) -> ReactionActor | None:
        """The member's roles and capabilities now, or `None` if they are not in the guild."""
        ...


class RoleMultiplierProvider(Protocol):
    """Load the role multipliers configured for a reaction scope."""

    async def __call__(self, scope: WeightScope) -> Sequence[RoleMultiplier]:
        """The scope's configured multipliers; roles absent from the result weigh 1."""
        ...
