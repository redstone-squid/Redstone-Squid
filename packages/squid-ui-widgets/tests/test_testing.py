"""The shipped harness, exercised before the widget suites depend on it."""

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui_widgets import testing as wt


def _tabs() -> sp.Tabs:
    return sp.Tabs(
        (sp.Tab("general", "General", "general body"), sp.Tab("privacy", "Privacy", "privacy body")),
        key="settings",
    )


def _form(title: str = "Value") -> sl.forms.FormSpec:
    return sl.forms.FormSpec(title, (sl.forms.TextField(key="value", label="Value"),))


class TestMounted:
    def test_it_renders_the_machine_through_the_component_shell(self) -> None:
        harness = wt.mounted(_tabs())

        assert harness.texts() == ["general body"]
        assert harness.labels() == ["General", "Privacy"]

    async def test_a_press_advances_the_machine_and_the_next_read_re_renders(self) -> None:
        harness = wt.mounted(_tabs())

        await harness.press("settings.privacy")

        assert harness.state == sp.TabsState("privacy")
        assert harness.texts() == ["privacy body"]

    async def test_a_press_names_the_available_keys_when_the_control_is_absent(self) -> None:
        harness = wt.mounted(_tabs())

        with pytest.raises(AssertionError, match=r"no ActionControl keyed 'settings.gone'"):
            await harness.press("settings.gone")

    async def test_choosing_settles_a_picker_the_machine_owns(self) -> None:
        machine = sp.MultiChoice(
            "Choices",
            (
                sp.MultiChoiceGroup(
                    "group", "Group", (sl.semantic.Choice("one", "One"), sl.semantic.Choice("two", "Two"))
                ),
            ),
        )
        harness = wt.mounted(machine)
        key = next(node.key for node in engine.walk(harness.nodes) if isinstance(node, sl.semantic.Choices))

        await harness.choose(key, "two")

        assert harness.state.staged == ("two",)

    async def test_submitting_reaches_the_form_the_trigger_opens(self) -> None:
        machine = sp.CollectionEditor("Items", create=_form(), label=lambda value: str(value["value"]))
        harness = wt.mounted(machine)
        trigger = engine.find_all(harness.nodes, sl.semantic.FormTrigger)[0]

        await harness.submit(trigger.key, {"value": "first"})

        assert harness.texts() != []
        assert any("first" in text for text in harness.texts() + harness.labels())

    async def test_a_finish_action_reaches_the_recording_responder(self) -> None:
        harness = wt.mounted(_tabs(), finish_actions=["select:privacy"])

        assert not harness.finished
        await harness.press("settings.privacy")

        assert harness.finished

    async def test_the_handler_sees_the_transition_the_press_produced(self) -> None:
        seen: list[tuple[object, object]] = []

        async def changed(event: sp.TransitionEvent[sp.TabsState]) -> None:
            seen.append((event.previous, event.state))

        harness = wt.mounted(_tabs(), on_change=changed)
        await harness.press("settings.privacy")

        assert seen == [(sp.TabsState("general"), sp.TabsState("privacy"))]


class TestRouted:
    def test_it_renders_the_stateless_shell_and_reports_every_route_in_order(self) -> None:
        render = wt.routed(_tabs())

        assert render.texts() == ["general body"]
        assert [route.action for route in render.routes] == ["select:general", "select:privacy"]

    def test_every_route_reaches_the_ids_the_render_carries(self) -> None:
        render = wt.routed(_tabs())

        assert len(render.route_ids()) == len(render.routes)

    def test_a_route_carries_the_state_its_id_must_encode(self) -> None:
        render = wt.routed(_tabs())

        assert render.route_for("select:privacy").state == sp.TabsState("privacy")

    def test_it_renders_from_a_supplied_state_rather_than_the_initial_one(self) -> None:
        render = wt.routed(_tabs(), sp.TabsState("privacy"))

        assert render.texts() == ["privacy body"]

    def test_route_for_names_what_the_render_asked_for_when_there_is_no_match(self) -> None:
        render = wt.routed(_tabs())

        with pytest.raises(AssertionError, match=r"no route for 'gone'.*select:general"):
            render.route_for("gone")
