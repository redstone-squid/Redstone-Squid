"""Opaque native items must declare and independently validate their composition."""

from dataclasses import dataclass, replace

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.errors import DrawInvariantError
from squid_ui.ext.discord.limits import V2Limits
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.extensions import ExtensionKind
from squid_ui.planning import AtomicContract, plan
from squid_ui.planning.adapter import AdapterProfile, PreparedExtension
from squid_ui.planning.composition import Placement, PlanningContext, ResourceIdentity, SharedUse
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.native_extensions import NativeComposition, NativePlacement
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.presentations import budget
from squid_ui.primitives import nodes as native
from squid_ui.primitives.constraints import Never
from squid_ui.target_types import DiscordAdapter

ADAPTER = AdapterProfile(DiscordAdapter, "composition.extensions", "1")
KIND = ExtensionKind[str, object]("test.badge")


def measure(node: scene.Extension, resource: object, context: PlanningContext) -> ResourceCost:
    assert node.kind == KIND.name
    assert isinstance(resource, str)
    return ResourceCost({Axis.COMPONENTS: 1, Axis.DISPLAY_TEXT: len(resource)})


def badge(
    label: str,
    *,
    placements: frozenset[NativePlacement] = frozenset({"root", "panel"}),
    asset: Asset | None = None,
) -> native.RawItem:
    uses = (
        (SharedUse(ResourceIdentity("attachment", asset.key), asset, ResourceCost({Axis.ATTACHMENTS: 1})),)
        if asset
        else ()
    )
    return native.RawItem(
        lambda: label,
        text_cost=len(label),
        kind=KIND.name,
        composition=NativeComposition(
            AtomicContract(
                ResourceCost({Axis.COMPONENTS: 1, Axis.DISPLAY_TEXT: len(label)}),
                {use.identity: use.cost for use in uses},
            ),
            measure,
            placements,
            (label,),
            uses,
        ),
    )


@dataclass(frozen=True)
class BadgeAdapter:
    def prepare(self, payload: str) -> PreparedExtension[object]:
        return PreparedExtension(ResourceCost({Axis.COMPONENTS: 1, Axis.DISPLAY_TEXT: len(payload)}), {}, payload)

    def compose(self, payload: str, *, context: PlanningContext) -> native.RawItem:
        return badge(payload)


def test_registered_extension_participates_in_regions_and_delivers_its_resource() -> None:
    target = components_v2_target(replace(ADAPTER, extensions={KIND.name: BadgeAdapter()}))
    node = native.Extension(KIND, 1, "Ready", native.Text("Ready", overflow=Never()))
    result = plan(sl.region(node, key="r", title="R"), target=target)
    assert result.allocation is not None
    extension = result.scene.body.children[0]
    assert isinstance(extension, scene.Extension)
    resource_key = extension.payload["resource"]
    assert isinstance(resource_key, str)
    assert result.resources[resource_key] == "Ready"
    assert extension.version == 1
    assert result.cost.get(Axis.DISPLAY_TEXT) == 5


def test_extension_uses_its_authored_fallback_without_a_native_adapter() -> None:
    node = native.Extension(KIND, 1, "Ready", native.Text("Portable", overflow=Never()))
    result = plan(sl.region(node, key="r", title="R"), target=classic_target(ADAPTER))
    assert result.allocation is not None
    assert "Portable" in scene.Codec.dumps(result.scene)


def test_opaque_shared_assets_are_charged_once_across_siblings() -> None:
    asset = sl.file(b"data", filename="report.txt")
    result = plan(
        budget(sl.stack(badge("A", asset=asset), badge("B", asset=asset)), limits={Axis.ATTACHMENTS: 1}),
        target=components_v2_target(ADAPTER),
    )
    assert result.cost.get(Axis.ATTACHMENTS) == 1
    assert result.cost.get(Axis.COMPONENTS) == 2
    assert result.resources[f"asset:{asset.key}"] is asset


def test_opaque_cost_is_checked_against_independent_native_measurement() -> None:
    node = badge("Ready")
    node = replace(node, factory=lambda: "longer than the declared text")
    with pytest.raises(DrawInvariantError, match="independent measurement"):
        plan(sl.region(node, key="r", title="R"), target=components_v2_target(ADAPTER))


@pytest.mark.parametrize("placement", ["role", "slot", "capability"])
def test_opaque_contract_prevents_illegal_placements_and_missing_capabilities(placement: str) -> None:
    node = badge("Ready")
    assert node.composition is not None
    if placement == "role":
        node = replace(
            node,
            composition=replace(
                node.composition, contract=replace(node.composition.contract, placements=frozenset({Placement.CONTENT}))
            ),
        )
    elif placement == "slot":
        node = replace(node, composition=replace(node.composition, placements=frozenset({"row"})))
    else:
        node = replace(
            node,
            composition=replace(
                node.composition, contract=replace(node.composition.contract, capabilities=frozenset({"missing"}))
            ),
        )
    with pytest.raises(JointUnsupportedError if placement == "capability" else JointInfeasibleError):
        plan(sl.region(sl.paragraph("Body"), key="r", title="R", header=(node,)), target=components_v2_target(ADAPTER))


def test_opaque_row_and_accessory_contracts_count_native_components() -> None:
    node = sl.stack(
        native.Row((badge("Button", placements=frozenset({"row"})),)),
        native.Section((native.Text("Text"),), badge("Accessory", placements=frozenset({"accessory"}))),
    )
    result = plan(sl.region(node, key="r", title="R"), target=components_v2_target(ADAPTER))
    assert result.cost.get(Axis.COMPONENTS) == 5
    with pytest.raises(JointInfeasibleError):
        plan(node, target=components_v2_target(ADAPTER, limits=V2Limits(total_components=4)))


def test_legacy_raw_cost_does_not_silently_become_a_composition_contract() -> None:
    with pytest.raises(JointUnsupportedError) as error:
        plan(sl.region(native.RawItem(object), key="r", title="R"), target=components_v2_target(ADAPTER))
    assert "composition contract" in str(error.value.diagnostics.violations)
