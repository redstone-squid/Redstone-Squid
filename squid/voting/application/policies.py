"""Built-in vote weighting policies."""

from collections.abc import Awaitable, Callable, Sequence
from typing import override

from squid.permissions.domain.catalogue import VOTE_LOG_DELETE_CAST, VOTE_WEIGHT_STAFF
from squid.reactions.application import RoleWeightPolicy
from squid.reactions.domain import ReactionActor, RoleMultiplier, WeightScope
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
            eligibility=lambda actor, scope: (
                scope.kind != "delete_log" or VOTE_LOG_DELETE_CAST.name in actor.capabilities
            ),
            staff_capability=VOTE_WEIGHT_STAFF.name,
        )

    @override
    async def calculate(self, actor: VoteActor, session: VoteSessionSnapshot, emoji: str) -> float | None:
        reaction_actor = ReactionActor(
            user_id=actor.discord_id,
            guild_id=actor.guild_id,
            role_ids=actor.role_ids,
            capabilities=actor.capabilities,
        )
        return await self._policy.calculate(reaction_actor, WeightScope(actor.guild_id, session.kind))
