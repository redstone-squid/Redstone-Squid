"""Shared reaction application ports."""

from collections.abc import Sequence
from typing import Protocol

from squid.core.actors import UserActor
from squid.core.reactions.domain import RoleMultiplier, WeightScope


class WeightPolicy(Protocol):
    """Return a positive reaction weight, or None if the actor is ineligible."""

    async def calculate(self, actor: UserActor, scope: WeightScope) -> float | None: ...


class ActorResolver(Protocol):
    """Resolve current member facts as plain values, not library objects."""

    async def resolve(self, user_id: int, guild_id: int, scope: WeightScope) -> UserActor | None: ...


class RoleMultiplierProvider(Protocol):
    """Load the role multipliers configured for a reaction scope."""

    async def __call__(self, scope: WeightScope) -> Sequence[RoleMultiplier]: ...
