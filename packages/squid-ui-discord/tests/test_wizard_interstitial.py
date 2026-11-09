"""The Continue button between consecutive wizard forms belongs to the Discord frontend.

Discord will not open a modal from a modal submission, so a wizard whose next step is also a
form needs a button press before the frontend can open it. The wizard state already
points to detail; MessageRoot supplies the Continue button for that pending form.
These tests check that presentation step separately from the wizard's transitions.
"""

import discord

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.testing import commit_render, interaction_harness


def _form(title: str, key: str) -> sl.forms.FormSpec:
    return sl.forms.FormSpec(title, (sl.forms.TextField(key=key, label=key.title()),))


def _steps(answers: sp.WizardAnswers):
    yield sp.WizardStep("kind", "Kind", _form("Choose kind", "kind"))
    if answers.get("kind", {}).get("kind") == "advanced":
        yield sp.WizardStep("detail", "Detail", _form("Add detail", "detail"))
    yield sp.WizardStep("review", "Review", sl.paragraph("Review answers"))


def _text_input(modal: discord.ui.Modal) -> discord.ui.TextInput:
    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    assert isinstance(label.component, discord.ui.TextInput)
    return label.component


async def _submit_form(message_root: MessageRoot, key: str, value: str) -> None:
    opened = interaction_harness()
    await message_root.dispatch(key, opened)
    modal = opened.response.send_modal.await_args.args[0]
    assert isinstance(modal, discord.ui.Modal)
    _text_input(modal)._value = value  # pyrefly: ignore[missing-attribute]
    await modal.on_submit(interaction_harness())


async def test_consecutive_forms_use_the_framework_owned_interstitial_hop() -> None:
    wizard = sp.Wizard("Build", _steps)
    message_root = MessageRoot(wizard, access=Everyone(), timeout=None)
    commit_render(message_root)

    await _submit_form(message_root, "wizard.kind", "advanced")

    assert wizard.machine_state.current == "detail"
    view = commit_render(message_root)
    button = next(
        item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.label == "Continue"
    )
    assert button.custom_id is not None and button.custom_id.endswith(":wizard.detail")


async def test_plain_next_opens_the_following_form_without_an_intermediate_render() -> None:
    wizard = sp.Wizard(
        "Profile",
        (
            sp.WizardStep("intro", "Introduction", "Ready"),
            sp.WizardStep("name", "Name", _form("Name", "name")),
            sp.WizardStep("done", "Done", "Review"),
        ),
    )
    message_root = MessageRoot(wizard, access=Everyone(), timeout=None)
    commit_render(message_root)

    opened = interaction_harness()
    await message_root.dispatch("wizard.name", opened)

    assert opened.response.send_modal.await_count == 1
    assert wizard.machine_state.current == "intro"
