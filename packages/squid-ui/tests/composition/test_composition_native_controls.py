"""Native Discord controls retain exact structure and callback ownership in regions."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
import squid_ui.ext.html
from squid_ui import scene
from squid_ui.capabilities import Capability
from squid_ui.entity import EntityKind, EntityRef, EntityType
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.forms import FormBinding, FormSpec, SubmitEvent, TextField
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.semantics import check_alternative
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.presentations import alternative, alternatives, equivalent
from squid_ui.primitives import nodes as native
from squid_ui.primitives.constraints import Never
from squid_ui.selectors import query, select
from squid_ui.target_types import DiscordAdapter

ADAPTER = AdapterProfile(DiscordAdapter, "native-controls.discord", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [components_v2_target(ADAPTER), classic_target(ADAPTER)]


async def untouched(event: sl.PressEvent) -> None:
    pytest.fail("planning invoked a native callback")


def rows(body: scene.Body) -> tuple[tuple[scene.Node, ...], ...]:
    match body:
        case scene.ComponentsV2(children=children):
            return tuple(child.items for child in children if isinstance(child, scene.Row))
        case scene.ClassicMessage(rows=controls):
            return tuple(row.controls for row in controls)
        case _:
            pytest.fail("expected a Discord scene")


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
@pytest.mark.parametrize("grouped", [False, True])
def test_premium_buttons_retain_skus_in_exact_and_automatic_rows(
    target: Target[Any, Any, Any, Any], grouped: bool
) -> None:
    buttons = tuple(native.PremiumButton(index + 1) for index in range(6 if grouped else 2))
    node = native.ControlGroup(buttons) if grouped else native.Row(buttons)
    result = plan(sl.region(node, key="r", title="R"), target=target, strict=True)
    assert result.allocation is not None
    assert [
        item.sku_id for row in rows(result.scene.body) for item in row if isinstance(item, scene.PremiumButton)
    ] == [button.sku_id for button in buttons]
    assert [len(row) for row in rows(result.scene.body)] == ([5, 1] if grouped else [2])
    assert not result.bindings and not result.report.events


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_premium_buttons_require_the_target_capability(
    target: Target[Any, Any, Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        target.dialect, "capabilities", target.protocol_capabilities - {Capability.ACTIONS_DISCORD_PREMIUM}
    )
    with pytest.raises(JointUnsupportedError) as error:
        plan(sl.region(native.PremiumButton(123), key="r", title="R"), target=target)
    assert "Discord premium buttons" in str(error.value.diagnostics)


def test_premium_button_alternative_cannot_silently_change_the_sku() -> None:
    source = native.PremiumButton(123)
    choice = alternative(native.PremiumButton(456), key="changed", fidelity=equivalent())
    with pytest.raises(LayoutInvariantError, match="interaction"):
        check_alternative(alternatives(choice, content=source, key="choice"), choice)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_native_rows_preserve_keys_emoji_and_routed_controls(target: Target[Any, Any, Any, Any]) -> None:
    controls = native.Row(
        (
            native.Button(None, untouched, "emoji", emoji="✅"),
            native.RoutedButton("Route", "test.route", disabled=True),
            native.LinkButton("Docs", "https://example.com", disabled=True),
        )
    )
    result = plan(sl.region(controls, key="r", title="R"), target=target, strict=True)
    assert result.allocation is not None
    assert set(result.bindings) == {"emoji"}
    assert result.bindings["emoji"].handler is untouched
    rendered = rows(result.scene.body)
    assert len(rendered) == 1 and len(rendered[0]) == 3
    button, route, link = rendered[0]
    assert isinstance(button, scene.Button) and button.emoji is not None
    assert isinstance(route, scene.RoutedButton) and route.route_id == "test.route" and route.disabled
    assert isinstance(link, scene.Link) and link.url == "https://example.com" and link.disabled


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_exact_rows_cannot_be_repacked_but_control_groups_can(target: Target[Any, Any, Any, Any]) -> None:
    buttons = tuple(native.Button(str(index), untouched, str(index)) for index in range(6))
    with pytest.raises(JointInfeasibleError):
        plan(sl.region(native.Row(buttons), key="r", title="R"), target=target)
    with pytest.raises(JointInfeasibleError):
        plan(sl.region(native.Row(()), key="r", title="R"), target=target)
    result = plan(sl.region(native.ControlGroup(buttons), key="r", title="R"), target=target)
    assert [len(row) for row in rows(result.scene.body)] == [5, 1]


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_control_groups_can_split_across_pages(target: Target[Any, Any, Any, Any]) -> None:
    buttons = tuple(native.Button(str(index), untouched, str(index)) for index in range(6))
    region = OccurrenceId(("root", "r"))
    result = plan(
        sl.region(native.ControlGroup(buttons), key="r", title="R"),
        target=target,
        composition=CompositionOptions(automatic=True, partitions={region: (3, 6)}),
    )
    assert result.allocation is not None
    assert set(result.allocation.select({region: 0}).bindings) == {"0", "1", "2"}
    assert set(result.allocation.select({region: 1}).bindings) == {"3", "4", "5"}


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_native_form_button_keeps_its_submission_binding(target: Target[Any, Any, Any, Any]) -> None:
    async def submit(event: SubmitEvent) -> None:
        pytest.fail("planning submitted a form")

    form = FormBinding("form", FormSpec("Name", (TextField(key="name", label="Name"),)), submit)
    node = native.FormButton("Open", untouched, "open", form=form)
    result = plan(sl.region(node, key="r", title="R"), target=target)
    assert result.form_bindings["form"] is form
    choice = alternative(replace(node, form=None), key="changed", fidelity=equivalent())
    with pytest.raises(LayoutInvariantError, match="undeclared interaction change"):
        check_alternative(alternatives(choice, content=node, key="group"), choice)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_native_bindings_only_exist_on_their_visible_page(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(
        native.Row((native.Button("First", untouched, "first"),)),
        native.Row((native.Button("Second", untouched, "second"),)),
        key="r",
        title="R",
    )
    region = OccurrenceId(("root", "r"))
    result = plan(node, target=target, composition=CompositionOptions(automatic=True, partitions={region: (1, 2)}))
    assert result.allocation is not None
    assert "first" in result.allocation.select({region: 0}).bindings
    assert "second" not in result.allocation.select({region: 0}).bindings
    assert "second" in result.allocation.select({region: 1}).bindings
    assert "first" not in result.allocation.select({region: 1}).bindings


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_native_selectors_preserve_defaults_and_callbacks(target: Target[Any, Any, Any, Any]) -> None:
    async def choose(event: sl.SelectionEvent) -> None:
        pytest.fail("planning selected an option")

    async def entities(event: sl.EntitySelectionEvent) -> None:
        pytest.fail("planning selected an entity")

    choices = native.SelectMenu(
        (native.Option("One", "1", default=True), native.Option("Two", "2")), choose, "choices", min_values=0
    )
    people = native.EntitySelect(
        EntityType.USER, entities, "people", default_values=(EntityRef(EntityKind.USER, 123),), disabled=True
    )
    result = plan(sl.region(choices, people, key="r", title="R"), target=target)
    assert result.bindings["choices"].handler is choose
    assert result.bindings["people"].handler is entities
    selected = query(result.scene, select(scene.Select)).one().node
    entity = query(result.scene, select(scene.EntitySelect)).one().node
    assert selected.options[0].default and selected.min_values == 0
    assert entity.default_values == people.default_values and entity.disabled


def test_checked_alternatives_cannot_replace_native_callbacks_or_split_exact_rows() -> None:
    async def changed(event: sl.PressEvent) -> None:
        pass

    button = native.Button("Action", untouched, "action")
    other = replace(button, on_click=changed)
    choice = alternative(other, key="changed", fidelity=equivalent())
    with pytest.raises(LayoutInvariantError, match="undeclared interaction change"):
        check_alternative(alternatives(choice, content=button, key="group"), choice)
    source = native.Row((button, native.Button("Second", untouched, "second")))
    choice = alternative(sl.group(*source.items), key="split", fidelity=equivalent())
    with pytest.raises(LayoutInvariantError, match="preserve the Row boundary"):
        check_alternative(alternatives(choice, content=source, key="group"), choice)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_native_callback_change_invalidates_retained_allocation(target: Target[Any, Any, Any, Any]) -> None:
    async def changed(event: sl.PressEvent) -> None:
        pass

    session = PresentationState()
    button = native.Button("Action", untouched, "action")
    first = plan(sl.region(button, key="r", title="R"), target=target, session=session)
    session.apply_updates(first.session_updates)
    second = plan(sl.region(replace(button, on_click=changed), key="r", title="R"), target=target, session=session)
    assert second.allocation is not first.allocation
    assert second.bindings["action"].handler is changed


def test_native_discord_rows_have_a_concrete_non_discord_capability_error() -> None:
    target: Target[Any, Any, Any, Any] = squid_ui.ext.html.target()
    with pytest.raises(JointUnsupportedError) as error:
        plan(
            sl.region(native.Row((native.Button("Action", untouched, "action"),)), key="r", title="R"),
            target=target,
        )
    assert "Discord native controls" in str(error.value.diagnostics)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_lossless_native_text_preserves_roles_and_structured_content(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(
        native.Heading("Heading", overflow=Never()),
        native.Text(sl.text_link("Docs", "https://example.com"), overflow=Never()),
        native.Code("a = ```", lang="py", overflow=Never()),
        native.Footer("Footer", overflow=Never()),
        key="r",
        title="R",
    )
    result = plan(node, target=target, strict=True)
    assert result.allocation is not None
    wire = scene.Codec.dumps(result.scene)
    assert "## Heading" in wire and "https://example.com" in wire
    assert "```py" in wire and "-# Footer" in wire
    assert not any(event.code == "composition.effect" for event in result.report.events)
