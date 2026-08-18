"""Self-account route contracts.

These routes used to demand a Discord ID, so a CLI device or a Minecraft player holding a
perfectly good `account_id` was refused its own account. They are keyed on the account now, and
these tests pin that along with the Minecraft refresh responses.
"""

from dataclasses import replace
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
    AccountProfile,
    AliasClaim,
    ClaimStatus,
    CreatorAlias,
    IdentityProvider,
    IdentityRefresh,
)
from squid.api.security import UNBOUNDED, Caller
from squid.api.v1.me import get_me, grant_consent, refresh_minecraft_identity, refresh_minecraft_identity_for
from squid.core.errors import AuthenticationError

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
CREATOR_ID = UUID("22222222-2222-4222-8222-222222222222")
NOW = Instant.from_utc(2026, 8, 16)


def _account(*, discord: bool = True) -> Account:
    identities = [replace(AccountIdentity.java(JAVA_UUID, username="Steve"), id=2)]
    if discord:
        identities.insert(0, replace(AccountIdentity.discord(7), id=1))
    return Account(
        identities=tuple(identities),
        consent=AccountConsent(CURRENT_CONSENT_VERSION, NOW),
        id=1,
        created_at=NOW,
        public_creator_id=CREATOR_ID,
    )


def _accounts(**overrides: object) -> Any:
    """A stub account service carrying the two reads the self view always makes."""
    defaults: dict[str, object] = {
        "get_account_by_id": AsyncMock(return_value=_account()),
        "get_profile": AsyncMock(return_value=AccountProfile.empty(1)),
        "list_identities": AsyncMock(return_value=_account().identities),
    }
    return cast(Any, SimpleNamespace(**(defaults | overrides)))


def _identity_of(response: Any, provider: IdentityProvider) -> Any:
    return next((identity for identity in response.identities if identity.provider is provider), None)


def _caller(kind: str = "account") -> Caller:
    """A caller carries an account and nothing about how it authenticated."""
    return Caller(kind=cast(Any, kind), subject=f"{kind}:1", nodes=UNBOUNDED, account_id=1)


async def test_a_cli_caller_can_read_its_own_account() -> None:
    """The regression: no Discord identity, but a real account."""
    accounts = _accounts(get_account_by_id=AsyncMock(return_value=_account(discord=False)))

    response = await get_me(accounts, _caller("cli"))

    accounts.get_account_by_id.assert_awaited_once_with(1)
    assert response.id == 1
    assert response.creator_id == CREATOR_ID
    assert _identity_of(response, IdentityProvider.DISCORD) is None
    assert _identity_of(response, IdentityProvider.JAVA).display_name == "Steve"


async def test_the_response_lists_identities_rather_than_flattening_them() -> None:
    """The self view is provider-neutral.

    The old shape had one `discord_id` and one `minecraft_uuid` field, which could not describe
    an account holding two identities from one provider — which a merge produces — and implied
    Discord by omission for callers that have none.
    """
    response = await get_me(_accounts(), _caller())

    assert [identity.provider for identity in response.identities] == [
        IdentityProvider.DISCORD,
        IdentityProvider.JAVA,
    ]
    assert _identity_of(response, IdentityProvider.DISCORD).subject == "7"
    assert all(identity.is_public for identity in response.identities)


async def test_the_response_carries_the_profile() -> None:
    profile = AccountProfile(account_id=1, display_name="Steve", bio="I build", hidden=True)
    response = await get_me(_accounts(get_profile=AsyncMock(return_value=profile)), _caller())

    assert response.profile.display_name == "Steve"
    assert response.profile.bio == "I build"
    assert response.profile.hidden is True
    assert response.profile.avatar is None


async def test_the_response_renders_a_java_avatar_from_its_source_identity() -> None:
    profile = AccountProfile(account_id=1, avatar_identity_id=2)
    response = await get_me(_accounts(get_profile=AsyncMock(return_value=profile)), _caller())

    assert response.profile.avatar is not None
    assert response.profile.avatar.identity_id == 2
    assert response.profile.avatar.url == f"https://mc-heads.net/avatar/{JAVA_UUID}"


async def test_a_caller_without_an_account_is_rejected() -> None:
    anonymous = Caller(kind="anonymous", subject="anonymous")

    with pytest.raises(AuthenticationError):
        await get_me(_accounts(), anonymous)


async def test_consent_is_granted_by_account_not_discord_id() -> None:
    accounts = _accounts(
        grant_current_consent=AsyncMock(return_value=_account(discord=False)),
        get_account_by_id=AsyncMock(return_value=_account(discord=False)),
    )

    response = await grant_consent(accounts, _caller("cli"))

    accounts.grant_current_consent.assert_awaited_once_with(1)
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

    response = await refresh_minecraft_identity(cast(Any, accounts), _caller())

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

    response = await refresh_minecraft_identity(cast(Any, accounts), _caller())

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

    response = await refresh_minecraft_identity(cast(Any, accounts), _caller())

    assert response.renamed is False
    assert response.previous_ign == "Steve"


async def test_staff_refresh_targets_the_named_account() -> None:
    refresh = IdentityRefresh(account_id=42, java_uuid=JAVA_UUID, current_name="Steve", previous_name="Steve")
    accounts = SimpleNamespace(refresh_java_identity=AsyncMock(return_value=refresh))

    response = await refresh_minecraft_identity_for(42, cast(Any, accounts))

    accounts.refresh_java_identity.assert_awaited_once_with(42)
    assert response.ign == "Steve"
