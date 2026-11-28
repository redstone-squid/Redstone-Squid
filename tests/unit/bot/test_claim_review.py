"""Semantic creator-credit review workspace tests."""

from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from typing import override
from unittest.mock import AsyncMock
from uuid import UUID

import discord
import pytest
from whenever import Instant

import squid.bot.accounts.claims as claims_view
import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.accounts.claims import ClaimReviewComponent
from squid.core.accounts.application import AccountService
from squid.core.accounts.domain import ClaimStatus
from squid.core.accounts.errors import AliasAlreadyClaimedError
from squid.core.accounts.models import Account, AccountIdentity, AliasClaim
from squid.core.actors import ActingContext, UserActor
from squid.core.permissions.domain.catalogue import ACCOUNT_CLAIM_APPROVE, ACCOUNT_CLAIM_REJECT
from squid_ui.ext.discord.testing import (
    AsyncCallRecorder,
    commit_render,
    delivered_to,
    http_error,
    interaction_harness,
    message_harness,
)
from squid_ui.testing import RecordingResponder, choose, keys, press_event, render_tree
from tests.support.discord import make_layout_bot
from tests.support.grants import grants

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
    async def pending_alias_claims(
        self, *, context: ActingContext, with_claimants: bool = False
    ) -> Sequence[AliasClaim]:
        assert with_claimants is True
        return self.claims

    @override
    async def approve_alias_claim(
        self,
        claim_id: int,
        *,
        context: ActingContext,
        reassign: bool = False,
        credit_ids: Collection[UUID] | None = None,
    ) -> AliasClaim:
        staff_account_id = context.effective.require_account_id()
        self.decisions.append(("approve", claim_id, staff_account_id, reassign))
        if self.conflict and not reassign:
            raise AliasAlreadyClaimedError("Notch").with_holder_name("Builder")
        return next(claim for claim in self.claims if claim.id == claim_id)

    @override
    async def reject_alias_claim(self, claim_id: int, *, context: ActingContext) -> AliasClaim:
        staff_account_id = context.effective.require_account_id()
        self.decisions.append(("reject", claim_id, staff_account_id, False))
        return next(claim for claim in self.claims if claim.id == claim_id)


@dataclass(frozen=True)
class ComponentHarness:
    component: ClaimReviewComponent
    accounts: AccountRecorder


def make_component(claims: tuple[AliasClaim, ...], *, conflict: bool = False) -> ComponentHarness:
    accounts = AccountRecorder(claims, conflict=conflict)

    return ComponentHarness(
        ClaimReviewComponent(
            accounts,
            claims,
            actor=AsyncMock(return_value=UserActor(account_id=AUTHOR_ID)),
            author_id=AUTHOR_ID,
            grants=grants(ACCOUNT_CLAIM_APPROVE, ACCOUNT_CLAIM_REJECT),
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


async def test_selecting_a_page_of_credits_keeps_other_pages_selected() -> None:
    claim = make_claim(7)
    claim.selected_credits = {str(UUID(int=i)): {"build_id": i, "account_id": None} for i in range(1, 27)}
    component = make_component((claim,)).component
    await choose(component, "claim", "7")
    payload = commit_render(make_layout_bot().ui.mount(component, access=sd.Owner(7), timeout=300)).to_components()
    assert "Build #25" in str(payload)
    await choose(component, "selected-credits-0", str(UUID(int=1)))
    assert set(component.included or ()) == {str(UUID(int=1)), str(UUID(int=26))}


@pytest.mark.parametrize(
    ("approve", "expected"),
    [
        pytest.param(True, "Approved the selected **Notch** credits for @555.", id="approve"),
        pytest.param(False, "Closed @555's claim on **Notch** without crediting it.", id="reject"),
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
    assert "Approving again reassigns the selected credits" in first.notices[0][0]
    assert first.notices[0][1] is sl.interactions.Visibility.PUBLIC

    second = RecordingResponder()
    await harness.component._resolve(press_event(responder=second), claim, AUTHOR_ID, approve=True)

    assert harness.accounts.decisions == [
        ("approve", 7, AUTHOR_ID, False),
        ("approve", 7, AUTHOR_ID, True),
    ]
    assert second.notices == [("Approved the selected **Notch** credits for @555.", sl.interactions.Visibility.PUBLIC)]


async def test_approving_after_an_undelivered_conflict_render_does_not_take_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader never saw "Reassign credits", so their next Approve must not reassign it."""

    async def consented(
        event: sd.ActionEvent, accounts: AccountService, work: Callable[[sd.ActionEvent, int], Awaitable[None]]
    ) -> None:
        del accounts
        await work(event, AUTHOR_ID)

    monkeypatch.setattr(claims_view, "with_account", consented)
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
    assert "Reassign credits" in str(message.edit.await_args.kwargs["view"].to_components())


async def test_approving_through_the_panel_removes_the_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    async def consented(
        event: sd.ActionEvent, accounts: AccountService, work: Callable[[sd.ActionEvent, int], Awaitable[None]]
    ) -> None:
        del accounts
        await work(event, AUTHOR_ID)

    monkeypatch.setattr(claims_view, "with_account", consented)
    harness = make_component((make_claim(7),))
    on_error = AsyncCallRecorder()
    message_root = make_layout_bot().ui.mount(
        harness.component, access=sd.Owner(AUTHOR_ID), timeout=300, on_error=on_error
    )
    with sl.runtime.transaction():
        harness.component.selected_id = 7
    await message_root.send(delivered_to(message_harness()))
    approve = next(key for key in message_root.snapshot().handler_keys if key.endswith("approve"))

    async def nothing_pending(*, context: ActingContext, with_claimants: bool = False) -> Sequence[AliasClaim]:
        del with_claimants
        return ()

    # The approval resolves claim 7, so the reload after it finds nothing left to review.
    monkeypatch.setattr(harness.accounts, "pending_alias_claims", nothing_pending)
    await message_root.dispatch(approve, interaction_harness(AUTHOR_ID), generation=message_root.generation)

    on_error.assert_not_awaited()
    assert harness.accounts.decisions == [("approve", 7, AUTHOR_ID, False)]
    assert harness.component.claims == ()


def test_an_empty_queue_offers_no_review_actions() -> None:
    tree = render_tree(make_component(()).component)

    assert not {"approve", "reject"} & set(keys(tree))
