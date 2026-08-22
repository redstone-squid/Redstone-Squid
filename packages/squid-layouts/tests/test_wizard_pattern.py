"""Branching Wizard behavior over component and router shells."""

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import Everyone, Mount
from squid_layouts.discord.testing import commit_render, fake_interaction
from squid_layouts.patterns import REVIEW_STEP
from squid_layouts.semantic import FormTrigger, Stack


def _form(title: str, key: str) -> sl.FormSpec:
    return sl.FormSpec(title, (sl.TextField(key=key, label=key.title()),))


def _steps(answers: sl.WizardAnswers):
    yield sl.WizardStep("kind", "Kind", _form("Choose kind", "kind"))
    if answers.get("kind", {}).get("kind") == "advanced":
        yield sl.WizardStep("detail", "Detail", _form("Add detail", "detail"))
    yield sl.WizardStep("review", "Review", sl.paragraph("Review answers"))


def _text_input(modal: discord.ui.Modal) -> discord.ui.TextInput:
    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    assert isinstance(label.component, discord.ui.TextInput)
    return label.component


async def _submit_form(mount: Mount, key: str, value: str) -> None:
    opened = fake_interaction()
    await mount.dispatch(key, opened)
    modal = opened.response.send_modal.await_args.args[0]
    assert isinstance(modal, discord.ui.Modal)
    _text_input(modal)._value = value  # pyrefly: ignore[missing-attribute]
    await modal.on_submit(fake_interaction())


def test_branch_flip_retains_orphans_but_finish_collects_only_live_steps() -> None:
    wizard = sl.Wizard("Build", _steps)
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
    component = wizard.component(initial=state)
    rendered = component.render()
    assert isinstance(rendered, Stack)
    trigger = next(child for child in rendered.children if isinstance(child, FormTrigger))
    assert trigger.spec.prefill == {"detail": "kept"}


async def test_consecutive_forms_use_the_framework_owned_interstitial_hop() -> None:
    wizard = sl.Wizard("Build", _steps).component()
    mount = Mount(wizard, access=Everyone(), timeout=None)
    commit_render(mount)

    await _submit_form(mount, "wizard.kind", "advanced")

    assert wizard.pattern_state.current == "detail"
    view = commit_render(mount)
    button = next(
        item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.label == "Continue"
    )
    assert button.custom_id is not None and button.custom_id.endswith(":wizard.detail")


async def test_plain_next_opens_the_following_form_without_an_intermediate_render() -> None:
    wizard = sl.Wizard(
        "Profile",
        (
            sl.WizardStep("intro", "Introduction", "Ready"),
            sl.WizardStep("name", "Name", _form("Name", "name")),
            sl.WizardStep("done", "Done", "Review"),
        ),
    ).component()
    mount = Mount(wizard, access=Everyone(), timeout=None)
    commit_render(mount)

    opened = fake_interaction()
    await mount.dispatch("wizard.name", opened)

    assert opened.response.send_modal.await_count == 1
    assert wizard.pattern_state.current == "intro"


async def test_last_form_dispatches_finish_once_with_live_answers() -> None:
    completed: list[sl.WizardAnswers] = []

    async def finish(_event: sl.PatternEvent[sl.WizardState], answers: sl.WizardAnswers) -> None:
        completed.append(answers)

    wizard = sl.Wizard("One", (sl.WizardStep("name", "Name", _form("Name", "name")),)).component(on_finish=finish)
    mount = Mount(wizard, access=Everyone(), timeout=None)
    commit_render(mount)

    await _submit_form(mount, "wizard.name", "Ada")

    assert wizard.pattern_state.complete
    assert completed == [{"name": {"name": "Ada"}}]


def test_router_shell_uses_input_phase_for_forms_and_next_state_for_buttons() -> None:
    wizard = sl.Wizard(
        "Routed",
        (sl.WizardStep("intro", "Intro", "Hello"), sl.WizardStep("done", "Done", "Bye")),
    )
    routes: list[sl.PatternRoute[sl.WizardState]] = []

    def route(request: sl.PatternRoute[sl.WizardState]) -> str:
        routes.append(request)
        return f"wizard:{request.state.current}:{int(request.state.complete)}"

    sl.RouterShell(route).render(wizard, wizard.initial_state)
    assert next(request for request in routes if request.action == "next") == sl.PatternRoute(
        "next", sl.WizardState("done"), "next"
    )

    form_wizard = sl.Wizard("Form", (sl.WizardStep("name", "Name", _form("Name", "name")),))
    sl.RouterShell(route).render(form_wizard, form_wizard.initial_state)
    assert next(request for request in routes if request.action == "submit:name") == sl.PatternRoute(
        "submit:name", sl.WizardState("name"), "input"
    )


def _review_steps(answers: sl.WizardAnswers):
    yield sl.WizardStep("name", "Name", _form("Name", "name"))
    yield sl.WizardStep("kind", "Kind", _form("Kind", "kind"))
    if answers.get("kind", {}).get("kind") == "advanced":
        yield sl.WizardStep("detail", "Detail", _form("Detail", "detail"))


def _answer(wizard: sl.Wizard, state: sl.WizardState, step: str, value: str) -> sl.WizardState:
    return wizard.transition(state, f"submit:{step}", submitted={step: value})


def _labels(rendered) -> list[object]:
    return [node.label for node in _walk(rendered) if isinstance(node, sl.Action | sl.FormTrigger)]


def _walk(node):
    yield node
    for child in getattr(node, "children", ()) or ():
        yield from _walk(child)
    for item in getattr(node, "items", ()) or ():
        yield from _walk(item)
    for entry in getattr(node, "fields", ()) or ():
        yield from _walk(entry)


def test_a_final_submit_lands_on_review_instead_of_completing() -> None:
    wizard = sl.Wizard("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    assert state.current == "kind"

    state = _answer(wizard, state, "kind", "basic")

    assert state.current == REVIEW_STEP
    assert state.reviewing
    assert not state.complete


def test_a_jumped_edit_returns_to_review_rather_than_marching_on() -> None:
    wizard = sl.Wizard("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")

    state = wizard.transition(state, "goto:name")
    assert state.current == "name"
    assert state.reviewing

    state = _answer(wizard, state, "name", "Grace")
    assert state.current == REVIEW_STEP
    assert wizard.live_answers(state)["name"] == {"name": "Grace"}


def test_back_from_a_jumped_edit_returns_to_review() -> None:
    wizard = sl.Wizard("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")
    state = wizard.transition(state, "goto:name")

    assert wizard.transition(state, "back").current == REVIEW_STEP


def test_a_branch_that_grows_after_an_edit_gates_finish_in_the_state_machine() -> None:
    wizard = sl.Wizard("Build", _review_steps, review=True)
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
    wizard = sl.Wizard("Build", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "advanced")

    rendered = wizard.component(initial=state).render()
    values = [node.value for node in _walk(rendered) if isinstance(node, sl.Field)]

    assert values == ["Ada", "advanced", sl.DEFAULT_CHROME.unanswered]
    assert "Finish" in _labels(rendered)


def test_a_summarize_callback_replaces_the_default_rows() -> None:
    review = sl.WizardReview(summarize=lambda answers: sl.paragraph(f"{len(answers)} answers"))
    wizard = sl.Wizard("Build", _review_steps, review=review)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")

    rendered = wizard.component(initial=state).render()

    assert not [node for node in _walk(rendered) if isinstance(node, sl.Field)]
    assert any(isinstance(node, sl.Paragraph) and node.content == "2 answers" for node in _walk(rendered))


async def test_finish_dispatches_once_from_the_review_screen() -> None:
    completed: list[sl.WizardAnswers] = []

    async def finish(_event: sl.PatternEvent[sl.WizardState], answers: sl.WizardAnswers) -> None:
        completed.append(dict(answers))

    wizard = sl.Wizard("One", (sl.WizardStep("name", "Name", _form("Name", "name")),), review=True)
    shell = wizard.component(on_finish=finish)
    mount = Mount(shell, access=Everyone(), timeout=None)
    commit_render(mount)

    await _submit_form(mount, "wizard.name", "Ada")
    assert shell.pattern_state.current == REVIEW_STEP
    assert not shell.pattern_state.complete

    commit_render(mount)
    await mount.dispatch("wizard.finish", fake_interaction())

    assert shell.pattern_state.complete
    assert completed == [{"name": {"name": "Ada"}}]


def test_a_review_state_still_routes_through_the_stateless_shell() -> None:
    wizard = sl.Wizard("Routed", _review_steps, review=True)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")
    routes: list[sl.PatternRoute[sl.WizardState]] = []

    sl.RouterShell(lambda request: (routes.append(request), "route")[1]).render(wizard, state)

    assert any(request.action == "goto:name" and request.state.reviewing for request in routes)
    assert any(request.action == "finish" for request in routes)


def test_the_review_step_key_is_reserved() -> None:
    with pytest.raises(ValueError, match="reserved"):
        sl.Wizard("Build", (sl.WizardStep(REVIEW_STEP, "Nope", "hi"),), review=True)


def test_a_wizard_without_review_is_unchanged() -> None:
    wizard = sl.Wizard("Build", _review_steps)
    state = _answer(wizard, wizard.initial_state, "name", "Ada")
    state = _answer(wizard, state, "kind", "basic")

    assert state.complete
    assert state.current == "kind"
    assert wizard.transition(state, "goto:name") == state
