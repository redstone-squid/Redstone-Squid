"""Branching Wizard behavior over component and router shells."""

import discord

import squid_layouts as sl
from squid_layouts.discord import Mount
from squid_layouts.discord.testing import commit_render, fake_interaction
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
    mount = Mount(wizard, timeout=None)
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
    mount = Mount(wizard, timeout=None)
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
    mount = Mount(wizard, timeout=None)
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
