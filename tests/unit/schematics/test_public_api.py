"""Public schematic routes are attachment-scoped and publication-safe."""

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest
from whenever import Instant

from squid.api.v1.schematics import get_schematic_content, list_build_schematics, router
from squid.builds.application import BuildQueryService
from squid.builds.domain import Status
from squid.builds.errors import BuildNotFoundError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, offset_page
from squid.schematics.application import SchematicPublication, SchematicService, StoredSchematic
from squid.schematics.application.queries import PublicSchematicDownload
from squid.schematics.domain import SchematicFormat, SchematicLicense, SchematicVisibility
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
    async def get_public(self, build_id: int) -> object:
        if build_id != 7:
            raise BuildNotFoundError(build_id)
        return SimpleNamespace(submission_status=Status.CONFIRMED)


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

    async def list_public_page(
        self,
        build_id: int,
        *,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 50,
    ) -> Page[StoredSchematic]:
        items = [self.stored] if build_id == 7 else []
        return offset_page(items, offset=selector.offset, page_size=page_size)

    async def public_download(self, build_id: int, schematic_id: int) -> PublicSchematicDownload:
        assert (build_id, schematic_id) == (7, 3)
        assert self.stored.publication.license is not None
        return PublicSchematicDownload(
            content=b"sanitized-sponge-v3",
            schematic=self.stored,
            license=self.stored.publication.license,
            source_format=self.stored.analysis.metrics.source_format,
        )


async def test_public_metadata_omits_digest_and_original_filename() -> None:
    page = await list_build_schematics(
        7,
        cast(BuildQueryService, ConfirmedBuilds()),
        cast(SchematicService, PublicSchematics()),
        page_size=50,
        offset=None,
    )

    assert page.total == 1
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
    # The fixture is a .litematic; every download used to be named `.schem` regardless
    # of the container that was actually stored.
    assert response.headers["content-disposition"] == 'attachment; filename="build-7-schematic-3.litematic"'
    assert "x-schematic-license" not in response.headers
    assert response.headers["cache-control"] == "public, max-age=300, must-revalidate"
    # The standard Link header carries the license instead of a bespoke X- header.
    assert 'rel="license"' in response.headers["link"]
    assert "creativecommons.org/licenses/by/4.0/" in response.headers["link"]
    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/schematics/{sha256}/content" not in paths


def test_publication_value_is_not_forgeable_from_an_unsanitized_record() -> None:
    legacy = SchematicPublication()

    assert legacy.is_public_downloadable is False


@pytest.mark.parametrize("source_format", list(SchematicFormat))
async def test_each_stored_format_downloads_under_its_own_extension(source_format: SchematicFormat) -> None:
    """The extension follows the container the analysis recorded, so a `.litematic`
    stops arriving named `.schem`. The stem stays server-generated."""
    schematics = PublicSchematics()
    analysis = schematics.stored.analysis
    schematics.stored = replace(
        schematics.stored,
        analysis=replace(analysis, metrics=replace(analysis.metrics, source_format=source_format)),
    )

    response = await get_schematic_content(
        7,
        3,
        cast(BuildQueryService, ConfirmedBuilds()),
        cast(SchematicService, schematics),
    )

    expected = f'attachment; filename="build-7-schematic-3.{source_format.value}"'
    assert response.headers["content-disposition"] == expected
