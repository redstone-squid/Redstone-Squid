"""One-way decisions over the component and router shells."""

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.semantic import ActionControl, Status
from squid_ui_widgets import testing as wt


def _decision() -> sp.Decision:
    return sp.Decision(
        sl.paragraph("Delete this build?"),
        (
            sp.DecisionOption("delete", "Delete", sl.Tone.DANGER, sl.semantic.Emphasis.STRONG),
            sp.DecisionOption("keep", "Keep"),
        ),
        key="delete-build",
    )


def test_choosing_settles_the_decision_and_later_transitions_are_noops() -> None:
    machine = _decision()

    decided = machine.transition(machine.initial_state, "choose:delete")
    repeated = machine.transition(decided, "choose:keep")

    assert decided == sp.DecisionState("delete")
    assert repeated is decided, "a settled decision returns the same state, not an equal one"


def test_deciding_disables_every_option_and_shows_a_status() -> None:
    harness = wt.mounted(_decision(), initial=sp.DecisionState("delete"))

    controls = engine.find_all(harness.nodes, ActionControl)

    assert controls, "the options are still drawn after deciding"
    assert all(not control.available for control in controls)
    assert engine.find_all(harness.nodes, Status)


async def test_the_handler_receives_the_chosen_option_and_the_finish_action_ends_the_shell() -> None:
    seen: list[tuple[str, sp.DecisionState]] = []

    async def decided(event: sp.TransitionEvent[sp.DecisionState], key: str) -> None:
        seen.append((key, event.state))

    harness = wt.driving(_decision().build_component(on_decide=decided, finish_on={"delete"}))

    await harness.press("delete-build.delete")

    assert harness.state == sp.DecisionState("delete")
    assert seen == [("delete", sp.DecisionState("delete"))]
    assert harness.finished


async def test_confirm_wires_two_options_and_carries_the_tone_it_was_given() -> None:
    """The Discord button *style* this tone becomes is asserted in the adapter's suite; what
    the machine owns is which controls exist, their order, and the tone it hands down."""
    seen: list[str] = []

    async def record(event: sp.TransitionEvent[sp.DecisionState]) -> None:
        seen.append(event.action)

    harness = wt.driving(sp.confirm("Proceed?", on_confirm=record, on_cancel=record, tone=sl.Tone.DANGER))

    assert harness.labels() == ["Confirm", "Cancel"]
    assert harness.control("confirm.confirm").tone is sl.Tone.DANGER

    await harness.press("confirm.confirm")

    assert seen == ["choose:confirm"]
    assert all(not control.available for control in engine.find_all(harness.nodes, ActionControl))
    assert engine.find_all(harness.nodes, Status)


def test_the_router_shell_encodes_a_serializable_decision_state() -> None:
    render = wt.routed(_decision())

    assert render.route_for("choose:delete").state == sp.DecisionState("delete")
    assert render.route_for("choose:delete").phase == "next"


def test_the_component_shell_renders_the_prompt_and_both_options_available() -> None:
    harness = wt.mounted(_decision())

    assert harness.texts() == ["Delete this build?"]
    assert harness.labels() == ["Delete", "Keep"]
    assert all(control.available for control in engine.find_all(harness.nodes, ActionControl))
    assert engine.find_all(harness.nodes, Status) == (), "no verdict before one is chosen"
