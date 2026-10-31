"""Branching Wizard behaviour over the component and router shells."""

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.semantic import FormTrigger
from squid_ui_widgets import REVIEW_STEP
from squid_ui_widgets import testing as wt


def _form(title: str, key: str) -> sl.forms.FormSpec:
    return sl.forms.FormSpec(title, (sl.forms.TextField(key=key, label=key.title()),))


def _steps(answers: sp.WizardAnswers):
    yield sp.WizardStep("kind", "Kind", _form("Choose kind", "kind"))
    if answers.get("kind", {}).get("kind") == "advanced":
        yield sp.WizardStep("detail", "Detail", _form("Add detail", "detail"))
    yield sp.WizardStep("review", "Review", sl.paragraph("Review answers"))


def test_branch_flip_retains_orphans_but_finish_collects_only_live_steps() -> None:
    wizard = sp.Wizard("Build", _steps)
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
    harness = wt.driving(wizard.build_component(initial=state))

    assert engine.find(harness.nodes, FormTrigger).spec.prefill == {"detail": "kept"}


async def test_the_last_form_finishes_once_with_the_live_answers() -> None:
    completed: list[sp.WizardAnswers] = []

    async def finish(_event: sp.TransitionEvent[sp.WizardState], answers: sp.WizardAnswers) -> None:
        completed.append(answers)

    harness = wt.driving(
        sp.Wizard("One", (sp.WizardStep("name", "Name", _form("Name", "name")),)).build_component(on_finish=finish)
    )

    await harness.submit("wizard.name", {"name": "Ada"})

    assert harness.state.complete
    assert completed == [{"name": {"name": "Ada"}}]


def test_router_shell_uses_input_phase_for_forms_and_next_state_for_buttons() -> None:
    wizard = sp.Wizard(
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

    form_wizard = sp.Wizard("Form", (sp.WizardStep("name", "Name", _form("Name", "name")),))
    sp.RouteDriver(route).render(form_wizard, form_wizard.initial_state)
    assert next(request for request in routes if request.action == "submit:name") == sp.TransitionRoute(
        "submit:name", sp.WizardState("name"), "input"
    )


def _review_steps(answers: sp.WizardAnswers):
    yield sp.WizardStep("name", "Name", _form("Name", "name"))
    yield sp.WizardStep("kind", "Kind", _form("Kind", "kind"))
    if answers.get("kind", {}).get("kind") == "advanced":
        yield sp.WizardStep("detail", "Detail", _form("Detail", "detail"))


def _answer(wizard: sp.Wizard, state: sp.WizardState, step: str, value: str) -> sp.WizardState:
    return wizard.transition(state, f"submit:{step}", submitted={step: value})


def _labels(rendered) -> list[object]:
    return [
        node.label
        for node in engine.walk(rendered)
        if isinstance(node, sl.semantic.ActionControl | sl.semantic.FormTrigger)
    ]


def _unused(node):
    yield node
    for child in getattr(node, "children", ()) or ():
        yield from engine.walk(child)
    for item in getattr(node, "items", ()) or ():
        yield from engine.walk(item)
    for entry in getattr(node, "fields", ()) or ():
        yield from engine.walk(entry)


def test_a_final_submit_lands_on_review_instead_of_completing() -> None:
    wizard = sp.Wizard("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    assert state.current == "kind"

    state = _answer(wizard, state, "kind", "basic")

    assert state.current == REVIEW_STEP
    assert state.reviewing
    assert not state.complete


def test_a_jumped_edit_returns_to_review_rather_than_marching_on() -> None:
    wizard = sp.Wizard("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")

    state = wizard.transition(state, "goto:name")
    assert state.current == "name"
    assert state.reviewing

    state = _answer(wizard, state, "name", "Grace")
    assert state.current == REVIEW_STEP
    assert wizard.live_answers(state)["name"] == {"name": "Grace"}


def test_back_from_a_jumped_edit_returns_to_review() -> None:
    wizard = sp.Wizard("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")
    state = wizard.transition(state, "goto:name")

    assert wizard.transition(state, "back").current == REVIEW_STEP


def test_a_branch_that_grows_after_an_edit_gates_finish_in_the_state_machine() -> None:
    wizard = sp.Wizard("Build", _review_steps, review=True)
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
    wizard = sp.Wizard("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")
    # Editing the branch answer from review grows a step nobody has answered yet.
    state = _answer(wizard, wizard.transition(state, "goto:kind"), "kind", "advanced")
    assert state.current == REVIEW_STEP

    rendered = wizard.build_component(initial=state).render()
    values = [node.value for node in engine.walk(rendered) if isinstance(node, sl.semantic.Field)]

    assert values == ["Ada", "advanced", sl.chrome.DEFAULT_CHROME.unanswered]
    assert "Finish" in _labels(rendered)


def test_a_summarize_callback_replaces_the_default_rows() -> None:
    review = sp.WizardReview(summarize=lambda answers: sl.paragraph(f"{len(answers)} answers"))
    wizard = sp.Wizard("Build", _review_steps, review=review)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")

    rendered = wizard.build_component(initial=state).render()

    assert not [node for node in engine.walk(rendered) if isinstance(node, sl.semantic.Field)]
    assert any(
        isinstance(node, sl.semantic.Paragraph) and node.content == "2 answers" for node in engine.walk(rendered)
    )


async def test_finishing_from_the_review_screen_happens_once() -> None:
    completed: list[sp.WizardAnswers] = []

    async def finish(_event: sp.TransitionEvent[sp.WizardState], answers: sp.WizardAnswers) -> None:
        completed.append(dict(answers))

    wizard = sp.Wizard("One", (sp.WizardStep("name", "Name", _form("Name", "name")),), review=True)
    harness = wt.driving(wizard.build_component(on_finish=finish))

    await harness.submit("wizard.name", {"name": "Ada"})

    assert harness.state.current == REVIEW_STEP
    assert not harness.state.complete

    await harness.press("wizard.finish")

    assert harness.state.complete
    assert completed == [{"name": {"name": "Ada"}}]


def test_a_review_state_still_routes_through_the_stateless_shell() -> None:
    wizard = sp.Wizard("Routed", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")
    routes: list[sp.TransitionRoute[sp.WizardState]] = []

    sp.RouteDriver(lambda request: (routes.append(request), "route")[1]).render(wizard, state)

    assert any(request.action == "goto:name" and request.state.reviewing for request in routes)
    assert any(request.action == "finish" for request in routes)


def test_the_review_step_key_is_reserved() -> None:
    with pytest.raises(ValueError, match="reserved"):
        sp.Wizard("Build", (sp.WizardStep(REVIEW_STEP, "Nope", "hi"),), review=True)


def test_a_wizard_without_review_is_unchanged() -> None:
    wizard = sp.Wizard("Build", _review_steps)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")

    assert state.complete
    assert state.current == "kind"
    assert wizard.transition(state, "goto:name") == state
