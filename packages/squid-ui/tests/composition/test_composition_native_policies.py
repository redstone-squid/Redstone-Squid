"""Native reservations and break annotations retain their authored constraints."""

from dataclasses import replace

import pytest

import squid_ui as sl
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.discord.limits import V2Limits
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantics import check_alternative
from squid_ui.presentation import PresentationState
from squid_ui.primitives import nodes as native
from squid_ui.primitives.constraints import Never, Paginate, Truncate
from squid_ui.target_types import DiscordAdapter

ADAPTER = AdapterProfile(DiscordAdapter, "native-policies", "1")
TARGET = components_v2_target(ADAPTER)


def test_native_budget_keeps_finite_stretch_and_authored_shortening() -> None:
    node = native.Budget((native.Text("1234567", overflow=Never()),), minimum=2, preferred=4, stretch=2)
    with pytest.raises(JointInfeasibleError):
        plan(node, target=TARGET)
    permitted = replace(node, children=(native.Text("1234567", overflow=Truncate()),))
    result = plan(permitted, target=TARGET)
    assert result.allocation is not None
    assert result.cost.get(Axis.DISPLAY_TEXT) == 6
    assert [event.message for event in result.report.events] == ["Shortening"]


def test_native_best_effort_relaxes_only_the_reservation() -> None:
    target = components_v2_target(ADAPTER, limits=V2Limits(total_text=4))
    hard = native.Budget((native.Text("Fits", overflow=Never()),), minimum=8, preferred=8)
    with pytest.raises(JointInfeasibleError):
        plan(hard, target=target)
    result = plan(replace(hard, best_effort=True), target=target, strict=True)
    assert result.allocation is not None
    assert result.cost.get(Axis.DISPLAY_TEXT) == 4
    assert not result.report.events
    with pytest.raises(JointInfeasibleError):
        plan(replace(hard, best_effort=True, children=(native.Text("Too long", overflow=Never()),)), target=target)


@pytest.mark.parametrize("unbreakable", [False, True])
def test_native_break_annotations_forbid_partial_groups(unbreakable: bool) -> None:
    first = native.Text("First", overflow=Never())
    second = native.Text("Second", overflow=Never())
    if unbreakable:
        children = (native.Break((first, second), unbreakable=True),)
    else:
        children = (native.Break((first,), keep_with_next=True), second)
    node = sl.region(*children, key="r", title="R")
    result = plan(node, target=TARGET)
    assert result.allocation is not None
    identity = OccurrenceId(("root", "r"))
    with pytest.raises(LayoutInvariantError):
        plan(node, target=TARGET, composition=CompositionOptions(automatic=True, partitions={identity: (1, 2)}))


@pytest.mark.parametrize(
    "source,changed",
    [
        (native.Break((native.Text("Body"),), unbreakable=True), native.Break((native.Text("Body"),))),
        (
            native.Budget((native.Text("Body"),), minimum=8, preferred=8),
            native.Budget((native.Text("Body"),), minimum=8, preferred=8, best_effort=True),
        ),
    ],
)
def test_checked_alternatives_cannot_weaken_native_policies(source: native.Node, changed: native.Node) -> None:
    choice = sl.alternative(changed, key="weakened", fidelity=sl.equivalent())
    with pytest.raises(LayoutInvariantError, match=r"preserve the .* boundary"):
        check_alternative(sl.alternatives(choice, content=source, key="choice"), choice)


async def turn(event: sl.PageEvent) -> None:
    pytest.fail("planning invoked a page handler")


def test_controlled_native_pages_reuse_through_budget_and_break_wrappers() -> None:
    session = PresentationState()

    def node(index: int) -> native.Budget:
        text = native.Text("First\nSecond", overflow=Paginate(key="body", per=1, page=sl.controlled(index, turn)))
        return native.Budget((native.Break((text,)),), minimum=0, preferred=100)

    first = plan(node(0), target=TARGET, session=session)
    session.apply_updates(first.session_updates)
    second = plan(node(1), target=TARGET, session=session)
    assert second.allocation is first.allocation
    assert "Second" in sl.scene.Codec.dumps(second.scene)


def test_classic_rejects_native_character_reservations_with_a_capability_reason() -> None:
    node = sl.region(native.Budget((native.Text("Body"),), minimum=0, preferred=10), key="r", title="R")
    with pytest.raises(JointUnsupportedError) as error:
        plan(node, target=classic_target(ADAPTER))
    assert "Budget requires one shared character axis" in str(error.value.diagnostics)
