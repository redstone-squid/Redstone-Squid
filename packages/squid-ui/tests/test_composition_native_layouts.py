"""Native V2 layouts keep exact sections and page their enclosing panels."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantics import check_alternative
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.primitives import nodes as native
from squid_ui.primitives.constraints import Never, Paginate
from squid_ui.target_types import DiscordAdapter
from squid_ui_discord.limits import V2Limits
from squid_ui_discord.planning.discord import classic_target, components_v2_target

ADAPTER = AdapterProfile(DiscordAdapter, "native-layout.discord", "1")
TARGET = components_v2_target(ADAPTER)


async def untouched(event: sl.PressEvent) -> None:
    pytest.fail("planning invoked a callback")


@pytest.mark.parametrize(
    "accessory",
    [
        native.Thumbnail("https://example.com/image.png", "Preview", spoiler=True),
        native.Button("Open", untouched, "open"),
        native.LinkButton("Docs", "https://example.com"),
        native.RoutedButton("Route", "test.route"),
        native.PremiumButton(123),
    ],
)
def test_native_section_preserves_accessory_and_measures_its_cost(
    accessory: native.Thumbnail | native.Button | native.LinkButton | native.RoutedButton | native.PremiumButton,
) -> None:
    node = native.Section((native.Text("Body", overflow=Never()),), accessory)
    result = plan(sl.region(node, key="r", title="R"), target=TARGET, strict=True)
    assert result.allocation is not None
    (section,) = result.scene.body.children
    assert isinstance(section, scene.Section)
    assert section.texts[0].content == "Body"
    assert result.cost.get(Axis.COMPONENTS) == 3
    assert set(result.bindings) == ({"open"} if isinstance(accessory, native.Button) else set())
    if isinstance(accessory, native.Thumbnail):
        assert section.accessory == scene.Thumbnail(accessory.url, "Preview", spoiler=True)


@pytest.mark.parametrize("count", [0, 4])
def test_exact_sections_reject_invalid_authored_slot_counts(count: int) -> None:
    node = native.Section(tuple(native.Text("Body") for _ in range(count)), native.Thumbnail("https://example.com"))
    with pytest.raises(JointInfeasibleError):
        plan(sl.region(node, key="r", title="R"), target=TARGET)


def test_panel_continuations_preserve_metadata_and_only_bind_visible_accessories() -> None:
    panel = native.Panel(
        tuple(
            native.Section((native.Text(str(index)),), native.Button("Open", untouched, str(index)))
            for index in range(2)
        ),
        accent=0x123456,
        spoiler=True,
    )
    identity = OccurrenceId(("root", "r"))
    result = plan(
        sl.region(panel, key="r", title="R"),
        target=TARGET,
        composition=CompositionOptions(automatic=True, partitions={identity: (1, 2)}),
    )
    assert result.allocation is not None
    for index in range(2):
        page = result.allocation.select({identity: index})
        rendered = next(node for node in page.scene.body.children if isinstance(node, scene.Panel))
        assert rendered.accent == panel.accent and rendered.spoiler
        assert len(rendered.children) == 1 and isinstance(rendered.children[0], scene.Section)
        assert set(page.bindings) == {str(index)}


def test_separators_preserve_spacing_and_visibility() -> None:
    result = plan(sl.region(native.Sep(large=True, visible=False), key="r", title="R"), target=TARGET)
    assert result.scene.body.children == (scene.Separator(large=True, visible=False),)


@pytest.mark.parametrize(
    "node",
    [
        native.Panel((native.Panel((native.Text("Nested"),)),)),
        native.Thumbnail("https://example.com/image.png"),
        native.Section((native.Text("Body"),), native.Thumbnail("https://example.com", "x" * 1025)),
    ],
)
def test_invalid_native_placement_or_accessible_text_is_not_certified(node: native.Node) -> None:
    with pytest.raises(JointInfeasibleError):
        plan(sl.region(node, key="r", title="R"), target=TARGET)


def test_section_cannot_be_split_to_evade_component_limits() -> None:
    node = native.Section((native.Text("Body", overflow=Never()),), native.Button("Open", untouched, "open"))
    with pytest.raises(JointInfeasibleError):
        plan(
            sl.region(node, key="r", title="R"),
            target=components_v2_target(ADAPTER, limits=V2Limits(total_components=2)),
        )


def test_section_slots_cannot_contain_their_own_pager_chrome() -> None:
    node = native.Section(
        (native.Text("Body", overflow=Paginate(key="body")),), native.Thumbnail("https://example.com")
    )
    with pytest.raises(JointUnsupportedError) as error:
        plan(sl.region(node, key="r", title="R"), target=TARGET)
    assert "pagination cannot be nested in a Section" in str(error.value.diagnostics)


def test_checked_alternative_must_preserve_exact_section_boundary() -> None:
    source = native.Section((native.Text("Body", overflow=Never()),), native.Button("Open", untouched, "open"))
    choice = sl.alternative(
        sl.group(*source.texts, source.accessory),
        key="split",
        fidelity=sl.equivalent(),
        correspondence=(sl.corresponds("open", "@accessory"),),
    )
    with pytest.raises(LayoutInvariantError, match="preserve the Section boundary"):
        check_alternative(sl.alternatives(choice, content=source, key="choice"), choice)


def test_accessory_changes_invalidate_retained_allocation() -> None:
    session = PresentationState()
    node = native.Section((native.Text("Body"),), native.Thumbnail("https://example.com/first.png"))
    first = plan(sl.region(node, key="r", title="R"), target=TARGET, session=session)
    session.apply_updates(first.session_updates)
    changed = replace(node, accessory=native.Button("Open", untouched, "open"))
    second = plan(sl.region(changed, key="r", title="R"), target=TARGET, session=session)
    assert second.allocation is not first.allocation
    assert second.bindings["open"].handler is untouched


def test_native_v2_layout_has_a_concrete_classic_capability_error() -> None:
    target: Target[Any, Any, Any, Any] = classic_target(ADAPTER)
    with pytest.raises(JointUnsupportedError) as error:
        plan(sl.region(native.Panel((native.Text("Body"),)), key="r", title="R"), target=target)
    assert "native Components V2 layout on another target" in str(error.value.diagnostics)
