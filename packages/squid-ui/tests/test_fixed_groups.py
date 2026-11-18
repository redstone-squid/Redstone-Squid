"""Fixed item groups specify boundaries, without bypassing target certification."""

import pytest

import squid_ui as sl
import squid_ui.ext.html as html
from squid_ui.ext.discord import testing as discord_testing
from squid_ui.planning import JointInfeasibleError, plan


def test_fixed_groups_preserve_membership_and_short_final_group() -> None:
    document = sl.bullets(*(sl.bullet(str(i), key=str(i)) for i in range(5)), key="results", group_size=2)
    assert document.group_count == 3
    result = plan(document, target=html.target())
    assert result.allocation is not None
    region = next(iter(result.allocation.regions.values()))
    assert region.cuts == (2, 4, 5)
    assert [len(anchors) for anchors in region.anchors] == [2, 2, 1]


def test_fixed_groups_fail_when_the_group_cannot_fit() -> None:
    target = discord_testing.target_profile("groups")
    entries = (sl.bullet("a" * 2100, key="first"), sl.bullet("b" * 2100, key="second"))
    fixed = sl.bullets(*entries, key="results", group_size=2)
    assert fixed.group_count == 1
    with pytest.raises(JointInfeasibleError):
        plan(fixed, target=target)
    adaptive = plan(sl.bullets(*entries, key="results", page_size=2), target=target)
    assert adaptive.allocation is not None
    assert len(next(iter(adaptive.allocation.regions.values())).cuts) > 1
    assert fixed.group_count == 1


def test_fixed_groups_validate_the_count_policy() -> None:
    with pytest.raises(ValueError, match="choose group_size"):
        sl.bullets("one", key="results", group_size=2, page_size=2)
    with pytest.raises(ValueError, match="group_size must be positive"):
        sl.bullets("one", key="results", group_size=0)
    assert sl.bullets(key="empty", group_size=2).group_count == 0
