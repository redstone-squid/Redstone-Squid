"""Actor-keyed agreement: who has approved, and when that resolves."""

from typing import Any

import pytest

import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import testing as engine
from squid_ui.semantic import ActionControl


def _agreement(**overrides: Any) -> sp.Agreement:
    return sp.Agreement(
        "Ship this change?",
        (sp.AgreementParticipant("1", "Alice"), sp.AgreementParticipant("2", "Bob")),
        **overrides,
    )


def test_it_renders_display_names_a_tally_and_keeps_only_ephemeral_state() -> None:
    agreement = _agreement()

    nodes = engine.render_tree(agreement)
    body = "\n".join(engine.texts(nodes))

    assert "Alice" in body and "Bob" in body
    assert "Approved: 0/2" in body
    assert set(engine.labels(nodes)) == {"Approve", "Withdraw"}
    assert set(sl.runtime.inspect_cells(agreement)) == {"approved", "resolved"}


async def test_it_resolves_once_and_reports_participants_in_declared_order() -> None:
    """Bob presses first, but the resolution reports declaration order, not press order."""
    resolved: list[tuple[str, ...]] = []

    async def on_resolve(_event: sl.PressEvent, approved: tuple[str, ...]) -> None:
        resolved.append(approved)

    agreement = _agreement(on_resolve=on_resolve)

    await engine.press(agreement, "agreement.approve", actor="2")
    await engine.press(agreement, "agreement.approve", actor="1")
    await engine.press(agreement, "agreement.approve", actor="1")

    assert agreement.approved == ("1", "2")
    assert agreement.resolved
    assert resolved == [("1", "2")], "resolving twice would append a second entry"
    assert all(not control.available for control in engine.find_all(engine.render_tree(agreement), ActionControl))


async def test_withdrawing_removes_only_the_pressing_participant() -> None:
    agreement = _agreement()

    await engine.press(agreement, "agreement.approve", actor="1")
    await engine.press(agreement, "agreement.withdraw", actor="1")

    assert agreement.approved == ()
    assert not agreement.resolved


async def test_an_outsider_is_refused_without_any_frontend_membership_check() -> None:
    """The participant list is the membership test; a transport's access policy is a second,
    independent gate, asserted in the adapter's suite."""
    agreement = _agreement(require=1)

    await engine.press(agreement, "agreement.approve", actor="99")

    assert agreement.approved == ()
    assert not agreement.resolved


def test_it_validates_identity_threshold_and_the_controls_it_offers() -> None:
    participant = sp.AgreementParticipant("1", "Alice")

    with pytest.raises(ValueError, match="at least one participant"):
        sp.Agreement("Prompt", ())
    with pytest.raises(ValueError, match="unique"):
        sp.Agreement("Prompt", (participant, participant))
    with pytest.raises(ValueError, match="reachable positive threshold"):
        sp.Agreement("Prompt", (participant,), require=2)

    without_withdraw = sp.Agreement("Prompt", (participant,), allow_withdraw=False)

    assert engine.labels(engine.render_tree(without_withdraw)) == ["Approve"]
