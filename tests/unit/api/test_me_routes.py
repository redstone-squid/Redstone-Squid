"""Self-account route contracts.

These routes used to demand a Discord ID, so a CLI device or a Minecraft player holding a
perfectly good `account_id` was refused its own account. They are keyed on the account now, and
these tests pin that along with the Minecraft refresh responses.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from whenever import Instant

from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    Account,
    AccountConsent,
    AccountIdentity,
    AliasClaim,
    ClaimStatus,
    CreatorAlias,
    IdentityRefresh,
)
from squid.api.security import UNBOUNDED, Principal
from squid.api.v1.me import get_me, grant_consent, refresh_minecraft_identity, refresh_minecraft_identity_for
from squid.core.errors import AuthenticationError

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
NOW = Instant.from_utc(2026, 8, 16)


def _account(*, discord: bool = True) -> Account:
    identities = [AccountIdentity.java(JAVA_UUID, username="Steve")]
    if discord:
        identities.insert(0, AccountIdentity.discord(7))
    return Account(
        identities=tuple(identities),
        consent=AccountConsent(CURRENT_CONSENT_VERSION, NOW),
        id=1,
    )


def _principal(kind: str = "account", *, discord_id: int | None = 7) -> Principal:
    return Principal(
        kind=cast(Any, kind),
        subject=f"{kind}:1",
        nodes=UNBOUNDED,
        discord_id=discord_id,
        account_id=1,
    )


async def test_a_cli_principal_can_read_its_own_account() -> None:
    """The regression: no Discord identity, but a real account."""
    accounts = SimpleNamespace(get_account_by_id=AsyncMock(return_value=_account(discord=False)))

    response = await get_me(cast(Any, accounts), _principal("cli", discord_id=None))

    accounts.get_account_by_id.assert_awaited_once_with(1)
    assert response.id == 1
    assert response.discord_id is None
    assert response.ign == "Steve"


async def test_a_discord_principal_still_reports_its_discord_id() -> None:
    accounts = SimpleNamespace(get_account_by_id=AsyncMock(return_value=_account()))

    response = await get_me(cast(Any, accounts), _principal())

    assert response.discord_id == 7


async def test_a_principal_without_an_account_is_rejected() -> None:
    accounts = SimpleNamespace(get_account_by_id=AsyncMock())
    anonymous = Principal(kind="anonymous", subject="anonymous")

    with pytest.raises(AuthenticationError):
        await get_me(cast(Any, accounts), anonymous)


async def test_consent_is_granted_by_account_not_discord_id() -> None:
    accounts = SimpleNamespace(grant_current_consent_for_account=AsyncMock(return_value=_account(discord=False)))

    response = await grant_consent(cast(Any, accounts), _principal("cli", discord_id=None))

    accounts.grant_current_consent_for_account.assert_awaited_once_with(1)
    assert response.consent_pending is False


async def test_refresh_reports_a_rename_and_its_new_credit() -> None:
    refresh = IdentityRefresh(
        account_id=1,
        java_uuid=JAVA_UUID,
        current_name="NewName",
        previous_name="OldName",
        claimed_alias=CreatorAlias(5, "NewName", account_id=1),
        retained_alias_names=("OldName",),
    )
    accounts = SimpleNamespace(refresh_java_identity=AsyncMock(return_value=refresh))

    response = await refresh_minecraft_identity(cast(Any, accounts), _principal())

    accounts.refresh_java_identity.assert_awaited_once_with(1)
    assert response.renamed is True
    assert response.previous_ign == "OldName"
    assert response.ign == "NewName"
    assert response.claimed_creator_name == "NewName"
    assert response.retained_creator_names == ("OldName",)
    assert response.contested_creator_name is None
    assert response.pending_claim_id is None


async def test_refresh_reports_a_contested_name_without_claiming_it() -> None:
    refresh = IdentityRefresh(
        account_id=1,
        java_uuid=JAVA_UUID,
        current_name="Contested",
        previous_name="OldName",
        contested_alias=CreatorAlias(9, "Contested", account_id=2),
        opened_claim=AliasClaim(3, 9, "Contested", 1, ClaimStatus.PENDING, NOW),
    )
    accounts = SimpleNamespace(refresh_java_identity=AsyncMock(return_value=refresh))

    response = await refresh_minecraft_identity(cast(Any, accounts), _principal())

    assert response.claimed_creator_name is None
    assert response.contested_creator_name == "Contested"
    assert response.pending_claim_id == 3


async def test_refresh_reports_an_unchanged_name() -> None:
    refresh = IdentityRefresh(
        account_id=1,
        java_uuid=JAVA_UUID,
        current_name="Steve",
        previous_name="Steve",
        claimed_alias=CreatorAlias(5, "Steve", account_id=1),
    )
    accounts = SimpleNamespace(refresh_java_identity=AsyncMock(return_value=refresh))

    response = await refresh_minecraft_identity(cast(Any, accounts), _principal())

    assert response.renamed is False
    assert response.previous_ign == "Steve"


async def test_staff_refresh_targets_the_named_account() -> None:
    refresh = IdentityRefresh(account_id=42, java_uuid=JAVA_UUID, current_name="Steve", previous_name="Steve")
    accounts = SimpleNamespace(refresh_java_identity=AsyncMock(return_value=refresh))

    response = await refresh_minecraft_identity_for(42, cast(Any, accounts))

    accounts.refresh_java_identity.assert_awaited_once_with(42)
    assert response.ign == "Steve"
