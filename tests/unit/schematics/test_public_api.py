"""Public schematic routes are attachment-scoped and publication-safe."""

from types import SimpleNamespace
from typing import cast

from whenever import Instant

from squid.api.v1.schematics import get_schematic_content, list_build_schematics, router
from squid.builds.application import BuildQueryService
from squid.builds.domain import Status
from squid.schematics.application import SchematicPublication, SchematicService, StoredSchematic
from squid.schematics.domain import SchematicLicense, SchematicVisibility
from tests.unit.schematics.fakes import make_analysis


def _public_publication() -> SchematicPublication:
    now = Instant.parse_iso("2026-08-11T12:00:00Z")
    return SchematicPublication(
        visibility=SchematicVisibility.PUBLIC_DOWNLOAD,
        license=SchematicLicense.CC_BY_4_0,
        rights_attested_at=now,
        rights_attested_by_account_id=7,
        sanitized_at=now,
        sanitizer_version="nucleation-test",
        sanitization_report={"removed": 0},
        published_at=now,
    )


class ConfirmedBuilds:
    async def get(self, build_id: int) -> object | None:
        return SimpleNamespace(submission_status=Status.CONFIRMED) if build_id == 7 else None


class PublicSchematics:
    def __init__(self) -> None:
        self.stored = StoredSchematic(
            id=3,
            build_id=7,
            file_sha256="a" * 64,
            is_primary=True,
            original_filename="private-player-name.litematic",
            analysis=make_analysis(),
            publication=_public_publication(),
        )

    async def list_public_for_build(self, build_id: int) -> list[StoredSchematic]:
        return [self.stored] if build_id == 7 else []

    async def public_content(self, build_id: int, schematic_id: int) -> tuple[bytes, StoredSchematic]:
        assert (build_id, schematic_id) == (7, 3)
        return b"sanitized-sponge-v3", self.stored


async def test_public_metadata_omits_digest_and_original_filename() -> None:
    page = await list_build_schematics(
        7,
        cast(BuildQueryService, ConfirmedBuilds()),
        cast(SchematicService, PublicSchematics()),
        page_size=50,
        offset=None,
    )

    item = page.items[0].model_dump(mode="json")
    assert "sha256" not in item
    assert "filename" not in item
    assert item["license"] == "cc_by_4_0"
    assert item["download_url"] == "/v1/builds/7/schematics/3/content"


async def test_download_uses_scoped_locator_and_short_revalidation_cache() -> None:
    response = await get_schematic_content(
        7,
        3,
        cast(BuildQueryService, ConfirmedBuilds()),
        cast(SchematicService, PublicSchematics()),
    )

    assert response.body == b"sanitized-sponge-v3"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="build-7-schematic-3.schem"'
    assert response.headers["x-schematic-license"] == "cc_by_4_0"
    assert response.headers["cache-control"] == "public, max-age=300, must-revalidate"
    assert "creativecommons.org/licenses/by/4.0/" in response.headers["link"]
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/schematics/{sha256}/content" not in paths


def test_publication_value_is_not_forgeable_from_an_unsanitized_record() -> None:
    legacy = SchematicPublication()

    assert legacy.is_public_downloadable is False
