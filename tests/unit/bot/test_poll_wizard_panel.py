"""The mounted poll wizard, and which message its terminal edit lands on."""

from types import SimpleNamespace
from typing import Any, cast

from squid.bot.voting.poll_wizard import PollConfirmationComponent, PollDraft
from squid_layouts.discord.testing import fake_interaction, fake_message
from tests.helpers.voting import GENERIC_OPTIONS

OWNER_ID = 11


def make_wizard() -> PollConfirmationComponent:
    publisher = SimpleNamespace(create_and_publish=None, may_create_network=None)
    draft = PollDraft(question="Best door?", options_text="One\nTwo")
    return PollConfirmationComponent(cast(Any, publisher), OWNER_ID, 42, draft, GENERIC_OPTIONS)


async def test_cancelling_disables_the_wizard_and_leaves_the_notice_alone() -> None:
    """`_cancel` answers with a notice before finishing, which spends the interaction.

    Finishing through it would have replaced the "Poll cancelled." reply with a disabled
    wizard and left the real wizard clickable. The mount falls back to the message instead.
    """
    mount = make_wizard().mount()
    message = fake_message()
    mount.bind(message, mount.build_view())

    interaction = fake_interaction(user_id=OWNER_ID)
    await mount.dispatch("cancel", interaction)

    interaction.response.send_message.assert_awaited_once()
    interaction.edit_original_response.assert_not_awaited()
    message.edit.assert_awaited_once()
    disabled = message.edit.await_args.kwargs["view"]
    assert all(getattr(item, "disabled", True) for item in disabled.walk_children())
