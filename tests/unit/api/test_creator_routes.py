"""Public creator route contracts."""

from uuid import UUID

import pytest
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import (
    CreditedAlias,
    IdentityProvider,
    ProfileLink,
    PublicCreatorProfile,
    PublicIdentity,
)
from squid.accounts.errors import CreatorNotFoundError
from squid.api.v1.creators import get_creator_profile

CREATOR_ID = UUID("11111111-1111-4111-8111-111111111111")
CANONICAL_ID = UUID("22222222-2222-4222-8222-222222222222")
JOINED = Instant.from_utc(2026, 1, 1)


class AccountReader(AccountService):
    def __init__(self, profile: PublicCreatorProfile | None) -> None:
        self.profile = profile

    async def get_public_profile(self, public_id: UUID) -> PublicCreatorProfile | None:
        return self.profile


def _accounts(profile: PublicCreatorProfile | None) -> AccountService:
    return AccountReader(profile)


async def test_a_visible_profile_serves_everything_it_publishes() -> None:
    profile = PublicCreatorProfile(
        public_id=CREATOR_ID,
        hidden=False,
        aliases=(CreditedAlias("Notch", build_count=3),),
        display_name="Notch",
        bio="I build things",
        pronouns="they/them",
        links=(ProfileLink("Site", "https://example.com"),),
        avatar_url="https://mc-heads.net/avatar/x",
        joined_at=JOINED,
        identities=(PublicIdentity(IdentityProvider.JAVA, "x", "Notch"),),
    )

    response = await get_creator_profile(CREATOR_ID, _accounts(profile))

    assert response.id == CREATOR_ID
    assert response.hidden is False
    assert response.display_name == "Notch"
    assert response.aliases[0].build_count == 3
    assert response.links[0].url == "https://example.com"
    assert response.avatar_url == "https://mc-heads.net/avatar/x"
    assert [identity.provider for identity in response.identities] == [IdentityProvider.JAVA]


async def test_a_hidden_profile_still_serves_its_credits() -> None:
    """Hiding withholds the person, not the attribution."""
    profile = PublicCreatorProfile(
        public_id=CREATOR_ID,
        hidden=True,
        aliases=(CreditedAlias("Notch", build_count=3),),
    )

    response = await get_creator_profile(CREATOR_ID, _accounts(profile))

    assert response.hidden is True
    assert response.aliases[0].name == "Notch"
    assert response.display_name is None
    assert response.bio is None
    assert response.links == []
    assert response.identities == []
    assert response.avatar_url is None
    assert response.joined_at is None


async def test_a_merged_creator_reports_its_canonical_id() -> None:
    profile = PublicCreatorProfile(public_id=CREATOR_ID, hidden=False, canonical_public_id=CANONICAL_ID)

    response = await get_creator_profile(CREATOR_ID, _accounts(profile))

    assert response.id == CREATOR_ID
    assert response.canonical_id == CANONICAL_ID


async def test_an_unknown_creator_is_a_404() -> None:
    with pytest.raises(CreatorNotFoundError):
        await get_creator_profile(CREATOR_ID, _accounts(None))
