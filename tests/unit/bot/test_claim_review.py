"""The panel that replaced `account approve-claim` and `account reject-claim`."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest
from whenever import Instant

from squid.accounts.domain import Account, AccountIdentity, AliasClaim, ClaimStatus
from squid.accounts.errors import AliasAlreadyClaimedError
from squid.bot import claims_view
from squid.bot.claims_view import ApproveClaimButton, ClaimReviewView, ClaimSelect, RejectClaimButton

AUTHOR_ID = 11
NOW = Instant.from_utc(2026, 8, 19)


def make_claim(claim_id: int, *, name: str = "Notch", discord_id: int = 555) -> AliasClaim:
    claimant = Account((AccountIdentity.discord(discord_id),), None, 42, NOW)
    return AliasClaim(claim_id, 9, name, 42, ClaimStatus.PENDING, NOW, claimant=claimant)


def make_panel(
    claims: tuple[AliasClaim, ...],
    *,
    can_approve: bool = True,
    can_reject: bool = True,
    approve: Any = None,
    remaining: tuple[AliasClaim, ...] | None = None,
) -> tuple[ClaimReviewView, Any]:
    accounts = SimpleNamespace(
        pending_alias_claims=AsyncMock(return_value=claims if remaining is None else remaining),
        approve_alias_claim=approve or AsyncMock(return_value=claims[0] if claims else None),
        reject_alias_claim=AsyncMock(return_value=claims[0] if claims else None),
    )
    view = ClaimReviewView(
        cast(Any, accounts),
        claims,
        author_id=AUTHOR_ID,
        can_approve=can_approve,
        can_reject=can_reject,
    )
    return view, accounts


def make_interaction() -> Any:
    """A component interaction that remembers having been deferred.

    The stub tracks `is_done` because the callbacks depend on it: deferring the update is what
    lets one click both redraw the panel and answer with a followup.
    """
    deferred = False

    async def defer(*args: object, **kwargs: object) -> None:
        nonlocal deferred
        deferred = True

    return SimpleNamespace(
        user=SimpleNamespace(id=AUTHOR_ID),
        message=None,
        response=SimpleNamespace(
            edit_message=AsyncMock(),
            send_message=AsyncMock(),
            defer=AsyncMock(side_effect=defer),
            is_done=lambda: deferred,
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


@pytest.fixture(autouse=True)
def _reviewer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Grant the click-time re-check and the consent gate; both are tested elsewhere."""
    monkeypatch.setattr(claims_view, "enforce", AsyncMock())
    monkeypatch.setattr(claims_view, "ensure_consented_account", AsyncMock(return_value=7))


def text_of(view: discord.ui.LayoutView) -> str:
    return "\n".join(child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay))


def buttons_of(view: discord.ui.LayoutView) -> list[str]:
    return [
        str(child.label)
        for child in view.walk_children()
        if isinstance(child, ApproveClaimButton | RejectClaimButton) and child.label is not None
    ]


def select_of(view: ClaimReviewView) -> ClaimSelect:
    return next(child for child in view.walk_children() if isinstance(child, ClaimSelect))


def test_the_queue_names_its_claimants_and_offers_them_for_review() -> None:
    view, _ = make_panel((make_claim(7),))

    assert "Claim #7 — Notch" in text_of(view)
    assert "<@555>" in text_of(view)
    assert [option.value for option in select_of(view).options] == ["7"]


def test_a_reviewer_is_shown_only_the_decisions_they_hold() -> None:
    """A control nobody can use should not be offered; `enforce` is still the gate."""
    approver, _ = make_panel((make_claim(7),), can_reject=False)
    rejecter, _ = make_panel((make_claim(7),), can_approve=False)

    assert buttons_of(approver) == ["Approve"]
    assert buttons_of(rejecter) == ["Reject"]


def test_the_decisions_wait_for_a_claim_to_be_picked() -> None:
    view, _ = make_panel((make_claim(7),))
    disabled = [
        child.disabled for child in view.walk_children() if isinstance(child, ApproveClaimButton | RejectClaimButton)
    ]

    assert disabled == [True, True]

    view.select(7)

    assert [
        child.disabled for child in view.walk_children() if isinstance(child, ApproveClaimButton | RejectClaimButton)
    ] == [False, False]


async def test_approving_credits_the_selected_claim_and_says_so_publicly() -> None:
    """The credit is a change to shared state, so the decision leaves a public artifact."""
    claim = make_claim(7)
    view, accounts = make_panel((claim,), remaining=())
    view.select(7)
    interaction = make_interaction()

    await view.approve(cast(Any, interaction))

    assert accounts.approve_alias_claim.await_args is not None
    assert accounts.approve_alias_claim.await_args.args == (7,)
    assert accounts.approve_alias_claim.await_args.kwargs["reassign"] is False
    assert interaction.followup.send.await_args is not None
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is False


async def test_a_contested_name_asks_before_it_transfers() -> None:
    """A transfer used to be a flag on the command; it is now a second, deliberate click."""
    claim = make_claim(7)
    approve = AsyncMock(side_effect=[AliasAlreadyClaimedError("Notch"), claim])
    view, _ = make_panel((claim,), approve=approve, remaining=())
    view.select(7)

    await view.approve(cast(Any, make_interaction()))

    assert approve.await_args_list[0].kwargs["reassign"] is False
    assert buttons_of(view)[0] == "Take the name"

    await view.approve(cast(Any, make_interaction()))

    assert approve.await_args_list[1].kwargs["reassign"] is True


async def test_picking_another_claim_disarms_a_pending_transfer() -> None:
    claims = (make_claim(7), make_claim(8, name="Herobrine"))
    view, _ = make_panel(claims, approve=AsyncMock(side_effect=AliasAlreadyClaimedError("Notch")))
    view.select(7)

    await view.approve(cast(Any, make_interaction()))
    assert view.reassign_armed is True

    view.select(8)

    assert view.reassign_armed is False
    assert buttons_of(view)[0] == "Approve"


async def test_a_resolved_claim_leaves_the_queue() -> None:
    view, _ = make_panel((make_claim(7),), remaining=())

    view.select(7)
    await view.reject(cast(Any, make_interaction()))

    assert "No creator credit claims are awaiting review." in text_of(view)
    assert view.selected is None
