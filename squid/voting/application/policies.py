"""Built-in vote weighting policies."""

from collections.abc import Awaitable, Callable, Sequence
from typing import override

from squid.core.actors import Actor
from squid.permissions.domain.catalogue import VOTE_LOG_DELETE_CAST, VOTE_WEIGHT_STAFF
from squid.reactions.application import RoleWeightPolicy
from squid.reactions.domain import RoleMultiplier, WeightScope
from squid.voting.application.ports import VoteWeightPolicy
from squid.voting.domain import RoleWeight, VoteKind, VoteSessionSnapshot

type RoleWeightProvider = Callable[[int, VoteKind], Awaitable[Sequence[RoleWeight]]]


class RoleVoteWeightPolicy(VoteWeightPolicy):
    """Use the highest configured role multiplier, with a 3x staff fallback.

    A matching role setting takes precedence even when its multiplier is below 3.
    With no matching role, the staff capability gives weight 3 and other eligible
    voters receive weight 1. Deletion votes also require the log-deletion voting
    capability; other voting kinds have no additional eligibility check here.
    """

    def __init__(self, provider: RoleWeightProvider):
        async def multipliers(scope: WeightScope) -> tuple[RoleMultiplier, ...]:
            weights = await provider(scope.guild_id, VoteKind(scope.kind))
            return tuple(RoleMultiplier(scope, weight.role_id, weight.multiplier) for weight in weights)

        self._policy = RoleWeightPolicy(
            multipliers,
            eligibility=lambda actor, scope: (
                scope.kind != "delete_log" or VOTE_LOG_DELETE_CAST.name in actor.capabilities
            ),
            staff_capability=VOTE_WEIGHT_STAFF.name,
        )

    @override
    async def calculate(self, actor: Actor, session: VoteSessionSnapshot) -> float | None:
        if actor.guild_id is None:
            return None
        return await self._policy.calculate(actor, WeightScope(actor.guild_id, session.kind))
