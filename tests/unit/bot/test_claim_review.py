"""Semantic creator-credit review workspace tests."""

from collections.abc import Sequence
from dataclasses import dataclass

from whenever import Instant

import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.accounts.domain import Account, AccountIdentity, AliasClaim, ClaimStatus
from squid.bot.claims_view import ClaimReviewComponent
from squid.permissions.domain import PermissionNode
from squid_ui.testing import RecordingResponder, choose, press
from squid_ui_discord.testing import commit_render
from tests.support.discord import make_layout_bot

AUTHOR_ID = 11
NOW = Instant.from_utc(2026, 8, 19)


def make_claim(claim_id: int, *, name: str = "Notch", discord_id: int = 555) -> AliasClaim:
    claimant = Account((AccountIdentity.discord(discord_id),), None, 42, NOW)
    return AliasClaim(claim_id, 9, name, 42, ClaimStatus.PENDING, NOW, claimant=claimant)


class AccountRecorder(AccountService):
    def __init__(self, claims: tuple[AliasClaim, ...]) -> None:
        self.claims = claims

    async def pending_alias_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        assert with_claimants is True
        return self.claims

    async def approve_alias_claim(
        self, claim_id: int, *, staff_account_id: int, reassign: bool = False
    ) -> AliasClaim:
        return next(claim for claim in self.claims if claim.id == claim_id)

    async def reject_alias_claim(self, claim_id: int, *, staff_account_id: int) -> AliasClaim:
        return next(claim for claim in self.claims if claim.id == claim_id)


@dataclass(frozen=True)
class ComponentHarness:
    component: ClaimReviewComponent
    accounts: AccountRecorder


def make_component(claims: tuple[AliasClaim, ...]) -> ComponentHarness:
    accounts = AccountRecorder(claims)

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
    payload = commit_render(bot.client_runtime.mount(component, access=sd.Owner(7), timeout=300)).to_components()

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
