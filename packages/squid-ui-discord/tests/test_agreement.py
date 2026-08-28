"""Where Discord access policy meets an agreement's own membership check.

The widget's behaviour -- who has approved, resolution order, withdrawal, validation -- is
tested without a transport in `squid-ui-widgets`. What is left here is the pair of gates that
only exist once a mount is in front of it, and the difference in how each one refuses.
"""

from typing import Any

import squid_ui_discord
import squid_ui_widgets as sp
from squid_ui_discord.testing import commit_render, fake_interaction


def _agreement(**overrides: Any) -> sp.Agreement:
    return sp.Agreement(
        "Ship this change?",
        (sp.AgreementParticipant("1", "Alice"), sp.AgreementParticipant("2", "Bob")),
        **overrides,
    )


async def test_an_outsider_admitted_by_access_is_still_refused_by_the_participant_list() -> None:
    """`Everyone()` lets the press through the mount, so the widget's own check has to hold.

    It refuses by deferring: the interaction is consumed, and nothing changes.
    """
    agreement = _agreement(require=1)
    message_root = squid_ui_discord.MessageRoot(agreement, access=squid_ui_discord.Everyone(), timeout=None)
    commit_render(message_root)
    interaction = fake_interaction(user_id=99)

    await message_root.dispatch("agreement.approve", interaction)

    assert agreement.approved == ()
    assert not agreement.resolved
    assert interaction.response.defer.await_count == 1


async def test_users_access_refuses_before_the_agreement_is_reached_at_all() -> None:
    """The mount's own gate answers first, and it answers differently -- a message, not a defer."""
    agreement = _agreement(require=1)
    message_root = squid_ui_discord.MessageRoot(agreement, access=squid_ui_discord.Users({1, 2}), timeout=None)
    commit_render(message_root)
    interaction = fake_interaction(user_id=99)

    await message_root.dispatch("agreement.approve", interaction)

    assert agreement.approved == ()
    assert interaction.response.send_message.await_count == 1
    assert interaction.response.defer.await_count == 0
