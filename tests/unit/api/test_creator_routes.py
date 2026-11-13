"""Public creator route contracts."""

from typing import override
from uuid import UUID

import httpx
import pytest
from litestar import Litestar, get
from litestar.di import Provide
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import (
    CreditedAlias,
    IdentityDetails,
    IdentityProvider,
    ProfileLink,
    PublicCreatorProfile,
)
from squid.accounts.errors import CreatorNotFoundError
from squid.api.openapi import ApplicationValueSchemaPlugin
from squid.api.v1.creators import PublicCreatorDTO, get_creator_profile

CREATOR_ID = UUID("11111111-1111-4111-8111-111111111111")
CANONICAL_ID = UUID("22222222-2222-4222-8222-222222222222")
JOINED = Instant.from_utc(2026, 1, 1)


class AccountReader(AccountService):
    def __init__(self, profile: PublicCreatorProfile | None) -> None:
        self.profile = profile

    @override
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
        identities=(IdentityDetails(IdentityProvider.JAVA, "x", "Notch"),),
    )

    response = await get_creator_profile.fn(CREATOR_ID, _accounts(profile))

    assert response.public_id == CREATOR_ID
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

    response = await get_creator_profile.fn(CREATOR_ID, _accounts(profile))

    assert response.hidden is True
    assert response.aliases[0].name == "Notch"
    assert response.display_name is None
    assert response.bio is None
    assert response.links == ()
    assert response.identities == ()
    assert response.avatar_url is None
    assert response.joined_at is None


async def test_a_merged_creator_reports_its_canonical_id() -> None:
    profile = PublicCreatorProfile(public_id=CREATOR_ID, hidden=False, canonical_public_id=CANONICAL_ID)

    response = await get_creator_profile.fn(CREATOR_ID, _accounts(profile))

    assert response.public_id == CREATOR_ID
    assert response.canonical_public_id == CANONICAL_ID


async def test_an_unknown_creator_is_a_404() -> None:
    with pytest.raises(CreatorNotFoundError):
        await get_creator_profile.fn(CREATOR_ID, _accounts(None))


@pytest.mark.parametrize("hidden", [False, True])
async def test_creator_dto_preserves_public_json_and_excludes_unpublished_properties(hidden: bool) -> None:
    profile = PublicCreatorProfile(
        public_id=CREATOR_ID,
        canonical_public_id=CANONICAL_ID,
        hidden=hidden,
        aliases=(CreditedAlias("Notch", build_count=3),),
        display_name=None if hidden else "Notch",
        links=() if hidden else (ProfileLink("Site", "https://example.com"),),
        joined_at=None if hidden else JOINED,
        identities=() if hidden else (IdentityDetails(IdentityProvider.JAVA, "x", "Notch"),),
    )
    accounts = _accounts(profile)
    handler = get(
        "/creators/{creator_id:uuid}",
        return_dto=PublicCreatorDTO,
        dependencies={"accounts": Provide(lambda: accounts, sync_to_thread=False)},
    )(get_creator_profile.fn)
    app = Litestar([handler], plugins=[ApplicationValueSchemaPlugin()], type_encoders={Instant: Instant.format_iso})
    # HTTPX and Litestar describe compatible ASGI interfaces with different scope types.
    transport = httpx.ASGITransport(app=app)  # type: ignore[bad-argument-type]
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get(f"/creators/{CREATOR_ID}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(CREATOR_ID),
        "canonical_id": str(CANONICAL_ID),
        "hidden": hidden,
        "aliases": [{"name": "Notch", "build_count": 3}],
        "display_name": None if hidden else "Notch",
        "bio": None,
        "pronouns": None,
        "links": [] if hidden else [{"label": "Site", "url": "https://example.com"}],
        "avatar_url": None,
        "joined_at": None if hidden else "2026-01-01T00:00:00Z",
        "identities": [] if hidden else [{"provider": "java", "subject": "x", "display_name": "Notch"}],
    }
    schemas = app.openapi_schema.to_schema()["components"]["schemas"]
    schema = next(value for value in schemas.values() if "canonical_id" in value.get("properties", {}))
    assert set(schema["properties"]) == set(response.json())
    assert {"type": "string", "format": "date-time"} in schema["properties"]["joined_at"]["oneOf"]

    assert set(schemas["CreditedAlias"]["properties"]) == {"name", "build_count"}
    assert set(schemas["ProfileLink"]["properties"]) == {"label", "url"}
    assert set(schemas["IdentityDetails"]["properties"]) == {"provider", "subject", "display_name"}
