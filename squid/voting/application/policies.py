"""Built-in vote weighting policies."""

from collections.abc import Awaitable, Callable, Sequence
from typing import override

from squid.voting.application.ports import VoteWeightPolicy
from squid.voting.domain import RoleWeight, VoteActor, VoteSessionSnapshot

type RoleWeightProvider = Callable[[int, str], Awaitable[Sequence[RoleWeight]]]


class RoleVoteWeightPolicy(VoteWeightPolicy):
    """Use the highest configured role multiplier, with a 3x staff fallback."""

    def __init__(self, provider: RoleWeightProvider):
        self._provider = provider

    @override
    async def calculate(self, actor: VoteActor, session: VoteSessionSnapshot, emoji: str) -> float | None:
        if session.kind == "delete_log" and not (actor.is_trusted or actor.is_staff):
            return None
        weights = await self._provider(actor.guild_id, session.kind)
        configured = [weight.multiplier for weight in weights if weight.role_id in actor.role_ids]
        if configured:
            return max(configured)
        return 3.0 if actor.is_staff else 1.0
