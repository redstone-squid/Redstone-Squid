"""Semantic creator-credit review workspace tests."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import override

import discord
import pytest
from whenever import Instant

import squid.bot.claims_view as claims_view
import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.accounts.domain import ClaimStatus
from squid.accounts.errors import AliasAlreadyClaimedError
from squid.accounts.models import Account, AccountIdentity, AliasClaim
from squid.bot.claims_view import ClaimReviewComponent
from squid.bot.consent import ConsentedAccountWork
from squid.permissions.domain import PermissionNode
from squid_ui.testing import RecordingResponder, choose, press_event
from squid_ui_discord.testing import (
    AsyncCallRecorder,
    commit_render,
    delivered_to,
    http_error,
    interaction_harness,
    message_harness,
)
from tests.support.discord import make_layout_bot

AUTHOR_ID = 11
NOW = Instant.from_utc(2026, 8, 19)


def make_claim(claim_id: int, *, name: str = "Notch", discord_id: int = 555) -> AliasClaim:
    claimant = Account(identities=(AccountIdentity.discord(discord_id),), consent=None, id=42, created_at=NOW)
    return AliasClaim(
        id=claim_id,
        alias_id=9,
        alias_name=name,
        account_id=42,
        status=ClaimStatus.PENDING,
        created_at=NOW,
        claimant=claimant,
    )


class AccountRecorder(AccountService):
    def __init__(self, claims: tuple[AliasClaim, ...], *, conflict: bool = False) -> None:
        self.claims = claims
        self.conflict = conflict
        self.decisions: list[tuple[str, int, int, bool]] = []

    @override
    async def pending_alias_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        assert with_claimants is True
        return self.claims

    @override
    async def approve_alias_claim(self, claim_id: int, *, staff_account_id: int, reassign: bool = False) -> AliasClaim:
        self.decisions.append(("approve", claim_id, staff_account_id, reassign))
        if self.conflict and not reassign:
            raise AliasAlreadyClaimedError("Notch").with_holder_name("Builder")
        return next(claim for claim in self.claims if claim.id == claim_id)

    @override
    async def reject_alias_claim(self, claim_id: int, *, staff_account_id: int) -> AliasClaim:
        self.decisions.append(("reject", claim_id, staff_account_id, False))
        return next(claim for claim in self.claims if claim.id == claim_id)


@dataclass(frozen=True)
class ComponentHarness:
    component: ClaimReviewComponent
    accounts: AccountRecorder


def make_component(claims: tuple[AliasClaim, ...], *, conflict: bool = False) -> ComponentHarness:
    accounts = AccountRecorder(claims, conflict=conflict)

    async def authorize(_node: PermissionNode) -> bool:
        return True

    return ComponentHarness(
        ClaimReviewComponent(
            accounts,
            claims,
            author_id=AUTHOR_ID,
            can_approve=True,
            can_reject=True,
            authorize=authorize,
        ),
        accounts,
    )


def test_the_queue_names_claimants_in_a_semantic_render() -> None:
    component = make_component((make_claim(7),)).component

    bot = make_layout_bot()
    payload = commit_render(bot.ui.mount(component, access=sd.Owner(7), timeout=300)).to_components()

    assert "Claim #7" in str(payload)
    assert "Notch" in str(payload)
    assert "<@555>" in str(payload)
    assert "Approve" in str(payload)
    assert "Reject" in str(payload)


async def test_selecting_a_claim_enables_decisions() -> None:
    component = make_component((make_claim(7),)).component

    await choose(component, "claim", "7")

    assert component.selected_id == 7
    assert component.selected is not None
    assert component.reassign_armed is None


@pytest.mark.parametrize(
    ("approve", "expected"),
    [
        pytest.param(True, "Credited **Notch** to <@555>.", id="approve"),
        pytest.param(False, "Closed <@555>'s claim on **Notch** without crediting it.", id="reject"),
    ],
)
async def test_claim_decisions_name_the_claimant_in_the_public_confirmation(
    approve: bool,
    expected: str,
) -> None:
    claim = make_claim(7)
    harness = make_component((claim,))
    responder = RecordingResponder()

    await harness.component._resolve(press_event(responder=responder), claim, AUTHOR_ID, approve=approve)

    assert responder.notices == [(expected, sl.interactions.Visibility.PUBLIC)]
    action = "approve" if approve else "reject"
    assert harness.accounts.decisions == [(action, 7, AUTHOR_ID, False)]


async def test_a_held_name_explains_the_conflict_then_requires_deliberate_reassignment() -> None:
    claim = make_claim(7)
    harness = make_component((claim,), conflict=True)
    first = RecordingResponder()

    await harness.component._resolve(press_event(responder=first), claim, AUTHOR_ID, approve=True)

    assert harness.component.reassign_armed == claim.id
    assert "Builder" in first.notices[0][0]
    assert "Approving again takes the name" in first.notices[0][0]
    assert first.notices[0][1] is sl.interactions.Visibility.PUBLIC

    second = RecordingResponder()
    await harness.component._resolve(press_event(responder=second), claim, AUTHOR_ID, approve=True)

    assert harness.accounts.decisions == [
        ("approve", 7, AUTHOR_ID, False),
        ("approve", 7, AUTHOR_ID, True),
    ]
    assert second.notices == [("Credited **Notch** to <@555>.", sl.interactions.Visibility.PUBLIC)]


async def test_approving_after_an_undelivered_conflict_render_does_not_take_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader never saw "Take the name", so their next Approve must not reassign it."""

    async def consented(event: sd.ActionEvent, accounts: AccountService, work: ConsentedAccountWork) -> None:
        del accounts
        await work(event, AUTHOR_ID)

    monkeypatch.setattr(claims_view, "with_consented_account", consented)
    harness = make_component((make_claim(7),), conflict=True)
    message_root = make_layout_bot().ui.mount(harness.component, access=sd.Owner(AUTHOR_ID), timeout=300)
    with sl.runtime.transaction():
        harness.component.selected_id = 7
    message = message_harness()
    await message_root.send(delivered_to(message))
    approve = next(key for key in message_root.snapshot().handler_keys if key.endswith("approve"))
    generation = message_root.generation

    # The conflict notice answers the press, so the panel repaints through the sent message.
    message.edit.error = http_error(message="edit refused")
    with pytest.raises(discord.HTTPException):
        await message_root.dispatch(approve, interaction_harness(AUTHOR_ID), generation=generation)
    assert message_root.generation == generation
    message.edit.error = None
    retry = interaction_harness(AUTHOR_ID)
    await message_root.dispatch(approve, retry, generation=generation)

    assert harness.accounts.decisions == [("approve", 7, AUTHOR_ID, False)]
    assert "Take the name" in str(retry.response.edit_message.await_args.kwargs["view"].to_components())


async def test_approving_through_the_panel_removes_the_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    async def consented(event: sd.ActionEvent, accounts: AccountService, work: ConsentedAccountWork) -> None:
        del accounts
        await work(event, AUTHOR_ID)

    monkeypatch.setattr(claims_view, "with_consented_account", consented)
    harness = make_component((make_claim(7),))
    on_error = AsyncCallRecorder()
    message_root = make_layout_bot().ui.mount(
        harness.component, access=sd.Owner(AUTHOR_ID), timeout=300, on_error=on_error
    )
    with sl.runtime.transaction():
        harness.component.selected_id = 7
    await message_root.send(delivered_to(message_harness()))
    approve = next(key for key in message_root.snapshot().handler_keys if key.endswith("approve"))

    async def nothing_pending(*, with_claimants: bool = False) -> Sequence[AliasClaim]:
        del with_claimants
        return ()

    # The approval resolves claim 7, so the reload after it finds nothing left to review.
    monkeypatch.setattr(harness.accounts, "pending_alias_claims", nothing_pending)
    await message_root.dispatch(approve, interaction_harness(AUTHOR_ID), generation=message_root.generation)

    on_error.assert_not_awaited()
    assert harness.accounts.decisions == [("approve", 7, AUTHOR_ID, False)]
    assert harness.component.claims == ()
