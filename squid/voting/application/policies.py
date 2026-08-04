"""Built-in vote weighting policies."""

from collections.abc import Awaitable, Callable, Sequence
from typing import override

from squid.reactions.application import RoleWeightPolicy
from squid.reactions.domain import RoleMultiplier, WeightScope
from squid.voting.application.ports import VoteWeightPolicy
from squid.voting.domain import RoleWeight, VoteActor, VoteSessionSnapshot

type RoleWeightProvider = Callable[[int, str], Awaitable[Sequence[RoleWeight]]]


class RoleVoteWeightPolicy(VoteWeightPolicy):
    """Use the highest configured role multiplier, with a 3x staff fallback."""

    def __init__(self, provider: RoleWeightProvider):
        async def multipliers(scope: WeightScope) -> tuple[RoleMultiplier, ...]:
            weights = await provider(scope.guild_id, scope.kind)
            return tuple(RoleMultiplier(scope, weight.role_id, weight.multiplier) for weight in weights)

        self._policy = RoleWeightPolicy(
            multipliers,
            eligibility=lambda actor, scope: scope.kind != "delete_log" or actor.is_trusted or actor.is_staff,
        )

    @override
    async def calculate(self, actor: VoteActor, session: VoteSessionSnapshot, emoji: str) -> float | None:
        return await self._policy.calculate(actor, WeightScope(actor.guild_id, session.kind))
