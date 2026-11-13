"""Self-account routes use account identity for browser, CLI and Minecraft callers.

These tests also cover public identity/profile projections and Minecraft rename results.
"""

from dataclasses import replace
from typing import Any, cast, override
from uuid import UUID

import pytest
from whenever import Instant

from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    AccountConsent,
    ClaimStatus,
    IdentityProvider,
    IdentityRefresh,
)
from squid.accounts.models import Account, AccountIdentity, AccountProfile, AliasClaim, CreatorAlias
from squid.api.security import UNBOUNDED, Caller
from squid.api.v1.me import get_me, grant_consent, refresh_minecraft_identity, refresh_minecraft_identity_for
from squid.core.errors import AuthenticationError
from tests.support.account_edits import SnapshotAccountService

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


class AccountRecorder(SnapshotAccountService):
    def __init__(
        self,
        *,
        account: Account | None = None,
        profile: AccountProfile | None = None,
        refresh: IdentityRefresh | None = None,
    ) -> None:
        self.account = account or _account()
        self.profile = profile or AccountProfile.empty(1)
        self.refresh = refresh
        self.account_reads: list[int] = []
        self.consent_grants: list[int] = []
        self.refreshes: list[tuple[int, UUID | None]] = []

    @override
    async def get_account_by_id(self, account_id: int) -> Account | None:
        self.account_reads.append(account_id)
        return self.account

    @override
    async def get_profile(self, account_id: int) -> AccountProfile:
        assert account_id == 1
        return self.profile

    @override
    async def list_identities(self, account_id: int) -> tuple[AccountIdentity, ...]:
        assert account_id == 1
        return self.account.identities

    @override
    async def grant_current_consent(self, account_id: int) -> Account:
        self.consent_grants.append(account_id)
        return self.account

    @override
    async def refresh_java_identity(self, account_id: int, *, java_uuid: UUID | None = None) -> IdentityRefresh:
        self.refreshes.append((account_id, java_uuid))
        assert self.refresh is not None
        return self.refresh


def _accounts(
    *,
    account: Account | None = None,
    profile: AccountProfile | None = None,
    refresh: IdentityRefresh | None = None,
) -> AccountRecorder:
    return AccountRecorder(account=account, profile=profile, refresh=refresh)


def _identity_of(response: Any, provider: IdentityProvider) -> Any:
    return next((identity for identity in response.identities if identity.provider is provider), None)


def _caller(kind: str = "account") -> Caller:
    """An account-backed caller without a Discord-specific identity."""
    return Caller(kind=cast(Any, kind), subject=f"{kind}:1", nodes=UNBOUNDED, account_id=1)


async def test_a_cli_caller_can_read_its_own_account() -> None:
    """A Java-only account remains readable through its CLI credential."""
    accounts = _accounts(account=_account(discord=False))
    response = (await get_me.fn(accounts, _caller("cli"))).content
    assert accounts.account_reads == [1]
    assert response.id == 1
    assert response.creator_id == CREATOR_ID
    assert _identity_of(response, IdentityProvider.DISCORD) is None
    assert _identity_of(response, IdentityProvider.JAVA).display_name == "Steve"


async def test_the_response_lists_identities_rather_than_flattening_them() -> None:
    """The self view identifies each linked provider separately."""
    response = (await get_me.fn(_accounts(), _caller())).content
    assert [identity.provider for identity in response.identities] == [IdentityProvider.DISCORD, IdentityProvider.JAVA]
    assert _identity_of(response, IdentityProvider.DISCORD).subject == "7"
    assert all(identity.is_public for identity in response.identities)


async def test_the_response_carries_the_profile() -> None:
    profile = AccountProfile(account_id=1, display_name="Steve", bio="I build", hidden=True)
    response = (await get_me.fn(_accounts(profile=profile), _caller())).content
    assert response.profile.display_name == "Steve"
    assert response.profile.bio == "I build"
    assert response.profile.hidden is True
    assert response.profile.avatar is None


async def test_the_response_renders_a_java_avatar_from_its_source_identity() -> None:
    profile = AccountProfile(account_id=1, avatar_identity_id=2)
    response = (await get_me.fn(_accounts(profile=profile), _caller())).content
    assert response.profile.avatar is not None
    assert response.profile.avatar.identity_id == 2
    assert response.profile.avatar.url == f"https://mc-heads.net/avatar/{JAVA_UUID}"


async def test_a_caller_without_an_account_is_rejected() -> None:
    anonymous = Caller(kind="anonymous", subject="anonymous")
    with pytest.raises(AuthenticationError):
        await get_me.fn(_accounts(), anonymous)


async def test_consent_is_granted_by_account_not_discord_id() -> None:
    accounts = _accounts(account=_account(discord=False))
    response = await grant_consent.fn(accounts, _caller("cli"), _reservation=None)
    assert accounts.consent_grants == [1]
    assert response.consent_pending is False


async def test_refresh_reports_a_rename_and_its_new_credit() -> None:
    refresh = IdentityRefresh(
        account_id=1,
        java_uuid=JAVA_UUID,
        current_name="NewName",
        previous_name="OldName",
        claimed_alias=CreatorAlias(id=5, name="NewName", account_id=1),
        retained_alias_names=("OldName",),
    )
    accounts = _accounts(refresh=refresh)
    response = await refresh_minecraft_identity.fn(accounts, _caller(), _reservation=None)
    assert accounts.refreshes == [(1, None)]
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
        contested_alias=CreatorAlias(id=9, name="Contested", account_id=2),
        opened_claim=AliasClaim(
            id=3, alias_id=9, alias_name="Contested", account_id=1, status=ClaimStatus.PENDING, created_at=NOW
        ),
    )
    accounts = _accounts(refresh=refresh)
    response = await refresh_minecraft_identity.fn(accounts, _caller(), _reservation=None)
    assert response.claimed_creator_name is None
    assert response.contested_creator_name == "Contested"
    assert response.pending_claim_id == 3


async def test_refresh_reports_an_unchanged_name() -> None:
    refresh = IdentityRefresh(
        account_id=1,
        java_uuid=JAVA_UUID,
        current_name="Steve",
        previous_name="Steve",
        claimed_alias=CreatorAlias(id=5, name="Steve", account_id=1),
    )
    accounts = _accounts(refresh=refresh)
    response = await refresh_minecraft_identity.fn(accounts, _caller(), _reservation=None)
    assert response.renamed is False
    assert response.previous_ign == "Steve"


async def test_staff_refresh_targets_the_named_account() -> None:
    refresh = IdentityRefresh(account_id=42, java_uuid=JAVA_UUID, current_name="Steve", previous_name="Steve")
    accounts = _accounts(refresh=refresh)
    response = await refresh_minecraft_identity_for.fn(42, accounts, _permission=None, _reservation=None)
    assert accounts.refreshes == [(42, None)]
    assert response.ign == "Steve"
