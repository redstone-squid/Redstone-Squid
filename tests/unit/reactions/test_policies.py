from math import inf, nan

import pytest

from squid.reactions.application import RoleWeightPolicy
from squid.reactions.domain import ReactionActor, RoleMultiplier, WeightScope


async def test_role_policy_uses_highest_matching_multiplier() -> None:
    scope = WeightScope(10, "starboard", 5)

    async def multipliers(requested: WeightScope) -> tuple[RoleMultiplier, ...]:
        assert requested == scope
        return (RoleMultiplier(scope, 20, 1.5), RoleMultiplier(scope, 30, 2.5))

    policy = RoleWeightPolicy(multipliers, staff_multiplier=1)

    assert await policy.calculate(ReactionActor(1, 10, frozenset({20, 30})), scope) == 2.5
    assert await policy.calculate(ReactionActor(1, 10), scope) == 1


async def test_role_policy_applies_eligibility_before_loading_configuration() -> None:
    called = False

    async def multipliers(scope: WeightScope) -> tuple[RoleMultiplier, ...]:
        nonlocal called
        called = True
        return ()

    policy = RoleWeightPolicy(multipliers, eligibility=lambda actor, scope: actor.is_trusted)

    assert await policy.calculate(ReactionActor(1), WeightScope(10, "delete_log")) is None
    assert not called


@pytest.mark.parametrize("multiplier", [0, -1, inf, nan])
def test_role_multiplier_must_be_positive_and_finite(multiplier: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        RoleMultiplier(WeightScope(10, "starboard", 5), 20, multiplier)


@pytest.mark.parametrize("multiplier", [0, -1, inf, nan])
def test_staff_multiplier_must_be_positive_and_finite(multiplier: float) -> None:
    async def multipliers(scope: WeightScope) -> tuple[RoleMultiplier, ...]:
        return ()

    with pytest.raises(ValueError, match="finite"):
        RoleWeightPolicy(multipliers, staff_multiplier=multiplier)
