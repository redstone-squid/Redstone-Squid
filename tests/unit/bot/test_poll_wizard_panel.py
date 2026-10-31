"""The mounted poll wizard, and which message its terminal edit lands on."""

from types import SimpleNamespace
from typing import Any, cast

import discord

import squid_layouts as sl
from squid.bot.voting.poll_wizard import PollConfirmationComponent, PollDraft
from squid_layouts.discord.testing import commit_render, delivered_to, fake_interaction, fake_message
from tests.helpers.discord import make_layout_bot
from tests.helpers.voting import GENERIC_OPTIONS

OWNER_ID = 11


def make_wizard() -> PollConfirmationComponent:
    publisher = SimpleNamespace(create_and_publish=None, may_create_network=None)
    draft = PollDraft(question="Best door?", options_text="One\nTwo")
    return PollConfirmationComponent(cast(Any, publisher), OWNER_ID, 42, draft, GENERIC_OPTIONS)


def test_scheduler_backed_wizard_renews_its_private_session() -> None:
    reactor = sl.discord.Reactor()

    mount = make_wizard().mount(source=make_layout_bot(), reactor=reactor)

    assert mount.scheduler is reactor
    assert isinstance(mount.expiry, sl.discord.RenewEphemeral)


async def test_cancelling_disables_the_wizard_and_leaves_the_notice_alone() -> None:
    """`_cancel` answers with a notice before finishing, which spends the interaction.

    Finishing through it would have replaced the "Poll cancelled." reply with a disabled
    wizard and left the real wizard clickable. The mount falls back to the message instead.
    """
    mount = make_wizard().mount(source=make_layout_bot())
    message = fake_message()
    await mount.send(delivered_to(message))

    interaction = fake_interaction(user_id=OWNER_ID)
    await mount.dispatch("cancel", interaction)

    interaction.response.send_message.assert_awaited_once()
    interaction.edit_original_response.assert_not_awaited()
    message.edit.assert_awaited_once()
    disabled = message.edit.await_args.kwargs["view"]
    assert all(getattr(item, "disabled", True) for item in disabled.walk_children())


async def test_custom_duration_submission_returns_through_the_mount_funnel() -> None:
    wizard = make_wizard()
    mount = wizard.mount(source=make_layout_bot())
    commit_render(mount)
    opening = fake_interaction(user_id=OWNER_ID)

    await mount.dispatch("duration", opening, ["custom"])

    modal = opening.response.send_modal.await_args.args[0]
    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    duration = label.component
    assert isinstance(duration, discord.ui.TextInput)
    duration._value = "12h"  # pyrefly: ignore[missing-attribute]

    await modal.on_submit(fake_interaction(user_id=OWNER_ID))

    assert wizard.draft.duration_seconds == 12 * 3600
    assert mount.generation == 2
