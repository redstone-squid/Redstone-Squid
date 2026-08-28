"""The mounted poll wizard, and which message its terminal edit lands on."""

from types import SimpleNamespace
from typing import Any, cast

import discord

import squid_ui_discord as sd
from squid.bot.voting.poll_wizard import PollConfirmationComponent, PollDraft
from squid_ui_discord.testing import fake_interaction, fake_message
from tests.helpers.discord import make_layout_bot
from tests.helpers.voting import GENERIC_OPTIONS

OWNER_ID = 11


def make_wizard() -> PollConfirmationComponent:
    publisher = SimpleNamespace(create_and_publish=None, may_create_network=None)
    draft = PollDraft(question="Best door?", options_text="One\nTwo")
    return PollConfirmationComponent(cast(Any, publisher), 42, draft, GENERIC_OPTIONS)


async def open_wizard(
    wizard: PollConfirmationComponent,
    *,
    message: discord.Message | None = None,
) -> tuple[PollConfirmationComponent, sd.MessageRoot, Any]:
    bot = make_layout_bot()
    interaction = fake_interaction(user_id=OWNER_ID)
    interaction.client = bot
    interaction.guild = SimpleNamespace(id=7)
    interaction.guild_locale = None
    interaction.locale = "en-US"
    if message is not None:
        interaction.original_response.return_value = message
    shown = await PollConfirmationComponent(
        wizard.publisher,
        wizard.author_account_id,
        wizard.draft,
        wizard.vote_options,
        allow_network=wizard.allow_network,
    ).show(interaction, wait=True)
    assert shown is not None
    sessions = bot.sessions.get(sd.SessionKey.user_guild("poll-wizard", OWNER_ID, 7))
    assert len(sessions) == 1
    return shown, sessions[0].root, interaction


async def test_scheduler_backed_wizard_renews_its_private_session() -> None:
    _, message_root, _ = await open_wizard(make_wizard())

    assert message_root.scheduler is not None
    assert isinstance(message_root.expiry, sd.RenewEphemeral)


async def test_cancelling_disables_the_wizard_and_leaves_the_notice_alone() -> None:
    """`_cancel` answers with a notice before finishing, which spends the interaction.

    Finishing through it would have replaced the "Poll cancelled." reply with a disabled
    wizard and left the real wizard clickable. The mount falls back to the message instead.
    """
    message = fake_message()
    _, message_root, opening = await open_wizard(make_wizard(), message=message)

    interaction = fake_interaction(user_id=OWNER_ID)
    await message_root.dispatch("cancel", interaction)

    interaction.response.send_message.assert_awaited_once()
    interaction.edit_original_response.assert_not_awaited()
    message.edit.assert_not_awaited()
    opening.edit_original_response.assert_awaited_once()
    disabled = opening.edit_original_response.await_args.kwargs["view"]
    assert all(getattr(item, "disabled", True) for item in disabled.walk_children())


async def test_custom_duration_submission_returns_through_the_message_root_funnel() -> None:
    wizard, message_root, _ = await open_wizard(make_wizard())
    opening = fake_interaction(user_id=OWNER_ID)

    await message_root.dispatch("duration", opening, ["custom"])

    modal = opening.response.send_modal.await_args.args[0]
    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    duration = label.component
    assert isinstance(duration, discord.ui.TextInput)
    duration._value = "12h"  # pyrefly: ignore[missing-attribute]

    await modal.on_submit(fake_interaction(user_id=OWNER_ID))

    assert wizard.draft.duration_seconds == 12 * 3600
    assert message_root.generation == 2
