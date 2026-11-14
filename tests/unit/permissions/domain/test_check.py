"""A permission result keeps aggregate access distinct from individual grants."""

import pytest

from squid.permissions.domain import CheckMode, Decision, PermissionCheck, Reason


@pytest.mark.parametrize(("mode", "allowed"), [("all", False), ("any", True)])
def test_mixed_results_keep_granted_nodes_and_denial_reasons(mode: CheckMode, allowed: bool) -> None:
    decisions = (
        Decision("build.submission.edit", allowed=True, reason=Reason.RULE),
        Decision("build.submission.approve", allowed=False, reason=Reason.FORBIDDEN),
    )
    result = PermissionCheck(decisions, mode=mode)

    assert result.allowed is allowed
    assert result.allowed_nodes == frozenset({"build.submission.edit"})
    assert result.decisions == decisions
    assert result.decisions[1].reason is Reason.FORBIDDEN


@pytest.mark.parametrize("mode", ["all", "any"])
def test_denied_nodes_never_become_capabilities(mode: CheckMode) -> None:
    result = PermissionCheck((Decision("bot.tree.sync", allowed=False, reason=Reason.DEFAULT),), mode=mode)

    assert not result.allowed
    assert not result.allowed_nodes
