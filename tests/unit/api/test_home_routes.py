"""Catalogue counts and the public server directory over HTTP."""

from typing import override
from uuid import UUID

from litestar.testing import TestClient
from whenever import Instant

from squid.core.actors import ActingContext
from squid.core.catalogue.application import CatalogueStats
from squid.core.minecraft_auth.domain.models import PublicServerProfile, PublishedPaperServer
from tests.unit.api.fakes import MockCatalogueStats, MockMinecraftInstallations, build_app

SERVER_ID = UUID("6f1c2a6e-9a51-4c43-9d3b-3e3c5b7f9a10")


class CountedCatalogue(MockCatalogueStats):
    @override
    async def stats(self, *, context: ActingContext) -> CatalogueStats:
        return CatalogueStats(builds=120, records=45, creators=38)


class ListedServers(MockMinecraftInstallations):
    @override
    async def public_servers(self, *, context: ActingContext) -> tuple[PublishedPaperServer, ...]:
        profile = PublicServerProfile(
            enabled=True, display_name="Redstone Hub", address="play.example.net", sponsor_opt_in=True
        )
        return (PublishedPaperServer(SERVER_ID, profile, Instant.from_utc(2026, 3, 1)),)


def test_stats_are_public_and_cacheable() -> None:
    app, _ = build_app(catalogue_stats=CountedCatalogue())
    with TestClient(app) as http:
        response = http.get("/v1/stats")

    assert response.status_code == 200
    assert response.json() == {"builds": 120, "records": 45, "creators": 38}
    assert response.headers["Cache-Control"] == "public, max-age=300"


def test_servers_list_public_profiles() -> None:
    app, _ = build_app(minecraft_installations=ListedServers())
    with TestClient(app) as http:
        response = http.get("/v1/minecraft/servers")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"] == [
        {
            "installation_id": str(SERVER_ID),
            "display_name": "Redstone Hub",
            "address": "play.example.net",
            "description": None,
            "website_url": None,
            "sponsor": True,
            "listed_since": "2026-03-01T00:00:00Z",
        }
    ]
