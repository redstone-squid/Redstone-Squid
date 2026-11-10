"""Branching Wizard behaviour through ComponentDriver and RouteDriver."""

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.runtime import transaction
from squid_ui.semantic import FormTrigger
from squid_ui_widgets import REVIEW_STEP
from squid_ui_widgets import testing as wt
from squid_ui_widgets.machines.wizard import Wizard as WizardMachine


def _form(title: str, key: str) -> sl.forms.FormSpec:
    return sl.forms.FormSpec(title, (sl.forms.TextField(key=key, label=key.title()),))


def _steps(answers: sp.WizardAnswers):
    yield sp.WizardStep("kind", "Kind", _form("Choose kind", "kind"))
    if answers.get("kind", {}).get("kind") == "advanced":
        yield sp.WizardStep("detail", "Detail", _form("Add detail", "detail"))
    yield sp.WizardStep("review", "Review", sl.paragraph("Review answers"))


def test_branch_flip_retains_orphans_but_finish_collects_only_live_steps() -> None:
    wizard = WizardMachine("Build", _steps)
    state = wizard.transition(wizard.initial_state, "submit:kind", submitted={"kind": "advanced"})
    state = wizard.transition(state, "submit:detail", submitted={"detail": "kept"})
    assert state.current == "review"

    state = wizard.transition(state, "back")
    state = wizard.transition(state, "back")
    state = wizard.transition(state, "submit:kind", submitted={"kind": "basic"})

    assert state.current == "review"
    assert {answer.step for answer in state.answers} == {"kind", "detail"}
    assert wizard.live_answers(state) == {"kind": {"kind": "basic"}}

    state = wizard.transition(state, "back")
    state = wizard.transition(state, "submit:kind", submitted={"kind": "advanced"})
    assert state.current == "detail"
    harness = wt.driving(sp.Wizard("Build", _steps, initial_state=state))

    assert engine.find(harness.nodes, FormTrigger).spec.prefill == {"detail": "kept"}


async def test_the_last_form_finishes_once_with_the_live_answers() -> None:
    completed: list[sp.WizardAnswers] = []

    async def finish(_event: sp.TransitionEvent[sp.WizardState], answers: sp.WizardAnswers) -> None:
        completed.append(answers)

    harness = wt.driving(sp.Wizard("One", (sp.WizardStep("name", "Name", _form("Name", "name")),), on_finish=finish))

    await harness.submit("wizard.name", {"name": "Ada"})

    assert harness.state.complete
    assert completed == [{"name": {"name": "Ada"}}]


def test_router_shell_uses_input_phase_for_forms_and_next_state_for_buttons() -> None:
    wizard = WizardMachine(
        "Routed",
        (sp.WizardStep("intro", "Intro", "Hello"), sp.WizardStep("done", "Done", "Bye")),
    )
    routes: list[sp.TransitionRoute[sp.WizardState]] = []

    def route(request: sp.TransitionRoute[sp.WizardState]) -> str:
        routes.append(request)
        return f"wizard:{request.state.current}:{int(request.state.complete)}"

    sp.RouteDriver(route).render(wizard, wizard.initial_state)
    assert next(request for request in routes if request.action == "next") == sp.TransitionRoute(
        "next", sp.WizardState("done"), "next"
    )

    form_wizard = WizardMachine("Form", (sp.WizardStep("name", "Name", _form("Name", "name")),))
    sp.RouteDriver(route).render(form_wizard, form_wizard.initial_state)
    assert next(request for request in routes if request.action == "submit:name") == sp.TransitionRoute(
        "submit:name", sp.WizardState("name"), "input"
    )


def _review_steps(answers: sp.WizardAnswers):
    yield sp.WizardStep("name", "Name", _form("Name", "name"))
    yield sp.WizardStep("kind", "Kind", _form("Kind", "kind"))
    if answers.get("kind", {}).get("kind") == "advanced":
        yield sp.WizardStep("detail", "Detail", _form("Detail", "detail"))


def _answer(wizard: WizardMachine, state: sp.WizardState, step: str, value: str) -> sp.WizardState:
    return wizard.transition(state, f"submit:{step}", submitted={step: value})


def _labels(rendered) -> list[object]:
    return [
        sl.text.source_text(node.label)
        for node in engine.walk(rendered)
        if isinstance(node, sl.semantic.ActionControl | sl.semantic.FormTrigger)
    ]


def test_a_final_submit_lands_on_review_instead_of_completing() -> None:
    wizard = WizardMachine("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    assert state.current == "kind"

    state = _answer(wizard, state, "kind", "basic")

    assert state.current == REVIEW_STEP
    assert state.reviewing
    assert not state.complete


def test_a_jumped_edit_returns_to_review_rather_than_marching_on() -> None:
    wizard = WizardMachine("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")

    state = wizard.transition(state, "goto:name")
    assert state.current == "name"
    assert state.reviewing

    state = _answer(wizard, state, "name", "Grace")
    assert state.current == REVIEW_STEP
    assert wizard.live_answers(state)["name"] == {"name": "Grace"}


def test_back_from_a_jumped_edit_returns_to_review() -> None:
    wizard = WizardMachine("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")
    state = wizard.transition(state, "goto:name")

    assert wizard.transition(state, "back").current == REVIEW_STEP


def test_a_branch_that_grows_after_an_edit_gates_finish_in_the_state_machine() -> None:
    wizard = WizardMachine("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")
    assert wizard.answered(state)

    state = wizard.transition(state, "goto:kind")
    state = _answer(wizard, state, "kind", "advanced")

    assert state.current == REVIEW_STEP
    assert not wizard.answered(state)
    assert not wizard.transition(state, "finish").complete

    state = wizard.transition(state, "goto:detail")
    state = _answer(wizard, state, "detail", "kept")
    assert wizard.answered(state)
    assert wizard.transition(state, "finish").complete


def test_review_rows_summarize_answers_and_mark_the_unanswered_ones() -> None:
    wizard = WizardMachine("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")
    # Editing the branch answer from review grows a step nobody has answered yet.
    state = _answer(wizard, wizard.transition(state, "goto:kind"), "kind", "advanced")
    assert state.current == REVIEW_STEP

    rendered = sp.Wizard("Build", _review_steps, review=True, initial_state=state).render()
    values = [node.value for node in engine.walk(rendered) if isinstance(node, sl.semantic.Field)]

    assert values == ["Ada", "advanced", sl.chrome.DEFAULT_CHROME.unanswered]
    assert "Finish" in _labels(rendered)


def test_a_summarize_callback_replaces_the_default_rows() -> None:
    review = sp.WizardReview(summarize=lambda answers: sl.paragraph(f"{len(answers)} answers"))
    wizard = WizardMachine("Build", _review_steps, review=review)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")

    rendered = sp.Wizard("Build", _review_steps, review=review, initial_state=state).render()

    assert not [node for node in engine.walk(rendered) if isinstance(node, sl.semantic.Field)]
    assert any(
        isinstance(node, sl.semantic.Paragraph) and node.content == "2 answers" for node in engine.walk(rendered)
    )


async def test_finishing_from_the_review_screen_happens_once() -> None:
    completed: list[sp.WizardAnswers] = []

    async def finish(_event: sp.TransitionEvent[sp.WizardState], answers: sp.WizardAnswers) -> None:
        completed.append(dict(answers))

    wizard = sp.Wizard("One", (sp.WizardStep("name", "Name", _form("Name", "name")),), review=True, on_finish=finish)
    harness = wt.driving(wizard)

    await harness.submit("wizard.name", {"name": "Ada"})

    assert harness.state.current == REVIEW_STEP
    assert not harness.state.complete

    await harness.press("wizard.finish")

    assert harness.state.complete
    assert completed == [{"name": {"name": "Ada"}}]


def test_a_review_state_still_routes_through_the_stateless_shell() -> None:
    wizard = WizardMachine("Routed", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")
    routes: list[sp.TransitionRoute[sp.WizardState]] = []

    sp.RouteDriver[sp.WizardState](lambda request: (routes.append(request), "route")[1]).render(wizard, state)

    assert any(request.action == "goto:name" and request.state.reviewing for request in routes)
    assert any(request.action == "finish" for request in routes)


def test_the_review_step_key_is_reserved() -> None:
    with pytest.raises(ValueError, match="reserved"):
        WizardMachine("Build", (sp.WizardStep(REVIEW_STEP, "Nope", "hi"),), review=True)


def test_a_wizard_without_review_is_unchanged() -> None:
    wizard = WizardMachine("Build", _review_steps)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")

    assert state.complete
    assert state.current == "kind"
    assert wizard.transition(state, "goto:name") == state


async def test_components_keep_independent_state_and_expose_their_machine() -> None:
    steps = (sp.WizardStep("name", "Name", _form("Name", "name")),)
    first = sp.Wizard("First", steps)
    second = sp.Wizard("Second", steps)
    harness = wt.driving(first)

    await harness.submit("wizard.name", {"name": "Ada"})

    assert isinstance(first.machine, WizardMachine)
    assert first.machine_state.complete
    assert second.machine_state == second.machine.initial_state
    assert second.machine_state.answers == ()


async def test_finish_failure_rolls_back_answers_and_allows_retry() -> None:
    fail = True
    observed: list[sp.WizardState] = []

    async def finish(event: sp.TransitionEvent[sp.WizardState], answers: sp.WizardAnswers) -> None:
        assert answers == {"name": {"name": "Ada"}}
        assert wizard.machine_state == event.state
        observed.append(event.state)
        if fail:
            raise RuntimeError("save failed")

    wizard = sp.Wizard("One", (sp.WizardStep("name", "Name", _form("Name", "name")),), on_finish=finish)
    harness = wt.driving(wizard)
    initial = wizard.machine_state

    with pytest.raises(RuntimeError, match="save failed"), transaction():
        await harness.submit("wizard.name", {"name": "Ada"})

    assert wizard.machine_state == initial
    assert not harness.finished
    fail = False
    with transaction():
        await harness.submit("wizard.name", {"name": "Ada"})

    assert wizard.machine_state.complete
    assert len(observed) == 2


async def test_completion_callback_runs_again_only_after_reopening() -> None:
    completed: list[sp.WizardAnswers] = []

    async def finish(_event: sp.TransitionEvent[sp.WizardState], answers: sp.WizardAnswers) -> None:
        completed.append(answers)

    wizard = sp.Wizard("One", (sp.WizardStep("name", "Name", _form("Name", "name")),), review=True, on_finish=finish)
    harness = wt.driving(wizard)
    await harness.submit("wizard.name", {"name": "Ada"})
    await harness.press("wizard.finish")
    await harness.press("wizard.finish")
    assert completed == [{"name": {"name": "Ada"}}]

    await harness.press("wizard.goto.name")
    await harness.submit("wizard.name", {"name": "Grace"})
    await harness.press("wizard.finish")

    assert completed == [{"name": {"name": "Ada"}}, {"name": {"name": "Grace"}}]
    assert not harness.finished
