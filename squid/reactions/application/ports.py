"""Shared reaction application ports."""

from collections.abc import Sequence
from typing import Protocol

from squid.reactions.domain import ReactionActor, RoleMultiplier, WeightScope


class WeightPolicy(Protocol):
    """Calculate a positive reaction weight or reject an ineligible actor."""

    async def calculate(self, actor: ReactionActor, scope: WeightScope) -> float | None: ...


class ActorResolver(Protocol):
    """Resolve current framework-neutral member facts."""

    async def resolve(self, user_id: int, guild_id: int, scope: WeightScope) -> ReactionActor | None: ...


class RoleMultiplierProvider(Protocol):
    """Load the role multipliers configured for a reaction scope."""

    async def __call__(self, scope: WeightScope) -> Sequence[RoleMultiplier]: ...
