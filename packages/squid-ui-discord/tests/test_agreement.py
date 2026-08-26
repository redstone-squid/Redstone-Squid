"""Actor-keyed mounted agreement behavior."""

from typing import Any

import discord
import pytest

import squid_ui as sl
import squid_ui_discord
import squid_ui_widgets as sp
from squid_ui_discord.testing import commit_render, fake_interaction


def _agreement(**overrides: Any) -> sp.Agreement:
    return sp.Agreement(
        "Ship this change?",
        (
            sp.AgreementParticipant("1", "Alice"),
            sp.AgreementParticipant("2", "Bob"),
        ),
        **overrides,
    )


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button[Any]]:
    return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]


def test_agreement_renders_display_names_chrome_and_only_ephemeral_state() -> None:
    agreement = _agreement()
    message_root = squid_ui_discord.MessageRoot(agreement, access=squid_ui_discord.Users({1, 2}), timeout=None)
    view = commit_render(message_root)

    assert "Alice" in _text(view) and "Bob" in _text(view)
    assert "Approved: 0/2" in _text(view)
    assert {button.label for button in _buttons(view)} == {"Approve", "Withdraw"}
    assert set(sl.runtime.inspect_cells(agreement)) == {"approved", "resolved"}


async def test_agreement_resolves_once_in_participant_order() -> None:
    resolved: list[tuple[str, ...]] = []

    async def on_resolve(_event: sl.PressEvent, approved: tuple[str, ...]) -> None:
        resolved.append(approved)

    agreement = _agreement(on_resolve=on_resolve)
    message_root = squid_ui_discord.MessageRoot(agreement, access=squid_ui_discord.Users({1, 2}), timeout=None)
    commit_render(message_root)

    await message_root.dispatch("agreement.approve", fake_interaction(user_id=2))
    await message_root.dispatch("agreement.approve", fake_interaction(user_id=1))
    await message_root.dispatch("agreement.approve", fake_interaction(user_id=1))

    assert agreement.approved == ("1", "2")
    assert agreement.resolved
    assert resolved == [("1", "2")]
    assert all(button.disabled for button in _buttons(commit_render(message_root)))


async def test_withdrawal_removes_only_the_pressing_participant() -> None:
    agreement = _agreement()
    message_root = squid_ui_discord.MessageRoot(agreement, access=squid_ui_discord.Users({1, 2}), timeout=None)
    commit_render(message_root)

    await message_root.dispatch("agreement.approve", fake_interaction(user_id=1))
    await message_root.dispatch("agreement.withdraw", fake_interaction(user_id=1))

    assert agreement.approved == ()
    assert not agreement.resolved


async def test_frontend_neutral_membership_check_rejects_an_outsider() -> None:
    agreement = _agreement(require=1)
    message_root = squid_ui_discord.MessageRoot(agreement, access=squid_ui_discord.Everyone(), timeout=None)
    commit_render(message_root)
    interaction = fake_interaction(user_id=99)

    await message_root.dispatch("agreement.approve", interaction)

    assert agreement.approved == ()
    assert not agreement.resolved
    assert interaction.response.defer.await_count == 1


async def test_users_access_denies_before_agreement_dispatch() -> None:
    agreement = _agreement(require=1)
    message_root = squid_ui_discord.MessageRoot(agreement, access=squid_ui_discord.Users({1, 2}), timeout=None)
    commit_render(message_root)
    interaction = fake_interaction(user_id=99)

    await message_root.dispatch("agreement.approve", interaction)

    assert agreement.approved == ()
    assert interaction.response.send_message.await_count == 1


def test_agreement_validates_identity_threshold_and_controls() -> None:
    participant = sp.AgreementParticipant("1", "Alice")
    with pytest.raises(ValueError, match="at least one participant"):
        sp.Agreement("Prompt", ())
    with pytest.raises(ValueError, match="unique"):
        sp.Agreement("Prompt", (participant, participant))
    with pytest.raises(ValueError, match="reachable positive threshold"):
        sp.Agreement("Prompt", (participant,), require=2)

    agreement = sp.Agreement("Prompt", (participant,), allow_withdraw=False)
    message_root = squid_ui_discord.MessageRoot(agreement, access=squid_ui_discord.Users({1}), timeout=None)
    assert [button.label for button in _buttons(commit_render(message_root))] == ["Approve"]
