"""Run one state machine through ComponentDriver and RouteDriver.

The machine asks for controls without choosing how events reach it. ComponentDriver
attaches handlers to those controls; RouteDriver asks the host to encode transitions
in routing IDs.
This probe isolates that wiring from the behavior of individual widgets.
"""

from collections.abc import Mapping
from typing import override

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.semantic import ActionControl, Choices, Controlled, FormTrigger, RoutedActionControl, RoutedChoices
from squid_ui_widgets import testing as wt
from squid_ui_widgets.drivers import FormPresentingMachine


def _form() -> sl.forms.FormSpec:
    return sl.forms.FormSpec("Value", (sl.forms.TextField(key="value", label="Value"),))


class Echo(sl.Component[sl.ComponentsV2Target]):
    """A component a machine may embed in its content."""

    @override
    def render(self) -> sl.LayoutNode:
        return sl.paragraph("embedded")


class Probe:
    """Request a button, picker, and form from either driver."""

    def __init__(self, *, embed: bool = False) -> None:
        self.embed = embed
        self.child = Echo() if embed else None

    @property
    def initial_state(self) -> sp.TabsState:
        return sp.TabsState("start")

    def render(
        self,
        state: sp.TabsState,
        controls: sp.MachineControls[sp.TabsState, sl.ComponentsV2Target],
    ) -> sl.DocumentLike[sl.ComponentsV2Target]:
        content = controls.content(
            [self.child] if self.child is not None else [sl.paragraph(state.selected)], prefix="body"
        )
        # Mounted forms are layout nodes; routed forms belong inside action controls.
        trigger = controls.form(_form(), "edit", key="probe.edit", label="Edit")
        return sl.stack(
            *content,
            trigger if isinstance(trigger, FormTrigger) else None,
            sl.action_controls(
                controls.action_control("Go", "go", key="probe.go"),
                trigger if isinstance(trigger, RoutedActionControl) else None,
                key="probe.actions",
            ),
            controls.choices(
                (sl.semantic.Choice("one", "One"), sl.semantic.Choice("two", "Two")),
                "pick",
                key="probe.pick",
                selected=("one",),
                minimum=1,
                maximum=1,
            ),
        )

    def transition(
        self,
        state: sp.TabsState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> sp.TabsState:
        if action == "go":
            return sp.TabsState("went")
        if action == "pick":
            return sp.TabsState(values[0] if values else state.selected)
        if action == "edit" and submitted is not None:
            return sp.TabsState(str(submitted["value"]))
        return state


class OptionalProbe:
    @property
    def initial_state(self) -> str | None:
        return "machine default"

    def render(
        self,
        state: str | None,
        controls: sp.MachineControls[str | None, sl.ComponentsV2Target],
    ) -> sl.DocumentLike[sl.ComponentsV2Target]:
        del controls
        return sl.paragraph(repr(state))

    def transition(
        self,
        state: str | None,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> str | None:
        del action, values, submitted
        return state


class TestWhatEachShellInjects:
    def test_the_component_shell_injects_handler_backed_controls(self) -> None:
        nodes = wt.mounted(Probe()).nodes

        assert isinstance(engine.find(nodes, ActionControl, key="probe.go"), ActionControl)
        assert isinstance(engine.find(nodes, Choices, key="probe.pick").selection, Controlled)
        assert isinstance(engine.find(nodes, FormTrigger, key="probe.edit"), FormTrigger)

    def test_the_routed_shell_injects_id_backed_controls_for_the_very_same_render(self) -> None:
        """Routing IDs replace handlers without changing the machine's control keys."""
        nodes = wt.routed(Probe()).nodes

        assert isinstance(engine.find(nodes, RoutedActionControl, key="probe.go"), RoutedActionControl)
        assert isinstance(engine.find(nodes, RoutedChoices, key="probe.pick"), RoutedChoices)
        # A routed form has no in-process submit handler, so it lowers to a plain routed control.
        assert isinstance(engine.find(nodes, RoutedActionControl, key="probe.edit"), RoutedActionControl)

    def test_a_form_trigger_is_content_in_one_shell_and_a_control_in_the_other(self) -> None:
        """Drivers expose forms at different levels of the layout tree.

        ComponentDriver returns a FormTrigger that the frontend can present as a modal.
        RouteDriver returns a button carrying a routing ID, which needs a control group.
        Machines that display forms must place each result in the appropriate container.
        """
        assert isinstance(engine.find(wt.mounted(Probe()).nodes, FormTrigger, key="probe.edit"), FormTrigger)
        assert isinstance(
            engine.find(wt.routed(Probe()).nodes, RoutedActionControl, key="probe.edit"), RoutedActionControl
        )

    def test_both_shells_expose_the_same_control_keys(self) -> None:
        keyed = (ActionControl, Choices, FormTrigger, RoutedActionControl, RoutedChoices)

        def keys(nodes: object) -> set[str]:
            return {node.key for node in engine.walk(nodes) if isinstance(node, keyed)}

        assert keys(wt.mounted(Probe()).nodes) == keys(wt.routed(Probe()).nodes)


class TestRoutePhases:
    def test_a_button_encodes_the_state_its_press_will_produce(self) -> None:
        """The `next` phase computes the result before the button is pressed.

        No user input is needed, so RouteDriver can encode the resulting state in the ID.
        A host can decode that state after a restart and render it directly.
        """
        route = wt.routed(Probe()).route_for("go")

        assert route.phase == "next"
        assert route.state == sp.TabsState("went")

    def test_a_picker_encodes_the_state_its_selection_applies_to(self) -> None:
        """The `input` phase encodes the current state because the choice is not known yet.

        The host applies the transition after the selected values arrive.
        """
        route = wt.routed(Probe()).route_for("pick")

        assert route.phase == "input"
        assert route.state == Probe().initial_state

    def test_a_form_encodes_input_phase_for_the_same_reason(self) -> None:
        assert wt.routed(Probe()).route_for("edit").phase == "input"


class TestEmbeddedContent:
    def test_the_component_shell_embeds_a_component_through_a_keyed_boundary(self) -> None:
        assert "embedded" in wt.mounted(Probe(embed=True)).texts()

    def test_the_routed_shell_refuses_a_component_and_says_what_to_do_instead(self) -> None:
        """A routed render has no session to mount a child into, so this cannot be deferred to
        draw time -- and a silent drop would lose content with no diagnostic."""
        with pytest.raises(TypeError, match="a routed machine cannot embed Echo"):
            wt.routed(Probe(embed=True))


class TestComponentDriverWiring:
    async def test_on_change_sees_the_transition_and_the_action_that_caused_it(self) -> None:
        seen: list[sp.TransitionEvent[sp.TabsState]] = []

        async def changed(event: sp.TransitionEvent[sp.TabsState]) -> None:
            seen.append(event)

        harness = wt.mounted(Probe(), on_change=changed)

        await harness.press("probe.go")

        assert [(event.action, event.previous, event.state) for event in seen] == [
            ("go", sp.TabsState("start"), sp.TabsState("went"))
        ]

    async def test_on_change_runs_before_a_per_action_handler(self) -> None:
        """The general hook observes every transition; the specific one reacts to this action.
        Ordering matters because the specific handler may finish or redirect."""
        order: list[str] = []

        async def changed(_event: sp.TransitionEvent[sp.TabsState]) -> None:
            order.append("on_change")

        async def handled(_event: sp.TransitionEvent[sp.TabsState]) -> None:
            order.append("handler")

        harness = wt.mounted(Probe(), on_change=changed, handlers={"go": handled})

        await harness.press("probe.go")

        assert order == ["on_change", "handler"]

    async def test_a_handler_for_another_action_does_not_run(self) -> None:
        ran: list[str] = []

        async def handled(_event: sp.TransitionEvent[sp.TabsState]) -> None:
            ran.append("pick")

        harness = wt.mounted(Probe(), handlers={"pick": handled})

        await harness.press("probe.go")

        assert ran == []

    async def test_a_finish_action_finishes_and_others_do_not(self) -> None:
        harness = wt.mounted(Probe(), finish_actions=["pick"])

        await harness.press("probe.go")
        assert not harness.finished

        await harness.choose("probe.pick", "two")
        assert harness.finished

    async def test_a_selection_carries_its_values_into_the_transition(self) -> None:
        harness = wt.mounted(Probe())

        await harness.choose("probe.pick", "two")

        assert harness.state == sp.TabsState("two")

    async def test_a_submission_carries_its_values_into_the_transition(self) -> None:
        harness = wt.mounted(Probe())

        await harness.submit("probe.edit", {"value": "typed"})

        assert harness.state == sp.TabsState("typed")

    def test_an_explicit_initial_state_replaces_the_machines_own(self) -> None:
        assert wt.mounted(Probe(), initial=sp.TabsState("elsewhere")).state == sp.TabsState("elsewhere")

    def test_explicit_none_is_a_real_initial_state(self) -> None:
        machine = OptionalProbe()

        assert sp.ComponentDriver(machine).machine_state == "machine default"
        assert sp.ComponentDriver(machine, initial=None).machine_state is None
        assert wt.mounted(machine, initial=None).state is None
        assert "None" in wt.routed(machine, None).texts()


class TestRouteDriverTransition:
    def test_it_applies_input_the_routed_shell_could_not_apply_at_render_time(self) -> None:
        """The host's half of the `input` phase: decode the state from the id, then apply the
        values that arrived with the interaction."""
        driver: sp.RouteDriver[sp.TabsState, sl.ComponentsV2Target] = sp.RouteDriver(lambda request: request.action)
        machine = Probe()

        settled = driver.transition(machine, machine.initial_state, "pick", values=("two",))

        assert settled == sp.TabsState("two")

    def test_it_applies_a_submission_the_same_way(self) -> None:
        driver: sp.RouteDriver[sp.TabsState, sl.ComponentsV2Target] = sp.RouteDriver(lambda request: request.action)
        machine = Probe()

        settled = driver.transition(machine, machine.initial_state, "edit", submitted={"value": "typed"})

        assert settled == sp.TabsState("typed")


class TestFormPresentingMachine:
    def test_a_machine_that_answers_an_action_with_a_form_is_recognised_structurally(self) -> None:
        """`Editor` resolves nested sections through this shape rather than through an optional
        method on every machine, so the check has to be a runtime protocol."""
        editor = sp.Editor("Edit", (sp.EditorSection.from_form("value", "Value", _form()),))

        assert isinstance(editor, FormPresentingMachine)
        assert not isinstance(Probe(), FormPresentingMachine)
