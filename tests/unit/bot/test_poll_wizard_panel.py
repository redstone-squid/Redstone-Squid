"""The mounted poll wizard, and which message its terminal edit lands on."""

from types import SimpleNamespace
from typing import Any, cast

import discord

import squid_ui_discord as sd
from squid.bot.voting.poll_wizard import POLL_SCREEN, PollConfirmationComponent, PollDraft
from squid_ui_discord.testing import delivered_to, fake_interaction, fake_message
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
    scheduler: sd.MountScheduler | None = None,
    message: discord.Message | None = None,
) -> sd.Mount:
    bot = make_layout_bot()
    opened = await POLL_SCREEN.open(
        wizard,
        delivered_to(message or fake_message()),
        sessions=bot.mounts,
        opener=sd.Opener(OWNER_ID, 7),
        scheduler=scheduler,
        expiry=sd.RenewEphemeral() if scheduler is not None else sd.PauseUpdates(),
    )
    assert isinstance(opened, sd.sessions.Opened)
    return opened.session.root


async def test_scheduler_backed_wizard_renews_its_private_session() -> None:
    scheduler = sd.MountScheduler()

    mount = await open_wizard(make_wizard(), scheduler=scheduler)

    assert mount.scheduler is scheduler
    assert isinstance(mount.expiry, sd.RenewEphemeral)


async def test_cancelling_disables_the_wizard_and_leaves_the_notice_alone() -> None:
    """`_cancel` answers with a notice before finishing, which spends the interaction.

    Finishing through it would have replaced the "Poll cancelled." reply with a disabled
    wizard and left the real wizard clickable. The mount falls back to the message instead.
    """
    message = fake_message()
    mount = await open_wizard(make_wizard(), message=message)

    interaction = fake_interaction(user_id=OWNER_ID)
    await mount.dispatch("cancel", interaction)

    interaction.response.send_message.assert_awaited_once()
    interaction.edit_original_response.assert_not_awaited()
    message.edit.assert_awaited_once()
    disabled = message.edit.await_args.kwargs["view"]
    assert all(getattr(item, "disabled", True) for item in disabled.walk_children())


async def test_custom_duration_submission_returns_through_the_mount_funnel() -> None:
    wizard = make_wizard()
    mount = await open_wizard(wizard)
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
