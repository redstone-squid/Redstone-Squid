"""Semantic creator-credit review workspace tests."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from whenever import Instant

from squid.accounts.domain import Account, AccountIdentity, AliasClaim, ClaimStatus
from squid.bot.claims_view import ClaimReviewComponent
from squid_layouts.discord.testing import commit_render
from tests.helpers.discord import make_layout_bot

AUTHOR_ID = 11
NOW = Instant.from_utc(2026, 8, 19)


def make_claim(claim_id: int, *, name: str = "Notch", discord_id: int = 555) -> AliasClaim:
    claimant = Account((AccountIdentity.discord(discord_id),), None, 42, NOW)
    return AliasClaim(claim_id, 9, name, 42, ClaimStatus.PENDING, NOW, claimant=claimant)


def make_component(claims: tuple[AliasClaim, ...]) -> ClaimReviewComponent:
    accounts = SimpleNamespace(
        pending_alias_claims=AsyncMock(return_value=claims),
        approve_alias_claim=AsyncMock(return_value=claims[0] if claims else None),
        reject_alias_claim=AsyncMock(return_value=claims[0] if claims else None),
    )
    return ClaimReviewComponent(
        cast(Any, accounts),
        claims,
        author_id=AUTHOR_ID,
        can_approve=True,
        can_reject=True,
    )


def test_the_queue_names_claimants_in_a_semantic_render() -> None:
    component = make_component((make_claim(7),))

    payload = commit_render(component.mount(source=make_layout_bot())).to_components()

    assert "Claim #7" in str(payload)
    assert "Notch" in str(payload)
    assert "<@555>" in str(payload)
    assert "Approve" in str(payload)
    assert "Reject" in str(payload)


async def test_selecting_a_claim_enables_decisions() -> None:
    component = make_component((make_claim(7),))

    await component._select_claim(cast(Any, SimpleNamespace(selected=("7",))))

    assert component.selected_id == 7
    assert component.selected is not None
    assert component.reassign_armed is None


async def test_closing_finishes_the_semantic_mount() -> None:
    component = make_component((make_claim(7),))
    event = SimpleNamespace(finish=AsyncMock())

    await component._close(cast(Any, event))

    assert component.closed is True
    event.finish.assert_awaited_once()
