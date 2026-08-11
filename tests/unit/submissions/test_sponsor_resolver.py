"""Paper sponsor resolution exposes only explicit public opt-ins."""

from typing import Any, cast
from uuid import UUID

import pytest
from whenever import Instant

from squid.config import MinecraftAuthConfig
from squid.minecraft_auth.domain import PublicServerProfile, PublishedPaperServer
from squid.submissions.infrastructure.sponsors import PaperSponsorResolver

INSTALLATION_ID = UUID("00000000-0000-4000-8000-000000000701")
NOW = Instant.parse_iso("2026-08-11T20:00:00Z")


class FakeInstallations:
    def __init__(self, *servers: PublishedPaperServer) -> None:
        self.servers = {server.installation_id: server for server in servers}

    async def get_public_server(self, installation_id: UUID) -> PublishedPaperServer | None:
        return self.servers.get(installation_id)


def test_sponsor_attribution_activation_is_default_off_and_requires_minecraft_auth() -> None:
    assert MinecraftAuthConfig().sponsor_attribution_enabled is False
    with pytest.raises(ValueError, match="requires the Minecraft authorization flow"):
        MinecraftAuthConfig(sponsor_attribution_enabled=True)

    configured = MinecraftAuthConfig(
        pepper="p" * 32,
        verification_uri="https://example.test/minecraft/link",
        sponsor_attribution_enabled=True,
    )

    assert configured.sponsor_attribution_enabled is True


@pytest.mark.asyncio
async def test_resolver_projects_only_sponsor_opted_in_public_fields() -> None:
    server = PublishedPaperServer(
        INSTALLATION_ID,
        PublicServerProfile(
            enabled=True,
            display_name="Example server",
            address="play.example.test",
            description="Public description",
            website_url="https://example.test/server",
            sponsor_opt_in=True,
        ),
        NOW,
    )
    resolver = PaperSponsorResolver(cast(Any, FakeInstallations(server)))

    sponsor = await resolver.resolve(INSTALLATION_ID)

    assert sponsor is not None
    assert sponsor.installation_id == INSTALLATION_ID
    assert sponsor.display_name == "Example server"
    assert not hasattr(sponsor, "owner_account_id")
    assert not hasattr(sponsor, "secret_hash")


@pytest.mark.asyncio
async def test_resolver_fails_closed_without_sponsor_opt_in() -> None:
    server = PublishedPaperServer(
        INSTALLATION_ID,
        PublicServerProfile(enabled=True, display_name="Example server"),
        NOW,
    )
    resolver = PaperSponsorResolver(cast(Any, FakeInstallations(server)))

    assert await resolver.resolve(INSTALLATION_ID) is None
    assert await resolver.resolve(UUID("00000000-0000-4000-8000-000000000702")) is None


@pytest.mark.asyncio
async def test_resolver_fails_closed_for_an_unsafe_public_website() -> None:
    server = PublishedPaperServer(
        INSTALLATION_ID,
        PublicServerProfile(
            enabled=True,
            website_url="https://example.test:bad-port",
            sponsor_opt_in=True,
        ),
        NOW,
    )
    resolver = PaperSponsorResolver(cast(Any, FakeInstallations(server)))

    assert await resolver.resolve(INSTALLATION_ID) is None
