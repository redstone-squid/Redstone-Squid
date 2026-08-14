"""Reusable reaction weighting policies."""

from collections.abc import Callable
from math import isfinite
from typing import override

from squid.reactions.application.ports import RoleMultiplierProvider, WeightPolicy
from squid.reactions.domain import ReactionActor, WeightScope

type Eligibility = Callable[[ReactionActor, WeightScope], bool]


class RoleWeightPolicy(WeightPolicy):
    """Use the highest matching role multiplier with an optional staff fallback."""

    def __init__(
        self,
        provider: RoleMultiplierProvider,
        *,
        eligibility: Eligibility | None = None,
        staff_capability: str = "",
        staff_multiplier: float = 3.0,
    ) -> None:
        if not isfinite(staff_multiplier) or staff_multiplier <= 0:
            msg = "Staff multiplier must be finite and greater than zero."
            raise ValueError(msg)
        self._provider = provider
        self._eligibility = eligibility
        # A node *name*, supplied by whichever context uses this policy, so the
        # reactions context stays generic and never learns the catalogue.
        self._staff_capability = staff_capability
        self._staff_multiplier = staff_multiplier

    @override
    async def calculate(self, actor: ReactionActor, scope: WeightScope) -> float | None:
        if self._eligibility is not None and not self._eligibility(actor, scope):
            return None
        multipliers = await self._provider(scope)
        configured = [item.multiplier for item in multipliers if item.role_id in actor.role_ids]
        if configured:
            return max(configured)
        has_staff_capability = bool(self._staff_capability) and self._staff_capability in actor.capabilities
        return self._staff_multiplier if has_staff_capability else 1.0
