"""Semantic creator-credit review workspace tests."""

from collections.abc import Sequence
from dataclasses import dataclass

import pytest
from whenever import Instant

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.accounts.domain import Account, AccountIdentity, AliasClaim, ClaimStatus
from squid.accounts.errors import AliasAlreadyClaimedError
from squid.bot.claims_view import ClaimReviewComponent
from squid.permissions.domain import PermissionNode
from squid_ui.testing import RecordingResponder, choose, press, press_event
from squid_ui_discord.testing import commit_render
from tests.support.discord import make_layout_bot

AUTHOR_ID = 11
NOW = Instant.from_utc(2026, 8, 19)


def make_claim(claim_id: int, *, name: str = "Notch", discord_id: int = 555) -> AliasClaim:
    claimant = Account((AccountIdentity.discord(discord_id),), None, 42, NOW)
    return AliasClaim(claim_id, 9, name, 42, ClaimStatus.PENDING, NOW, claimant=claimant)


class AccountRecorder(AccountService):
    def __init__(self, claims: tuple[AliasClaim, ...], *, conflict: bool = False) -> None:
        self.claims = claims
        self.conflict = conflict
        self.decisions: list[tuple[str, int, int, bool]] = []

    async def pending_alias_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        assert with_claimants is True
        return self.claims

    async def approve_alias_claim(self, claim_id: int, *, staff_account_id: int, reassign: bool = False) -> AliasClaim:
        self.decisions.append(("approve", claim_id, staff_account_id, reassign))
        if self.conflict and not reassign:
            raise AliasAlreadyClaimedError("Notch").with_holder_name("Builder")
        return next(claim for claim in self.claims if claim.id == claim_id)

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


async def test_closing_finishes_the_semantic_root() -> None:
    component = make_component((make_claim(7),)).component
    responder = RecordingResponder()

    await press(component, "close", responder=responder)

    assert component.closed is True
    assert responder.finished is True


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
