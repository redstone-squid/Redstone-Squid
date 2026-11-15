"""Native ladders use the same source and ownership checks as peer presentations."""

from typing import Any

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning import plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.target import Target
from squid_ui.presentations import Replacement
from squid_ui.primitives import nodes as native
from squid_ui.primitives.constraints import Never, Paginate
from squid_ui.target_types import DiscordAdapter
from squid_ui_discord.limits import V2Limits
from squid_ui_discord.planning.discord import classic_target, components_v2_target

ADAPTER = AdapterProfile(DiscordAdapter, "native-variants", "1")


@pytest.mark.parametrize(
    "target", [components_v2_target(ADAPTER), classic_target(ADAPTER)], ids=lambda target: target.id
)
def test_native_variants_filter_target_requirements_and_preserve_explicit_source(
    target: Target[Any, Any, Any, Any],
) -> None:
    node = native.Variants(
        (
            native.Variant(
                (native.Panel((native.Text("Shared", overflow=Never()),)),),
                requires=frozenset({"test.v2"}),
                correspondence=(sl.corresponds("@content", "source"),),
            ),
            native.Variant(
                (native.Text("Shared", overflow=Never()),),
                correspondence=(sl.corresponds("@content", "source"),),
            ),
        ),
        content=sl.paragraph("Shared", key="source"),
        key="variants",
    )
    result = plan(node, target=target, strict=True)
    assert result.allocation is not None
    assert "Shared" in scene.Codec.dumps(result.scene)
    assert not result.report.events


def test_native_variants_preserve_order_and_report_declared_replacement() -> None:
    node = native.Variants(
        (
            native.Variant((native.Text("long primary", overflow=Never()),)),
            native.Variant(
                (native.Text("short", overflow=Never()),),
                fidelity=native.Fidelity.LOSSY,
                contract=sl.losses(Replacement(sl.ref("@content"))),
            ),
        ),
    )
    target = components_v2_target(ADAPTER, limits=V2Limits(total_text=5))
    result = plan(node, target=target)
    assert result.allocation is not None
    assert "short" in scene.Codec.dumps(result.scene)
    assert [event.message for event in result.report.events] == ["Replacement"]
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, strict=True)


def test_native_variant_cannot_remove_a_pagination_owner() -> None:
    source = native.Text("one\ntwo", overflow=Paginate(key="pages", per=1))
    node = native.Variants(
        (
            native.Variant(
                (native.Text("one\ntwo", overflow=Never()),), correspondence=(sl.corresponds("@content", "pages"),)
            ),
        ),
        content=source,
    )
    with pytest.raises(LayoutInvariantError, match="boundary"):
        plan(sl.region(node, key="r", title="R"), target=components_v2_target(ADAPTER))


def test_uncontracted_lossy_variant_names_the_missing_contract_in_regions() -> None:
    node = native.Variants((native.Variant((native.Text("other"),), fidelity=native.Fidelity.LOSSY),))
    with pytest.raises(JointUnsupportedError) as error:
        plan(sl.region(node, key="r", title="R"), target=components_v2_target(ADAPTER))
    assert "Variant.contract" in str(error.value.diagnostics.violations)


def test_unkeyed_sibling_variant_groups_do_not_collide() -> None:
    result = plan(
        sl.stack(native.Variants.of(native.Text("one")), native.Variants.of(native.Text("two"))),
        target=components_v2_target(ADAPTER),
    )
    assert result.allocation is not None
    wire = scene.Codec.dumps(result.scene)
    assert "one" in wire and "two" in wire


@pytest.mark.parametrize("strict", [False, True])
def test_component_pressure_steps_only_the_needed_ladders_before_shortening_text(strict: bool) -> None:
    ladders = tuple(
        native.Variants(
            tuple(
                native.Variant((node,), correspondence=(sl.corresponds("@content", "source"),))
                for node in (
                    native.Panel((native.Text(f"panel {index}"), native.Text("detail"), native.Text("more detail"))),
                    native.Text(f"line {index}"),
                )
            ),
            content=sl.paragraph("Complete fixture content", key="source"),
            priority=index,
        )
        for index in range(12)
    )
    result = plan(sl.stack(*ladders), target=components_v2_target(ADAPTER), strict=strict)
    assert result.allocation is not None
    assert not result.report.events
    body = result.scene.body
    assert sum(isinstance(node, scene.Panel) for node in body.children) == 9
    assert [node.content for node in body.children if isinstance(node, scene.Text)] == ["line 0", "line 1", "line 2"]
